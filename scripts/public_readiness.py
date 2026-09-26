"""Bounded privacy, link and secret checks for the public portfolio repository."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s]+|[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+")
EMAIL = re.compile(r"\b[A-Za-z0-9_.+\-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
PUBLIC_EMAIL = re.compile(r"(?:.*\.)?example\.(?:com|org|net|test)|(?:.*\.)?noreply\.github\.com")
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s]+?)(?:\s+"[^"\n]*")?\s*\)')
REFERENCE = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
DEFINITION = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+)", re.M)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, timeout=30,
    ).decode("utf-8")


def private_email(address: str) -> bool:
    return (address.lower() != "noreply@github.com"
            and not PUBLIC_EMAIL.fullmatch(address.rsplit("@", 1)[-1].lower()))


def forbidden_file(name: str) -> bool:
    path = Path(name)
    return (
        (path.name.startswith(".env") and path.name != ".env.example")
        or bool(re.search(r"\.(?:tfstate(?:\..*)?|tfplan|tfvars(?:\.json)?)$", name))
        or path.name in {"backend.hcl", "aws-deployment.json", "dump.rdb", "id_rsa", "id_ed25519"}
        or path.suffix in {".key", ".p12", ".pfx", ".dump", ".backup"}
        or any(part in {".aws", ".ssh", ".local", "node_modules", ".venv"} for part in path.parts)
    )


def privacy_issues(name: str, text: str) -> list[str]:
    issues = []
    for number, line in enumerate(text.splitlines(), 1):
        if HOME_PATH.search(line):
            issues.append(f"{name}:{number}: machine-specific home path")
        if any(private_email(match[0]) for match in EMAIL.finditer(line)):
            issues.append(f"{name}:{number}: non-example email needs review")
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----", line):
            issues.append(f"{name}:{number}: private-key material")
    return issues


def without_code(text: str) -> str:
    """Preserve line numbers while excluding fenced/indented code and comments."""
    output, fence = [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            output.append("\n")
        else:
            output.append("\n" if fence or line.startswith(("    ", "\t")) else line)
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m[0].count("\n"), "".join(output), flags=re.S)


def anchors(text: str) -> set[str]:
    cleaned = without_code(text)
    result = set(re.findall(r'<a\s+(?:name|id)=["\']([^"\']+)["\']', cleaned))
    used = set()
    lines = cleaned.splitlines()
    for index, line in enumerate(lines):
        heading = re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        title = heading[1] if heading else None
        if index + 1 < len(lines) and re.fullmatch(r" {0,3}(?:=+|-+)\s*", lines[index + 1]) and line.strip():
            title = line.strip()
        if title is None:
            continue
        title = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", title)
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).lower()
        slug = "".join(c for c in title if c in "-_ " or not unicodedata.category(c).startswith(("P", "S")))
        slug = slug.replace(" ", "-")
        base, suffix = slug, 0
        while slug in used:
            suffix += 1
            slug = f"{base}-{suffix}"
        used.add(slug)
        result.add(slug)
    return result


def markdown_issues(root: Path, files: set[str], texts: dict[str, str]) -> tuple[list[str], int]:
    issues, checked = [], 0
    for name, text in texts.items():
        if not name.lower().endswith(".md"):
            continue
        cleaned = re.sub(r"(`+).*?\1", "", without_code(text))
        definitions = {m[1].casefold(): m[2] for m in DEFINITION.finditer(cleaned)}
        targets = [(m.start(), m[1]) for m in LINK.finditer(cleaned)]
        targets += [(m.start(), m[2]) for m in DEFINITION.finditer(cleaned)]
        for match in REFERENCE.finditer(cleaned):
            key = (match[2] or match[1]).casefold()
            if key not in definitions:
                issues.append(f"{name}:{cleaned[:match.start()].count(chr(10)) + 1}: undefined link reference")
        # Reference definitions cover full, collapsed and shortcut reference links.
        targets += [(m.start(), m[1]) for m in re.finditer(r'<(?:a|img)\b[^>]*(?:href|src)=["\']([^"\']+)["\']', cleaned)]
        for position, target in targets:
            location = f"{name}:{cleaned[:position].count(chr(10)) + 1}"
            url = urlsplit(html.unescape(target.strip("<>")))
            if url.scheme in {"https", "http", "mailto"}:
                continue  # External availability/access is a separate manual review.
            if url.scheme or url.netloc or url.path.startswith(("/", "\\", "~")):
                issues.append(f"{location}: non-portable link")
                continue
            path = (root / name).parent / unquote(url.path) if url.path else root / name
            try:
                relative = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                issues.append(f"{location}: link escapes repository")
                continue
            checked += 1
            if relative not in files and not any(p.startswith(relative.rstrip("/") + "/") for p in files):
                issues.append(f"{location}: link target is not tracked")
            elif url.fragment and relative.lower().endswith(".md"):
                if unquote(url.fragment) not in anchors(texts.get(relative, "")):
                    issues.append(f"{location}: missing Markdown heading anchor")
    return issues, checked


def png_issues(name: str, data: bytes) -> list[str]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return [f"{name}: invalid PNG"]
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        offset += length + 12
        if offset > len(data):
            break
        if kind in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            return [f"{name}: image metadata needs privacy review"]
        if kind == b"IEND":
            return [] if offset == len(data) else [f"{name}: trailing image data needs review"]
    return [f"{name}: truncated PNG"]


def scan_secrets(binary: str, mode: str, source: Path, root: Path, scratch: Path) -> int:
    """Never emit scanner logs/matches, including on errors; reports are temporary."""
    report = scratch / f"{mode}.json"
    ignore = scratch / ".gitleaksignore"
    ignore.touch()
    command = [binary, mode, str(source), "--config", str(root / ".gitleaks.toml"),
               "--gitleaks-ignore-path", str(ignore), "--ignore-gitleaks-allow",
               "--redact=100", "--no-banner", "--no-color", "--timeout=180",
               "--report-format=json", "--report-path", str(report)]
    if mode == "git":
        command.append("--log-opts=--all --full-history")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS_")}
    result = subprocess.run(command, cwd=scratch, env=env, capture_output=True, timeout=195)
    try:
        findings = json.loads(report.read_text())
    except (OSError, ValueError):
        raise RuntimeError(f"{mode} secret scanner did not produce a valid report") from None
    if (not isinstance(findings, list) or result.returncode not in {0, 1}
            or bool(findings) != bool(result.returncode)):
        raise RuntimeError(f"{mode} secret scanner failed or returned inconsistent results")
    # Only counts leave the private temporary directory. Investigate findings locally
    # with redaction; never upload raw logs or matches to a PR or public artifact.
    print(f"{'BLOCKED' if findings else 'PASS'}: {mode} secrets ({len(findings)} findings)")
    return len(findings)


def check(root: Path, binary: str) -> int:
    files = set(filter(None, git(root, "ls-files", "-z").split("\0")))
    if not files:
        raise RuntimeError("No tracked files to audit")
    issues, texts = [], {}
    with tempfile.TemporaryDirectory(prefix="emergency-public-readiness-") as directory:
        scratch = Path(directory)  # mkdtemp creates a private 0700 directory.
        tree = scratch / "tree"
        tree.mkdir()
        for name in sorted(files):
            path = root / name
            if path.is_symlink() or not path.is_file():
                issues.append(f"{name}: missing file, symlink or submodule needs review")
                continue
            data = path.read_bytes()
            destination = tree / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            if forbidden_file(name):
                issues.append(f"{name}: private runtime/configuration file is tracked")
            if name.endswith(".png"):
                issues.extend(png_issues(name, data))
            try:
                texts[name] = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            issues.extend(privacy_issues(name, texts[name]))
        link_issues, count = markdown_issues(root, files, texts)
        issues.extend(link_issues)
        print(f"{'BLOCKED' if link_issues else 'PASS'}: Markdown ({count} local links)")
        for issue in issues:
            print(f"BLOCKED: {issue}")
        print(f"{'BLOCKED' if issues else 'PASS'}: tracked-file privacy ({len(files)} files)")
        if git(root, "rev-parse", "--is-shallow-repository").strip() != "false":
            raise RuntimeError("Full local history required; a shallow scan cannot pass")
        findings = scan_secrets(binary, "dir", tree, root, scratch)
        findings += scan_secrets(binary, "git", root, root, scratch)
    emails = git(root, "log", "--all", "--format=%ae%n%ce").splitlines()
    if any(private_email(email) for email in emails if email):
        print("BLOCKED: non-public email in Git metadata")
        issues.append("Git metadata privacy")
    if not any(Path(name).name.upper().startswith(("LICENSE", "LICENCE", "COPYING")) for name in files):
        print("LICENSE_STATUS=NONE: no project license selected")
    print("CHECK_SCOPE=local tracked files and all local Git refs; see docs/public-readiness.md")
    return 1 if issues or findings else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", default="gitleaks")
    args = parser.parse_args()
    binary = shutil.which(args.gitleaks)
    if not binary:
        print("BLOCKED: Gitleaks is required (brew install gitleaks); no scan was skipped")
        return 2
    try:
        return check(ROOT, binary)
    except RuntimeError as error:
        print(f"BLOCKED: {error}")  # Only our own fixed diagnostic messages.
        return 2
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Generic error output avoids leaking command output, local paths or keys.
        print(f"BLOCKED: readiness check failed ({type(error).__name__}); no clean result")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
