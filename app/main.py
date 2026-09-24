"""FastAPI entrypoint: middleware, versioned router, error handlers."""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import v1
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import auth_limiter, rate_limited_response
from app.db import models  # noqa: F401  (register models)
from app.db.base import Base
from app.db.session import engine

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger(__name__)

if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
    log.info("error tracking enabled (sentry)")
else:
    log.info("error tracking disabled: set SENTRY_DSN to enable")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().jwt_secret_key == "change-me-in-production-min-32-chars":
        log.warning("JWT_SECRET_KEY is the insecure default — set it before any shared deploy.")
    elif len(get_settings().jwt_secret_key) < 32:
        log.warning("JWT_SECRET_KEY is shorter than 32 chars — use a long random value.")
    # Phase 1A: auto-create tables for SQLite dev; Alembic used for explicit migrations.
    Base.metadata.create_all(bind=engine)
    try:
        from app.db.seed import seed_reference_data

        seed_reference_data()
    except Exception as e:  # noqa: BLE001 — boot must survive seed failures; logged above
        log.warning("reference seed skipped: %s", e)
    from app.db.session import SessionLocal
    from app.services.auth_service import purge_expired_tokens

    db = SessionLocal()
    try:
        log.info("purged %d expired refresh tokens", purge_expired_tokens(db))
    finally:
        db.close()
    yield


app = FastAPI(title=settings.app_name, version="1a", lifespan=lifespan)
register_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Abuse shield for cheap-to-hit POST endpoints (fixed window per IP).
# Credential + token endpoints (brute force) plus the Mentor message endpoint
# (external-LLM cost when a provider is configured; cheap fallback otherwise).
# Entries are full post-prefix path suffixes (see _auth_rate_limit below).
_AUTH_SENSITIVE = {
    "/auth/register",
    "/auth/login",
    "/auth/refresh",
    "/profiles/me/mentor/message",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    # API serves JSON only: lock down framing/object sources for renderers.
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.cookie_secure:
        # Only meaningful over HTTPS; sending it on localhost http is ignored.
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def _request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response.headers["x-request-id"] = rid
    log.info(
        "%s %s -> %s (%dms) rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        rid,
    )
    return response


@app.middleware("http")
async def _auth_rate_limit(request, call_next):
    path = request.url.path
    prefix = settings.api_v1_prefix
    suffix = path.removeprefix(prefix)
    if settings.rate_limit_enabled and request.method == "POST" and suffix in _AUTH_SENSITIVE:
        auth_limiter.max_requests = settings.rate_limit_auth_per_minute
        host = request.client.host if request.client else "unknown"
        ok, retry_after = auth_limiter.allowed((host, suffix))
        if not ok:
            return rate_limited_response(retry_after)
    return await call_next(request)


app.include_router(v1, prefix=settings.api_v1_prefix)
