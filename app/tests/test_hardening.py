"""Set 4 production hardening: error schema, atomicity, races, abuse shields.

Covers only gaps the audit found; existing suites own the rest.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rate_limit import auth_limiter
from app.db import models
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)
PW = "passWord123"


def _token(email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    return client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()["access_token"]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _pid(email: str) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        return db.scalar(select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)).id
    finally:
        db.close()


def _quiz(t: str) -> str:
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    return quizzes[0]["id"]


def _omap(aid: str) -> dict[str, dict[str, str]]:
    db = SessionLocal()
    try:
        out = {}
        for q in db.scalars(select(models.Question).where(models.Question.assessment_id == aid)):
            by_correct = {o.is_correct: o.id for o in q.options}
            out[q.id] = {"right": by_correct[True], "wrong": by_correct[False]}
        return out
    finally:
        db.close()


def _start(t: str, aid: str) -> str:
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    return att.json()["id"]


def test_unexpected_errors_use_consistent_schema_without_leak(monkeypatch):
    from fastapi.testclient import TestClient as _TC

    from app.services import twin_service

    def _boom(db, profile_id, now=None):
        raise RuntimeError("super secret db password=hunter2")

    monkeypatch.setattr(twin_service, "read_twin", _boom)
    # No-raise client: the generic 500 handler must convert the boom into the
    # standard {detail:{code,message}} shape instead of leaking internals.
    quiet = _TC(app, raise_server_exceptions=False)
    t = _token("hard500@example.com")
    r = quiet.get("/api/v1/profiles/me/twin", headers=_h(t))
    assert r.status_code == 500, r.status_code
    body = r.json()
    assert set(body) == {"detail"} and set(body["detail"]) == {"code", "message"}
    assert body["detail"]["code"] == "internal_error"
    blob = str(body).lower()
    assert "hunter2" not in blob and "password" not in blob


def test_submit_conflict_rolls_back_without_partial_state(monkeypatch):
    from app.services import skill_graph

    def _boom(db, attempt):
        raise IntegrityError("INSERT", {}, Exception("simulated race"))

    monkeypatch.setattr(skill_graph, "record_submission_evidence", _boom)
    email = "hardconflict@example.com"
    t = _token(email)
    aid = _quiz(t)
    att_id = _start(t, aid)
    r = client.post(f"/api/v1/attempts/{att_id}/submit", headers=_h(t))
    assert r.status_code == 409, (r.status_code, r.text)
    assert r.json()["detail"]["code"] == "submit_conflict"
    db = SessionLocal()
    try:
        attempt = db.get(models.Attempt, att_id)
        assert attempt.status == "in_progress"  # loser state untouched
        assert (
            db.scalar(
                select(models.SkillEvidence).where(models.SkillEvidence.attempt_id == att_id)
            )
            is None
            or db.scalars(
                select(models.SkillEvidence).where(models.SkillEvidence.attempt_id == att_id)
            ).all()
            == []
        )
        pid = _pid(email)
        assert (
            db.scalars(
                select(models.TwinSnapshot).where(models.TwinSnapshot.profile_id == pid)
            ).all()
            == []
        )
    finally:
        db.close()


def test_notify_failure_cannot_fail_submission(monkeypatch):
    from app.services import notify_service

    def _boom(db, profile_id, snapshot, computed):
        raise RuntimeError("notify exploded")

    monkeypatch.setattr(notify_service, "notify_after_submit", _boom)
    t = _token("hardnotify@example.com")
    aid = _quiz(t)
    att_id = _start(t, aid)
    om = _omap(aid)
    client.put(
        f"/api/v1/attempts/{att_id}/answers",
        json={"answers": [{"question_id": q, "selected_option_id": v["right"]} for q, v in om.items()]},
        headers=_h(t),
    )
    r = client.post(f"/api/v1/attempts/{att_id}/submit", headers=_h(t))
    assert r.status_code == 200, r.text  # graded result committed despite notify failure
    assert r.json()["score"] == r.json()["max_score"]


def test_save_answers_race_retries_without_duplication(monkeypatch):
    t = _token("hardautosave@example.com")
    aid = _quiz(t)
    att_id = _start(t, aid)
    om = _omap(aid)
    qid = next(iter(om))
    # Patch only around the racy PUT: earlier setup commits must succeed.
    real_commit = Session.commit
    calls = {"n": 0}

    def _flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("simulated autosave race"))
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", _flaky)
    r = client.put(
        f"/api/v1/attempts/{att_id}/answers",
        json={"answers": [{"question_id": qid, "selected_option_id": om[qid]["right"]}]},
        headers=_h(t),
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        rows = db.scalars(select(models.Answer).where(models.Answer.attempt_id == att_id)).all()
        assert len(rows) == 1  # retried into the winner's row, never duplicated
        assert rows[0].selected_option_id == om[qid]["right"]
    finally:
        db.close()


def test_mentor_rate_limit_returns_429_shape():
    settings = get_settings()
    old_limit, settings.rate_limit_auth_per_minute = settings.rate_limit_auth_per_minute, 3
    old_enabled, settings.rate_limit_enabled = settings.rate_limit_enabled, True
    auth_limiter.reset()
    try:
        t = _token("hardratelimit@example.com")
        codes = [
            client.post(
                "/api/v1/profiles/me/mentor/message",
                json={"message": "How am I doing?"},
                headers=_h(t),
            ).status_code
            for _ in range(4)
        ]
        assert codes[:3] == [200, 200, 200], codes
        assert codes[3] == 429, codes
        # Re-enable budget for the shape assertion below.
        auth_limiter.reset()
        r = client.post(
            "/api/v1/profiles/me/mentor/message",
            json={"message": "How am I doing?"},
            headers=_h(t),
        )
        assert r.status_code == 200
    finally:
        settings.rate_limit_auth_per_minute = old_limit
        settings.rate_limit_enabled = old_enabled
        auth_limiter.reset()


def test_cors_allowlist_never_wildcards_credentials():
    evil = client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
    assert evil.status_code == 200
    headers = {k.lower(): v for k, v in evil.headers.items()}
    assert headers.get("access-control-allow-origin") in (None, ""), headers
    assert "*" not in str(headers.get("access-control-allow-origin", ""))
    allowed = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_malformed_input_uses_consistent_schema():
    t = _token("hardmalformed@example.com")
    r = client.post(
        "/api/v1/profiles/me/recommendations/feedback",
        json={"rec_key": "k", "kind": "k", "refs": ["not", "a", "dict"], "action": "started"},
        headers=_h(t),
    )
    assert r.status_code == 422, r.status_code
    assert set(r.json()["detail"]) == {"code", "message"}
    assert client.get("/api/v1/profiles/me/recommendations/history", headers=_h(t)).json() == []


def test_history_limits_are_bounded():
    t = _token("hardlimits@example.com")
    for path in (
        "/api/v1/profiles/me/recommendations/history?limit=999999",
        "/api/v1/profiles/me/twin/evolution?limit=999999",
        "/api/v1/profiles/me/twin/snapshots?limit=999999",
        "/api/v1/profiles/me/notifications?limit=999999",
        "/api/v1/profiles/me/attempts?limit=999999",
    ):
        r = client.get(path, headers=_h(t))
        assert r.status_code == 200, (path, r.status_code)
        assert len(r.json()) <= 100, (path, len(r.json()))
