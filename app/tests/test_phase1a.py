"""Foundation tests: health, auth lifecycle, cross-client profile sync contract."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _register(email="sam@student.example.com", password="passWord123"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_auth_lifecycle_and_protection():
    tokens = _register()
    assert tokens["access_token"] and tokens["refresh_token"]
    # duplicate email rejected
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": "sam@student.example.com", "password": "passWord123"},
        ).status_code
        == 409
    )
    # wrong password rejected
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "sam@student.example.com", "password": "nope-nope-12"},
        ).status_code
        == 401
    )
    # unauthenticated profile blocked (drop cookie jar: register responses set cookies)
    client.cookies.clear()
    assert client.get("/api/v1/profiles/me").status_code == 401
    # refresh rotation works, old refresh dies
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_web_mobile_same_profile_contract():
    """Simulates: web login -> profile; mobile login -> same profile; mobile edit -> web sees it."""
    tokens = _register("alex@student.example.com")
    web_h = {"Authorization": f"Bearer {tokens['access_token']}"}
    mobile_login = client.post(
        "/api/v1/auth/login", json={"email": "alex@student.example.com", "password": "passWord123"}
    )
    mobile_h = {"Authorization": f"Bearer {mobile_login.json()['access_token']}"}

    web_profile = client.get("/api/v1/profiles/me", headers=web_h).json()
    mobile_profile = client.get("/api/v1/profiles/me", headers=mobile_h).json()
    assert web_profile["id"] == mobile_profile["id"]  # same backend-owned twin state

    # mobile modifies profile
    r = client.put(
        "/api/v1/profiles/me",
        headers=mobile_h,
        json={"full_name": "Alex Mobile", "hours_per_week": 10},
    )
    assert r.status_code == 200
    # web refresh sees the change
    assert client.get("/api/v1/profiles/me", headers=web_h).json()["full_name"] == "Alex Mobile"

    # nested resources round-trip
    assert (
        client.post(
            "/api/v1/profiles/me/goals", headers=web_h, json={"title": "ML Engineer"}
        ).status_code
        == 201
    )
    assert (
        client.get("/api/v1/profiles/me/goals", headers=mobile_h).json()[0]["title"]
        == "ML Engineer"
    )
    bad = client.post(
        "/api/v1/profiles/me/availability",
        headers=web_h,
        json={"weekday": 0, "start_time": "18:00", "end_time": "09:00"},
    )
    assert bad.status_code == 422  # business rule enforced server-side
    good = client.post(
        "/api/v1/profiles/me/availability",
        headers=web_h,
        json={"weekday": 0, "start_time": "09:00", "end_time": "11:00"},
    )
    assert good.status_code == 201
    assert client.get("/api/v1/subjects").status_code == 200


def test_error_shape_is_consistent():
    """Phase 1B: auth failures must use {detail:{code,message}}, not raw strings."""
    client.cookies.clear()
    r = client.get("/api/v1/profiles/me")
    assert r.status_code == 401
    assert set(r.json()["detail"].keys()) == {"code", "message"}
    r = client.get("/api/v1/profiles/me", headers={"Authorization": "Bearer bogus.token.here"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_token"


def test_logout_without_body_revokes_all_sessions():
    """Phase 1B: bodiless logout must not be a client-side-only no-op."""
    t1 = _register("logout@student.example.com")
    t2 = client.post(
        "/api/v1/auth/login",
        json={"email": "logout@student.example.com", "password": "passWord123"},
    ).json()
    h = {"Authorization": f"Bearer {t1['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204
    for tok in (t1["refresh_token"], t2["refresh_token"]):
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": tok}).status_code == 401


def test_subjects_tolerate_duplicates_reject_unknown():
    """Phase 1B: duplicate IDs are deduped; unknown IDs are 422."""
    tokens = _register("subj@student.example.com")
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    catalog = client.get("/api/v1/subjects").json()
    assert len(catalog) >= 2
    ids = [catalog[0]["id"], catalog[0]["id"], catalog[1]["id"]]
    r = client.put("/api/v1/profiles/me/subjects", headers=h, json={"subject_ids": ids})
    assert r.status_code == 200, r.text
    assert sorted(s["id"] for s in r.json()) == sorted({catalog[0]["id"], catalog[1]["id"]})
    bad = client.put(
        "/api/v1/profiles/me/subjects", headers=h, json={"subject_ids": ["no-such-id"]}
    )
    assert bad.status_code == 422


def test_user_delete_cascades_profile_and_tokens():
    """Phase 1B: deleting a user must not orphan profile rows or live sessions."""
    from sqlalchemy import select

    from app.db import models
    from app.db.session import SessionLocal

    tokens = _register("cascade@student.example.com")
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    profile_id = client.get("/api/v1/profiles/me", headers=h).json()["id"]

    db = SessionLocal()
    try:
        user = db.scalar(
            select(models.User).where(models.User.email == "cascade@student.example.com")
        )
        db.delete(user)
        db.commit()
        assert db.get(models.StudentProfile, profile_id) is None
        assert (
            db.scalar(select(models.RefreshToken).where(models.RefreshToken.user_id == user.id))
            is None
        )
    finally:
        db.close()
