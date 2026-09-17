"""Debt-cleanup regression tests: rate limits, cookie transport + CSRF,
refresh-reuse detection, token purge, FK enforcement.

Uses a SEPARATE TestClient so cookie-jar state cannot leak into the
Phase 1A suite (which asserts unauthenticated 401s on a shared client).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.rate_limit import auth_limiter
from app.core.security import hash_refresh_token
from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services.auth_service import purge_expired_tokens

tclient = TestClient(app)


def test_auth_rate_limit_returns_429_shape():
    settings = get_settings()
    old_limit, settings.rate_limit_auth_per_minute = settings.rate_limit_auth_per_minute, 3
    old_enabled, settings.rate_limit_enabled = settings.rate_limit_enabled, True
    auth_limiter.reset()
    try:
        codes = [
            tclient.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.com", "password": "wrongpass1"},
            ).status_code
            for _ in range(4)
        ]
        assert codes[:3] == [401, 401, 401], codes
        assert codes[3] == 429, codes
        r = tclient.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrongpass1"},
        )
        assert r.json()["detail"]["code"] == "rate_limited"
        assert "retry-after" in {k.lower() for k in r.headers}
    finally:
        settings.rate_limit_auth_per_minute = old_limit
        settings.rate_limit_enabled = old_enabled
        auth_limiter.reset()


def test_cookie_transport_authenticates_without_header():
    r = tclient.post(
        "/api/v1/auth/register",
        json={"email": "cookie@example.com", "password": "passWord123"},
    )
    assert r.status_code == 201
    set_cookie = r.headers.get("set-cookie", "")
    assert "digitwin_access" in set_cookie and "HttpOnly" in set_cookie
    # Fresh client carrying ONLY cookies (like a browser): no Authorization header.
    jar = TestClient(app)
    jar.cookies.update(r.cookies)
    me = jar.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "cookie@example.com"
    # Cookie refresh with empty body (web flow) rotates successfully.
    rr = jar.post("/api/v1/auth/refresh", json={})
    assert rr.status_code == 200 and "digitwin_access" in rr.headers.get("set-cookie", "")


def test_cookie_csrf_blocked_for_cross_site_mutation():
    r = tclient.post(
        "/api/v1/auth/register",
        json={"email": "csrf@example.com", "password": "passWord123"},
    )
    jar = TestClient(app)
    jar.cookies.update(r.cookies)
    evil = {"Origin": "https://evil.example.com"}
    blocked = jar.post("/api/v1/profiles/me/goals", json={"title": "x"}, headers=evil)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "csrf_blocked"
    # Safe methods and same-origin/origin-less requests still pass.
    assert jar.get("/api/v1/profiles/me", headers=evil).status_code == 200
    ok = jar.post("/api/v1/profiles/me/goals", json={"title": "legit"})
    assert ok.status_code == 201, ok.text


def test_refresh_reuse_revokes_all_sessions():
    r = tclient.post(
        "/api/v1/auth/register",
        json={"email": "reuse@example.com", "password": "passWord123"},
    )
    t1 = r.json()["refresh_token"]
    t2 = tclient.post("/api/v1/auth/refresh", json={"refresh_token": t1}).json()["refresh_token"]
    replay = tclient.post("/api/v1/auth/refresh", json={"refresh_token": t1})
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "refresh_reused"
    # Theft response: even the rotated token is now dead.
    assert tclient.post("/api/v1/auth/refresh", json={"refresh_token": t2}).status_code == 401


def test_purge_removes_only_grace_expired_tokens():
    db = SessionLocal()
    try:
        r = tclient.post(
            "/api/v1/auth/register",
            json={"email": "purge@example.com", "password": "passWord123"},
        )
        user_id = tclient.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        ).json()["id"]
        old = models.RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token("expired-token"),
            expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5),
            created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=6),
        )
        live_revoked = models.RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token("live-revoked-token"),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=5),
            revoked_at=datetime.now(UTC).replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add_all([old, live_revoked])
        db.commit()
        assert purge_expired_tokens(db) >= 1
        remaining = {
            t.token_hash
            for t in db.scalars(
                select(models.RefreshToken).where(models.RefreshToken.user_id == user_id)
            )
        }
        assert hash_refresh_token("expired-token") not in remaining
        # Unexpired revoked rows survive: reuse detection needs them.
        assert hash_refresh_token("live-revoked-token") in remaining
    finally:
        db.close()


def test_foreign_keys_enforced_at_db_level():
    db = SessionLocal()
    try:
        db.add(
            models.EducationInfo(
                profile_id="00000000-0000-0000-0000-000000000000", level="bachelor"
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("orphan education row was accepted: FK pragma off")
    finally:
        db.close()
