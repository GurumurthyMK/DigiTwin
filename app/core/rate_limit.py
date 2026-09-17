"""In-process fixed-window rate limiter for sensitive auth endpoints.

Dependency-free by design: Phase 1 runs a single API process, so a local
counter is sufficient and avoids Redis as a dev/prod dependency.
Multi-worker or multi-replica deploys MUST move to shared storage —
the limiter is isolated here so that swap is a one-file change.
"""

import time
from collections import defaultdict

from fastapi.responses import JSONResponse

from app.core.errors import error_body

_MAX_KEYS = 4096


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def allowed(self, key: tuple[str, str]) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        if len(self._hits) > _MAX_KEYS:
            self._hits = defaultdict(
                list, {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            )
        hits = [t for t in self._hits[key] if t > cutoff]
        self._hits[key] = hits
        if len(hits) >= self.max_requests:
            return False, int(hits[0] + self.window_seconds - now) + 1
        hits.append(now)
        return True, 0

    def reset(self) -> None:
        self._hits.clear()


auth_limiter = RateLimiter()


def rate_limited_response(retry_after: int) -> JSONResponse:
    return JSONResponse(
        content=error_body("rate_limited", "Too many attempts, try again shortly."),
        status_code=429,
        headers={"Retry-After": str(max(retry_after, 1))},
    )
