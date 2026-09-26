"""One-shot loopback receiver used by real browser E2E tests only."""

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import sys
import time

STATE = Path("/tmp/emergency-platform-e2e-webhook")
SECRET = STATE / "secret"
READY = STATE / "ready"
RESULT = STATE / "result.json"
PID = STATE / "pid"


def prepare() -> None:
    STATE.mkdir(mode=0o700, exist_ok=True)
    for path in (READY, RESULT, PID, SECRET):
        path.unlink(missing_ok=True)
    value = sys.stdin.buffer.read()
    if not value or len(value) > 512:
        raise SystemExit("expected a webhook signing secret on stdin")
    SECRET.write_bytes(value)
    SECRET.chmod(0o600)


def serve(port: int) -> None:
    if not SECRET.is_file():
        raise SystemExit("receiver secret was not prepared")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_POST(self):
            if self.path != "/events":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            timestamp = self.headers.get("X-Emergency-Timestamp", "")
            delivery_id = self.headers.get("X-Emergency-Delivery", "")
            try:
                payload = json.loads(body)
                expected = "sha256=" + hmac.new(
                    SECRET.read_bytes(),
                    timestamp.encode() + b"." + body,
                    hashlib.sha256,
                ).hexdigest()
                valid = (
                    bool(delivery_id)
                    and hmac.compare_digest(
                        expected, self.headers.get("X-Emergency-Signature", "")
                    )
                    and self.headers.get("X-Emergency-Event") == payload.get("type")
                )
                result = {
                    "signature_valid": valid,
                    "delivery_id": delivery_id,
                    "event_type": payload.get("type"),
                    "event_id": payload.get("id"),
                }
            except (ValueError, TypeError):
                result = {
                    "signature_valid": False,
                    "delivery_id": delivery_id,
                    "event_type": None,
                    "event_id": None,
                }
                valid = False
            temporary = RESULT.with_suffix(".tmp")
            temporary.write_text(json.dumps(result, separators=(",", ":")))
            temporary.chmod(0o600)
            temporary.replace(RESULT)
            self.send_response(204 if valid else 400)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 75
    PID.write_text(str(os.getpid()))
    READY.write_text(str(time.time()))
    READY.chmod(0o600)
    server.handle_request()
    server.server_close()
    PID.unlink(missing_ok=True)


def show_result() -> None:
    if not RESULT.is_file():
        raise SystemExit("receiver has not accepted a webhook")
    print(RESULT.read_text())


def cleanup() -> None:
    if PID.is_file():
        try:
            os.kill(int(PID.read_text()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
    for path in (READY, RESULT, PID, SECRET):
        path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "serve", "result", "cleanup"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "serve":
        serve(args.port)
    elif args.action == "result":
        show_result()
    else:
        cleanup()


if __name__ == "__main__":
    main()
