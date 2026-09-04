"""In-memory per-IP sliding window. Fine for a single-process MVP."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import settings

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check(request: Request, bucket: str, limit: int) -> None:
    key = (_client_ip(request), bucket)
    now = time.time()
    window = _hits[key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    window.append(now)


def limit_chat(request: Request) -> None:
    check(request, "chat", settings.rate_limit_chat_per_min)


def limit_vin(request: Request) -> None:
    check(request, "vin", settings.rate_limit_vin_per_min)
