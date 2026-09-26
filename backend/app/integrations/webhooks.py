"""Secret-safe signing and DNS-pinned outbound HTTP boundary."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import time
from urllib.parse import urlsplit
import aiohttp
from cryptography.fernet import Fernet

EVENT_TYPES = (
    "incident.created",
    "incident.updated",
    "incident.resolved",
    "unit.dispatched",
    "unit.arrived",
    "dispatch.completed",
)


class UnsafeURL(ValueError):
    pass


def cipher():
    try:
        return Fernet(os.environ.get("WEBHOOK_ENCRYPTION_KEY", "").encode())
    except (ValueError, TypeError):
        raise ValueError("Webhook encryption is not configured.") from None


def canonical(payload):
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def signed_headers(secret, payload, delivery_id, timestamp=None):
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + canonical(payload), hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Emergency-Event": payload["type"],
        "X-Emergency-Delivery": delivery_id,
        "X-Emergency-Timestamp": timestamp,
        "X-Emergency-Signature": "sha256=" + signature,
    }


async def validate_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
            or len(url) > 2048
            or any(ord(c) < 33 for c in url)
        ):
            raise UnsafeURL("Invalid webhook URL.")
        origin = f"{parts.scheme}://{parts.netloc}"
        allowed = os.environ.get("WEBHOOK_ALLOWED_LOOPBACK_ORIGINS", "").split(",")
        local = (
            os.environ.get("APP_ENV", "production") in {"development", "test"}
            and origin in allowed
        )
        if parts.scheme != "https" and not local:
            raise UnsafeURL("HTTPS is required.")
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                parts.hostname, port, type=socket.SOCK_STREAM
            ),
            timeout=3,
        )
        checked = []
        for family, _, _, _, address in addresses:
            ip = ipaddress.ip_address(address[0])
            ip = getattr(ip, "ipv4_mapped", None) or ip
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ) and not (local and ip.is_loopback):
                raise UnsafeURL("Webhook destination is not a public address.")
            checked.append(
                {
                    "hostname": parts.hostname,
                    "host": address[0],
                    "port": port,
                    "family": family,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not checked:
            raise UnsafeURL("Webhook destination did not resolve.")
        return checked
    except ValueError as exc:
        if isinstance(exc, UnsafeURL):
            raise
        raise UnsafeURL("Invalid webhook URL.") from None


class PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, addresses):
        self.addresses = addresses

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return self.addresses

    async def close(self):
        pass


async def post_webhook(url, payload, headers):
    addresses = await validate_url(url)
    connector = aiohttp.TCPConnector(
        resolver=PinnedResolver(addresses), use_dns_cache=False
    )
    async with aiohttp.ClientSession(
        connector=connector,
        trust_env=False,
        timeout=aiohttp.ClientTimeout(total=5, connect=3),
    ) as client:
        async with client.post(
            url, data=canonical(payload), headers=headers, allow_redirects=False
        ) as response:
            # Receiver bodies may be huge or sensitive; neither read nor persist them.
            return response.status
