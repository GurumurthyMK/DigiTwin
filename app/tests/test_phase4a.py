"""Phase 4A tests: intelligence is deterministic, evidence-linked, honest about
insufficiency, and never fabricates. No accuracy claims beyond the baseline."""

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


def _quiz(t, subject_idx=0):
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[subject_idx]['id']}/topics", headers=_h(t)).json()
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


def _strip_ts(obj):
    if isinstance(obj, dict):
        return {k: _strip_ts(v) for k, v in obj.items() if k != "generated_at"}
    if isinstance(obj, list):
        return [_strip_ts(v) for v in obj]
    return obj


def test_no_evidence_honest_states():
    t = _token("aiempty@example.com")
    assert client.get("/api/v1/profiles/me/insights", headers=_h(t)).json() == []
    pred = client.get("/api/v1/profiles/me/prediction", headers=_h(t)).json()
    assert pred["status"] == "insufficient_data" and pred["expected"] is None
    assert "3 graded attempts" in pred["requires"] and pred["disclaimer"]
    plan = client.get("/api/v1/profiles/me/recommendations", headers=_h(t)).json()
    assert plan["today"], "even with zero evidence the student gets a starter plan"
    assert plan["engine"] == "4a.1"
    career = client.get("/api/v1/profiles/me/career", headers=_h(t)).json()
    assert career["taxonomy_version"] == "careers-4a.1" and career["disclaimer"]
    assert all(m["confidence"] == "low" for m in career["matches"])
    assert all(m["missing"] for m in career["matches"]), "gaps shown, not hidden"


def test_mixed_evidence_drives_findings():
    t = _token("aimixed@example.com")
    aid = _quiz(t)
    _submit(t, aid, correct=True)
    _submit(t, aid, correct=False)
    insights = client.get("/api/v1/profiles/me/insights", headers=_h(t)).json()
    kinds = {i["type"] for i in insights}
    assert "weakness" in kinds, insights  # 0% sitting drags the topic under 0.45
    weak = next(i for i in insights if i["type"] == "weakness")
    assert weak["evidence"] and weak["confidence"] in ("low", "medium", "high")
    assert weak["priority"] in (1, 2) and weak["explanation"]
    # Every topic evidence ref resolves to a real stored topic.
    db = SessionLocal()
    try:
        for ins in insights:
            for ev in ins["evidence"]:
                if ev["kind"] == "topic":
                    assert db.get(models.Topic, ev["id"]) is not None
    finally:
        db.close()
    plan = client.get("/api/v1/profiles/me/recommendations", headers=_h(t)).json()
    assert plan["today"] and all(r["reason"] and r["refs"] is not None for r in plan["today"])
    assert plan["today"][0]["priority"] == 1


def test_prediction_math_and_determinism():
    t = _token("aipred@example.com")
    aid = _quiz(t)
    _submit(t, aid, correct=True)  # acc 1.0
    _submit(t, aid, correct=False)  # acc 0.0
    _submit(t, aid, correct=True)  # acc 1.0
    p1 = client.get("/api/v1/profiles/me/prediction", headers=_h(t)).json()
    assert p1["status"] == "ready" and p1["method"] == "baseline-ewma-4a.1"
    # Hand-check: ewma = 0.35*1 + 0.65*(0.35*0 + 0.65*1) = 0.7725; expected = 0.5*1 + 0.5*0.7725
    assert abs(p1["expected"] - 0.88625) < 0.01, p1
    assert p1["low"] <= p1["expected"] <= p1["high"] and p1["low"] >= 0.0 and p1["high"] <= 1.0
    assert p1["confidence"] == "low" and p1["evidence_n"] == 3  # 3 sittings: no false confidence
    assert "not a validated forecast" in p1["disclaimer"]
    p2 = client.get("/api/v1/profiles/me/prediction", headers=_h(t)).json()
    assert _strip_ts(p1) == _strip_ts(p2), "same evidence => same outputs"
    i2 = client.get("/api/v1/profiles/me/insights", headers=_h(t)).json()
    assert _strip_ts(client.get("/api/v1/profiles/me/insights", headers=_h(t)).json()) == _strip_ts(
        i2
    )


def test_career_transparency_and_goals():
    t = _token("aicareer@example.com")
    aid = _quiz(t)
    _submit(t, aid, correct=True)
    client.post("/api/v1/profiles/me/goals", json={"title": "Data Analyst"}, headers=_h(t))
    client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "beginner"}, headers=_h(t)
    )
    career = client.get("/api/v1/profiles/me/career", headers=_h(t)).json()
    da = next(m for m in career["matches"] if m["career_id"] == "data-analyst")
    assert "Data Analyst" in da["aligned_goals"], da
    assert da["factors"], "contributing factors shown"
    assert 0.0 <= da["score"] <= 1.0 and 0.0 <= da["coverage"] <= 1.0
    assert "not a guaranteed outcome" in career["disclaimer"]
    # Sorted best-first.
    scores = [m["score"] for m in career["matches"]]
    assert scores == sorted(scores, reverse=True)


def test_ai_cross_student_isolation():
    a = _token("aiisoA@example.com")
    b = _token("aiisoB@example.com")
    aid = _quiz(a)
    _submit(a, aid, correct=True)
    assert client.get("/api/v1/profiles/me/insights", headers=_h(a)).json() != []
    assert client.get("/api/v1/profiles/me/insights", headers=_h(b)).json() == []
    assert (
        client.get("/api/v1/profiles/me/prediction", headers=_h(b)).json()["status"]
        == "insufficient_data"
    )
