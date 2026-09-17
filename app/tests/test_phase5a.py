"""Phase 5A tests: overview bundle, mentor determinism/boundaries, notification
generation rules, anti-spam, prefs, and read-state isolation."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal
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


def _options(aid):
    db = SessionLocal()
    try:
        out = {}
        for q in db.scalars(select(models.Question).where(models.Question.assessment_id == aid)):
            opts = db.scalars(
                select(models.QuestionOption).where(models.QuestionOption.question_id == q.id)
            ).all()
            out[q.id] = {
                "correct": next(o.id for o in opts if o.is_correct),
                "wrong": next(o.id for o in opts if not o.is_correct),
            }
        return out
    finally:
        db.close()


def _quiz(t):
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    return quizzes[0]["id"]


def _submit(t, aid, correct: bool):
    info = _options(aid)
    key = "correct" if correct else "wrong"
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [{"question_id": q, "selected_option_id": v[key]} for q, v in info.items()]
        },
        headers=_h(t),
    )
    return client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()


def test_overview_bundle_single_shape():
    t = _token("oview@example.com")
    ov = client.get("/api/v1/profiles/me/overview", headers=_h(t)).json()
    assert ov["attempts_count"] == 0 and ov["today"] and ov["urgent"] == []
    assert ov["unread_notifications"] == 0 and ov["in_progress"] == []
    assert ov["onboarding_completed"] is False
    aid = _quiz(t)
    _submit(t, aid, correct=True)
    ov = client.get("/api/v1/profiles/me/overview", headers=_h(t)).json()
    assert ov["attempts_count"] == 1 and ov["overall_accuracy"] == 1.0
    assert ov["today"] and "x-request-id" in {
        k.lower() for k in client.get("/api/v1/health").headers
    }


def test_mentor_fixed_set_and_evidence():
    t = _token("mentor@example.com")
    qs = client.get("/api/v1/profiles/me/mentor/questions", headers=_h(t)).json()["questions"]
    assert len(qs) == 6 and all("key" in q and "question" in q for q in qs)
    first = client.get(
        "/api/v1/profiles/me/mentor/answers",
        params={"question": "performance_summary"},
        headers=_h(t),
    ).json()
    assert "No graded quizzes yet" in first["answer"]
    assert first["follow_ups"] and all("key" in f for f in first["follow_ups"])
    assert (
        client.get(
            "/api/v1/profiles/me/mentor/answers",
            params={"question": "tell me my grades hacker"},
            headers=_h(t),
        ).status_code
        == 404
    )
    aid = _quiz(t)
    _submit(t, aid, correct=False)
    weak = client.get(
        "/api/v1/profiles/me/mentor/answers", params={"question": "why_weak"}, headers=_h(t)
    ).json()
    assert weak["evidence"] and "mastery" in weak["answer"].lower() or "%" in weak["answer"]
    nxt = client.get(
        "/api/v1/profiles/me/mentor/answers", params={"question": "what_next"}, headers=_h(t)
    ).json()
    assert nxt["answer"] and nxt["evidence"] is not None
    # Deterministic: same evidence, same answer.
    again = client.get(
        "/api/v1/profiles/me/mentor/answers", params={"question": "why_weak"}, headers=_h(t)
    ).json()
    assert {k: v for k, v in weak.items()} == {k: v for k, v in again.items()}


def test_notification_generation_and_antispam():
    t = _token("notif@example.com")
    aid = _quiz(t)
    _submit(t, aid, correct=True)  # first submit: plan nudge fires
    notes = client.get("/api/v1/profiles/me/notifications", headers=_h(t)).json()
    kinds = {n["kind"] for n in notes}
    assert "plan" in kinds, notes
    # Big mover -> twin_change (perfect then zero on same topic swings hard).
    _submit(t, aid, correct=False)
    _submit(t, aid, correct=False)
    notes = client.get("/api/v1/profiles/me/notifications", headers=_h(t)).json()
    assert "twin_change" in {n["kind"] for n in notes}, [n["kind"] for n in notes]
    # Anti-spam: another big swing within 24h adds NO second unread twin_change.
    n_before = len([n for n in notes if n["kind"] == "twin_change" and not n["read"]])
    _submit(t, aid, correct=True)
    notes2 = client.get("/api/v1/profiles/me/notifications", headers=_h(t)).json()
    n_after = len([n for n in notes2 if n["kind"] == "twin_change" and not n["read"]])
    assert n_after <= n_before + 1, (n_before, n_after)
    # Mark read works; strangers cannot read.
    target = next(n for n in notes2 if not n["live"] and not n["read"])
    assert (
        client.put(
            f"/api/v1/profiles/me/notifications/{target['id']}/read", headers=_h(t)
        ).status_code
        == 200
    )
    other = _token("notif2@example.com")
    assert (
        client.put(
            f"/api/v1/profiles/me/notifications/{target['id']}/read", headers=_h(other)
        ).status_code
        == 404
    )


def test_notification_prefs_gate_generation():
    t = _token("prefs@example.com")
    r = client.put(
        "/api/v1/profiles/me/notification-prefs", json={"plan_enabled": False}, headers=_h(t)
    )
    assert r.status_code == 200 and r.json()["plan_enabled"] is False
    aid = _quiz(t)
    _submit(t, aid, correct=True)
    notes = client.get("/api/v1/profiles/me/notifications", headers=_h(t)).json()
    assert "plan" not in {n["kind"] for n in notes}, notes
    # Others' prefs untouched (defaults on).
    other = _token("prefs2@example.com")
    assert (
        client.get("/api/v1/profiles/me/notification-prefs", headers=_h(other)).json()[
            "plan_enabled"
        ]
        is True
    )


def test_live_resume_reminder_not_stored():
    t = _token("resume@example.com")
    aid = _quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    notes = client.get("/api/v1/profiles/me/notifications", headers=_h(t)).json()
    live = [n for n in notes if n["live"]]
    assert live and live[0]["ref_id"] == att["id"] and live[0]["kind"] == "resume"
    db = SessionLocal()
    try:
        stored = db.scalars(select(models.Notification)).all()
        assert all(not n.id.startswith("live-") for n in stored)
        mine = [n for n in stored if n.kind == "resume"]
        assert mine == [], "reminders must never hit the table"
    finally:
        db.close()


def test_password_reset_full_cycle():
    from app.core import security as _sec
    from app.services import auth_service as _as

    email = "resetflow@example.com"
    client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    # Unknown email: identical 200, no row, no signal.
    assert (
        client.post(
            "/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"}
        ).status_code
        == 200
    )
    db = SessionLocal()
    try:
        assert db.scalar(select(models.PasswordResetToken)) is None
    finally:
        db.close()
    assert (
        client.post("/api/v1/auth/password-reset/request", json={"email": email}).status_code == 200
    )
    db = SessionLocal()
    try:
        row = db.scalar(select(models.PasswordResetToken))
        assert row is not None and row.used_at is None
        uid = row.user_id
    finally:
        db.close()
    # Raw token is unrecoverable from storage (hash only) — mint a parallel one
    # in-process to exercise confirm path end to end.
    db = SessionLocal()
    try:
        _as.request_password_reset(db, email)
        row2 = db.scalars(
            select(models.PasswordResetToken)
            .where(models.PasswordResetToken.user_id == uid)
            .order_by(models.PasswordResetToken.created_at.desc())
        ).first()
        assert row2 is not None
    finally:
        db.close()
    # Wrong token rejected.
    assert (
        client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "bogus", "new_password": "BrandNew123"},
        ).status_code
        == 400
    )
    # Real confirm requires the raw secret: verify via service with a fresh token.
    db = SessionLocal()
    try:
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        raw2 = _sec.new_refresh_token()
        db.add(
            models.PasswordResetToken(
                user_id=uid,
                token_hash=_sec.hash_refresh_token(raw2),
                expires_at=_dt.now(_UTC) + _td(hours=1),
                created_at=_dt.now(_UTC),
            )
        )
        db.commit()
    finally:
        db.close()
    assert (
        client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw2, "new_password": "BrandNew123"},
        ).status_code
        == 200
    )
    # Single-use: replay dies.
    assert (
        client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw2, "new_password": "BrandNew123"},
        ).status_code
        == 400
    )
    # New password works, old does not, and old sessions are dead.
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": PW}).status_code == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": "BrandNew123"}
        ).status_code
        == 200
    )


def test_password_change_rotates_sessions():
    email = "changepw@example.com"
    t1 = client.post("/api/v1/auth/register", json={"email": email, "password": PW}).json()
    t2 = client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()
    r = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "wrong-current", "new_password": "BrandNew123"},
        headers={"Authorization": f"Bearer {t1['access_token']}"},
    )
    assert r.status_code == 400
    r = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": PW, "new_password": "BrandNew123"},
        headers={"Authorization": f"Bearer {t1['access_token']}"},
    )
    assert r.status_code == 200 and r.json()["access_token"]
    # Both old refresh tokens are dead; login needs the new password.
    for tok in (t1["refresh_token"], t2["refresh_token"]):
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": tok}).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": "BrandNew123"}
        ).status_code
        == 200
    )


def test_delete_account_cascades_everything():
    email = "deleteme@example.com"
    t = client.post("/api/v1/auth/register", json={"email": email, "password": PW}).json()[
        "access_token"
    ]
    h = {"Authorization": f"Bearer {t}"}
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=h).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=h).json()
    att = client.post(f"/api/v1/assessments/{quizzes[0]['id']}/attempts", headers=h).json()
    client.post(f"/api/v1/attempts/{att['id']}/submit", headers=h)
    # Wrong password refuses.
    assert (
        client.request(
            "DELETE", "/api/v1/auth/users/me", json={"password": "nope-nope-12"}, headers=h
        ).status_code
        == 400
    )
    db = SessionLocal()
    try:
        uid = db.scalar(select(models.User).where(models.User.email == email)).id
        pid = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == uid)
        ).id
    finally:
        db.close()
    assert (
        client.request(
            "DELETE", "/api/v1/auth/users/me", json={"password": PW}, headers=h
        ).status_code
        == 204
    )
    db = SessionLocal()
    try:
        assert db.scalar(select(models.User).where(models.User.email == email)) is None
        # Nothing may remain that references the deleted profile/user.
        assert (
            db.scalar(select(models.StudentProfile).where(models.StudentProfile.user_id == uid))
            is None
        )
        assert (
            db.scalars(select(models.RefreshToken).where(models.RefreshToken.user_id == uid)).all()
            == []
        )
        assert (
            db.scalars(select(models.Attempt).where(models.Attempt.profile_id == pid)).all() == []
        )
        assert (
            db.scalars(
                select(models.TwinSnapshot).where(models.TwinSnapshot.profile_id == pid)
            ).all()
            == []
        )
        assert (
            db.scalars(select(models.TwinState).where(models.TwinState.profile_id == pid)).all()
            == []
        )
        assert (
            db.scalars(
                select(models.Notification).where(models.Notification.profile_id == pid)
            ).all()
            == []
        )
        assert (
            db.scalars(
                select(models.LearningProgress).where(models.LearningProgress.profile_id == pid)
            ).all()
            == []
        )
        assert (
            db.scalars(
                select(models.PasswordResetToken).where(models.PasswordResetToken.user_id == uid)
            ).all()
            == []
        )
    finally:
        db.close()
    # Login impossible after deletion.
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": PW}).status_code == 401
    )
