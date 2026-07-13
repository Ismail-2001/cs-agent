"""
Simple in-memory sliding-window rate limiter, keyed by client IP.

Deliberately dependency-free — good enough for a single-instance deploy (which is what
render.yaml describes). If you scale to multiple instances behind a load balancer, move
this to Redis (INCR + EXPIRE) so limits are shared across instances instead of per-process.
"""

import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request

from agent.config import settings

# ip -> deque of request timestamps within the current window
_request_log: Dict[str, Deque[float]] = defaultdict(deque)


def _check_rate_limit(client_ip: str, limit_per_minute: int) -> None:
    now = time.monotonic()
    window_start = now - 60.0
    log = _request_log[client_ip]

    while log and log[0] < window_start:
        log.popleft()

    if len(log) >= limit_per_minute:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit_per_minute}/minute). Try again shortly.",
        )
    log.append(now)


def _client_ip(request: Request) -> str:
    # Respect a reverse proxy's forwarded header (Render sits behind one) if present,
    # otherwise fall back to the direct connection IP.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_default(request: Request) -> None:
    _check_rate_limit(_client_ip(request), settings.RATE_LIMIT_PER_MINUTE)


async def rate_limit_refund(request: Request) -> None:
    _check_rate_limit(_client_ip(request), settings.REFUND_RATE_LIMIT_PER_MINUTE)


async def rate_limit_resend(request: Request) -> None:
    _check_rate_limit(_client_ip(request), settings.RESEND_RATE_LIMIT_PER_MINUTE)
