"""Webhook signature verification, matching packages/webhooks."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Optional, Sequence, Union

Header = Union[str, Sequence[str], None]


class WebhookSignatureError(Exception):
    def __init__(self, message: str = "invalid webhook signature") -> None:
        super().__init__(message)


def _parse(header: Header) -> Optional[tuple[int, list[str]]]:
    text = header if isinstance(header, str) else ",".join(header) if isinstance(header, (list, tuple)) else None
    if text is None:
        return None
    timestamp: Optional[int] = None
    signatures: list[str] = []
    for part in text.split(","):
        key, sep, value = part.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "t" and re.fullmatch(r"[0-9]+", value):
            timestamp = int(value)
        elif key == "v1":
            signatures.append(value)
    return None if timestamp is None else (timestamp, signatures)


def verify_signature(header: Header, secret: str, body: Union[str, bytes], *, now: Optional[int] = None, tolerance: int = 300) -> bool:
    """True when `header` signs the raw `body` with `secret` recently enough. False, never an exception, on bad input."""
    parsed = _parse(header)
    if parsed is None:
        return False
    timestamp, signatures = parsed
    if abs((int(time.time()) if now is None else now) - timestamp) > tolerance:
        return False
    raw = body.encode("utf-8") if isinstance(body, str) else body
    expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + raw, hashlib.sha256).digest()
    for signature in signatures:
        try:
            if hmac.compare_digest(bytes.fromhex(signature), expected):
                return True
        except ValueError:
            continue
    return False


def construct_event(body: Union[str, bytes], header: Header, secret: str, *, now: Optional[int] = None, tolerance: int = 300) -> Any:
    """Verifies a delivery and returns the parsed event. Raises WebhookSignatureError otherwise. Pass the raw request body."""
    if not verify_signature(header, secret, body, now=now, tolerance=tolerance):
        raise WebhookSignatureError()
    return json.loads(body)
