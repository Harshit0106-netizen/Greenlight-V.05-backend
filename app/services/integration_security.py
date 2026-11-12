"""API keys, webhook signatures and webhook URL safety. Pure helpers; the database work lives in integration_service."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from urllib.parse import urlparse

KEY_PREFIX = "glk_"
SCOPES = ("reviews:write", "reviews:read")
EVENTS = ("review.in_review", "review.approved", "review.changes_requested", "review.escalated", "ping")


def generate_api_key() -> tuple[str, str, str]:
    """(plaintext, display prefix, sha256 hash). The plaintext is shown once and never stored."""
    plain = KEY_PREFIX + secrets.token_urlsafe(32)
    return plain, plain[:12], hash_key(plain)


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()  # keys are long random strings, so no salt is needed


def generate_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def check_webhook_url(url: str, allow_private: bool, resolver=socket.getaddrinfo) -> str | None:
    """None when the URL is acceptable, otherwise the reason. Blocks private, loopback and link-local targets
    outside development so a webhook cannot be pointed at internal services."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "The webhook URL must start with https:// and include a host."
    if p.scheme == "http" and not allow_private:
        return "The webhook URL must use https."
    if p.username or p.password:
        return "The webhook URL must not contain a username or password."
    if allow_private:
        return None
    try:
        infos = resolver(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return "Could not resolve the webhook host."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "The webhook URL points to a private or internal address."
    return None
