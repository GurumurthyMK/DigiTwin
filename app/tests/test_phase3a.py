"""Phase 3A tests: twin state is derived from evidence, updates on submit,
stays historical, and never leaks across students."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services import twin_service

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


def _quiz(t):
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    return quizzes[0]["id"]


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


def _submit(t, aid, correct: bool):
    info = _options(aid)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    key = "correct" if correct else "wrong"
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [{"question_id": q, "selected_option_id": v[key]} for q, v in info.items()]
        },
        headers=_h(t),
    )
    res = client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()
    return att["id"], res


def _twin(t):
    r = client.get("/api/v1/profiles/me/twin", headers=_h(t))
    assert r.status_code == 200, r.text
    return r.json()


def test_twin_empty_before_evidence():
    t = _token("twinempty@example.com")
    tw = _twin(t)
    assert tw["has_evidence"] is False and tw["version"] == 0
    assert tw["overall_mastery"] is None and tw["overall_accuracy"] is None
    assert tw["topics"] == [] and tw["subjects"] == []
    assert client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t)).json() == []


def test_submit_updates_twin_and_snapshots_it():
    t = _token("twingrow@example.com")
    aid = _quiz(t)
    att_id, res = _submit(t, aid, correct=True)

    tw = _twin(t)
    assert tw["has_evidence"] is True and tw["version"] == 1 and tw["attempts_count"] == 1
    assert tw["overall_accuracy"] == 1.0
    assert tw["overall_mastery"] is not None and tw["overall_mastery"] > 0.5
    assert tw["total_answers"] == res["max_score"] and tw["correct_answers"] == res["score"]
    assert len(tw["topics"]) >= 1 and all(x["evidence_count"] > 0 for x in tw["topics"])

    snaps = client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t)).json()
    assert len(snaps) == 1
    assert snaps[0]["trigger_type"] == "assessment_submitted" and snaps[0]["trigger_id"] == att_id
    assert "Attempt 1" in snaps[0]["summary"] and str(res["score"]) in snaps[0]["summary"]

    # Determinism: pure recompute from raw rows matches persisted state.
    db = SessionLocal()
    try:
        profile_id = db.scalar(
            select(models.User).where(models.User.email == "twingrow@example.com")
        ).id
        profile_id = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == profile_id)
        ).id
        recomputed = twin_service.compute_profile_twin(db, profile_id)
    finally:
        db.close()
    assert recomputed["overall_mastery"] == tw["overall_mastery"]
    assert recomputed["overall_accuracy"] == tw["overall_accuracy"]


def test_poor_attempt_moves_mastery_down_with_reason():
    t = _token("twinfall@example.com")
    aid = _quiz(t)
    _submit(t, aid, correct=True)
    before = _twin(t)["overall_mastery"]
    _submit(t, aid, correct=False)
    tw = _twin(t)
    assert tw["version"] == 2 and tw["overall_mastery"] < before
    snaps = client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t)).json()
    assert len(snaps) == 2  # history preserved, newest first
    assert snaps[0]["created_at"] >= snaps[1]["created_at"]
    deltas = [c["delta"] for c in snaps[0]["changes"]]
    assert deltas and all(d < 0 for d in deltas), snaps[0]["changes"]
    assert tw["latest_change"] and "Attempt 2" in tw["latest_change"]


def test_trend_and_consistency_need_evidence_depth():
    t = _token("twintrend@example.com")
    aid = _quiz(t)
    _submit(t, aid, correct=True)
    tw = _twin(t)
    assert tw["trend_direction"] is None and tw["consistency"] is None  # 1 attempt: honestly null
    _submit(t, aid, correct=True)
    _submit(t, aid, correct=False)
    tw = _twin(t)
    assert tw["trend_direction"] in ("improving", "stable", "declining")
    assert tw["consistency"] is not None and 0.0 <= tw["consistency"] <= 1.0


def test_twin_isolation_between_students():
    a = _token("twinisoA@example.com")
    b = _token("twinisoB@example.com")
    aid = _quiz(a)
    _submit(a, aid, correct=True)
    assert _twin(a)["has_evidence"] is True
    tw_b = _twin(b)
    assert tw_b["has_evidence"] is False and tw_b["overall_mastery"] is None
