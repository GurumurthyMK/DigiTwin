"""Phase 5B security + API audit: malformed input, isolation on every resource,
public/private boundaries, methods, clamping. Fixes ship with the findings."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PW = "passWord123"


def _token(email):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    return client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()[
        "access_token"
    ]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def test_malformed_availability_rejected():
    t = _token("malformed@example.com")
    for body in [
        {"weekday": 9, "start_time": "09:00", "end_time": "10:00"},
        {"weekday": 0, "start_time": "25:00", "end_time": "26:00"},
        {"weekday": 0, "start_time": "09:60", "end_time": "10:00"},
        {"weekday": 0, "start_time": "9:00", "end_time": "10:00"},
        {"weekday": 0, "start_time": "12:00", "end_time": "12:00"},
        {"weekday": -1, "start_time": "09:00", "end_time": "10:00"},
    ]:
        r = client.post("/api/v1/profiles/me/availability", json=body, headers=_h(t))
        assert r.status_code == 422, (body, r.status_code)
        assert r.json()["detail"]["code"] in ("validation_error", "invalid_slot"), r.json()


def test_profile_bounds_and_password_limits():
    t = _token("bounds@example.com")
    assert (
        client.put("/api/v1/profiles/me", json={"hours_per_week": 200}, headers=_h(t)).status_code
        == 422
    )
    assert (
        client.put("/api/v1/profiles/me", json={"hours_per_week": -1}, headers=_h(t)).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/register", json={"email": "x@example.com", "password": "x" * 200}
        ).status_code
        == 422
    )


def test_email_duplicate_case_insensitive():
    client.post("/api/v1/auth/register", json={"email": "CaseDup@example.com", "password": PW})
    r = client.post("/api/v1/auth/register", json={"email": "casedup@EXAMPLE.com", "password": PW})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_taken"


def test_cross_user_subresource_isolation():
    a = _token("isoOwner@example.com")
    b = _token("isoStranger@example.com")
    edu = client.post(
        "/api/v1/profiles/me/education", json={"level": "bachelor"}, headers=_h(a)
    ).json()
    goal = client.post("/api/v1/profiles/me/goals", json={"title": "X"}, headers=_h(a)).json()
    slot = client.post(
        "/api/v1/profiles/me/availability",
        json={"weekday": 1, "start_time": "09:00", "end_time": "10:00"},
        headers=_h(a),
    ).json()
    for path in [
        f"/api/v1/profiles/me/education/{edu['id']}",
        f"/api/v1/profiles/me/goals/{goal['id']}",
        f"/api/v1/profiles/me/availability/{slot['id']}",
    ]:
        r = client.delete(path, headers=_h(b))
        assert r.status_code == 404, (path, r.status_code)
    # Owner deletes still work (no over-blocking).
    assert (
        client.delete(f"/api/v1/profiles/me/goals/{goal['id']}", headers=_h(a)).status_code == 204
    )
    # Foreign progress target.
    assert (
        client.put(
            "/api/v1/profiles/me/progress/no-such-content",
            json={"status": "completed"},
            headers=_h(b),
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/v1/profiles/me/progress/no-such-content", json={"status": "done!"}, headers=_h(b)
        ).status_code
        == 422
    )


def test_public_private_method_boundaries():
    client.cookies.clear()  # shared jar holds session cookies from earlier tests
    assert client.get("/api/v1/subjects").status_code == 200
    assert client.get("/api/v1/skills").status_code == 200
    for path in [
        "/api/v1/profiles/me",
        "/api/v1/profiles/me/twin",
        "/api/v1/profiles/me/insights",
        "/api/v1/profiles/me/overview",
        "/api/v1/profiles/me/notifications",
        "/api/v1/profiles/me/prediction",
        "/api/v1/profiles/me/career",
    ]:
        r = client.get(path)
        assert r.status_code == 401 and set(r.json()["detail"].keys()) == {"code", "message"}, path
    assert client.get("/api/v1/auth/login").status_code == 405
    assert client.post("/api/v1/auth/login", json={"email": "nope@example.com"}).status_code == 422


def test_pagination_clamped_and_empty_answers_ok():
    t = _token("page@example.com")
    r = client.get("/api/v1/profiles/me/attempts?limit=10000", headers=_h(t))
    assert r.status_code == 200 and isinstance(r.json(), list)
    r = client.get("/api/v1/profiles/me/twin/snapshots?limit=10000", headers=_h(t)).json()
    assert isinstance(r, list)
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    att = client.post(f"/api/v1/assessments/{quizzes[0]['id']}/attempts", headers=_h(t)).json()
    r = client.put(f"/api/v1/attempts/{att['id']}/answers", json={"answers": []}, headers=_h(t))
    assert r.status_code == 200  # empty draft save is a harmless no-op


def test_security_headers_present():
    r = client.get("/api/v1/health")
    h = {k.lower(): v for k, v in r.headers.items()}
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
    assert h.get("referrer-policy") == "same-origin"
    assert "frame-ancestors 'none'" in h.get("content-security-policy", "")
    assert "x-request-id" in h
