"""Shared-key authentication and encrypted JSON envelopes for ADB Hub.

The project intentionally avoids extra dependencies. This module provides a
small authenticated stream-encryption envelope built from HMAC-SHA256. If this
service is exposed beyond a trusted LAN, prefer terminating TLS and stronger
crypto at a reverse proxy as well.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from config import ADB_HUB_AUTH_SECRET

TOKEN_PLAINTEXT = (
    "adb-hub-static-token-v1:"
    "yH3s6ZcQe9N0pVk2W8rD4mTb7LxFa1UjG5qPwS0nRcEiKzAoY6MdBhXl93TfQvJ"
)
ENVELOPE_VERSION = "adb-hub-enc-v1"


class SecurityError(Exception):
    """Raised when authentication or encrypted payload handling fails."""


def _require_secret(secret: str | None = None) -> bytes:
    value = secret if secret is not None else ADB_HUB_AUTH_SECRET
    if not value:
        raise SecurityError("ADB_HUB_AUTH_SECRET is not configured")
    return value.encode("utf-8")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode((data + padding).encode("ascii"))
    except Exception as exc:
        raise SecurityError("invalid base64 field") from exc


def _derive_key(secret: bytes) -> bytes:
    return hashlib.sha256(b"adb-hub-secret-v1\0" + secret).digest()


def _keystream(key: bytes, nonce: bytes, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        counter_bytes = counter.to_bytes(8, "big")
        out.extend(hmac.new(key, b"stream\0" + nonce + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return bytes(out[:size])


def _tag(key: bytes, aad: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(key, b"tag\0" + aad + nonce + ciphertext, hashlib.sha256).digest()


def encrypt_bytes(plaintext: bytes, aad: bytes = b"payload", secret: str | None = None) -> dict[str, str]:
    key = _derive_key(_require_secret(secret))
    nonce = secrets.token_bytes(16)
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    return {
        "v": ENVELOPE_VERSION,
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
        "tag": _b64e(_tag(key, aad, nonce, ciphertext)),
    }


def decrypt_envelope(envelope: dict[str, Any] | str, aad: bytes = b"payload", secret: str | None = None) -> bytes:
    if isinstance(envelope, str):
        parts = envelope.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            raise SecurityError("invalid compact encrypted token")
        envelope = {
            "v": ENVELOPE_VERSION,
            "nonce": parts[1],
            "ciphertext": parts[2],
            "tag": parts[3],
        }
    if not isinstance(envelope, dict) or envelope.get("v") != ENVELOPE_VERSION:
        raise SecurityError("invalid encrypted envelope version")
    nonce = _b64d(str(envelope.get("nonce", "")))
    ciphertext = _b64d(str(envelope.get("ciphertext", "")))
    sent_tag = _b64d(str(envelope.get("tag", "")))
    key = _derive_key(_require_secret(secret))
    expected = _tag(key, aad, nonce, ciphertext)
    if not hmac.compare_digest(sent_tag, expected):
        raise SecurityError("encrypted envelope authentication failed")
    stream = _keystream(key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream))


def encrypt_token(secret: str | None = None) -> str:
    envelope = encrypt_bytes(TOKEN_PLAINTEXT.encode("utf-8"), aad=b"token", secret=secret)
    return "v1.{nonce}.{ciphertext}.{tag}".format(**envelope)


def verify_encrypted_token(token: str) -> None:
    if not token:
        raise SecurityError("missing X-ADB-Hub-Token")
    plaintext = decrypt_envelope(token, aad=b"token").decode("utf-8", errors="strict")
    if not hmac.compare_digest(plaintext, TOKEN_PLAINTEXT):
        raise SecurityError("invalid token plaintext")


def encrypt_json_payload(data: Any, secret: str | None = None) -> dict[str, str]:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encrypt_bytes(raw, aad=b"json-payload", secret=secret)


def decrypt_json_payload(envelope: dict[str, Any]) -> Any:
    raw = decrypt_envelope(envelope, aad=b"json-payload")
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise SecurityError("decrypted payload is not valid JSON") from exc


if __name__ == "__main__":
    print(json.dumps({"token": encrypt_token()}, ensure_ascii=False))
