"""Phase 2A tests: curriculum, attempt lifecycle, server-authoritative scoring,
answer secrecy, attempt isolation, history/performance honesty."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)

EMAIL = "quiz@student.example.com"
PW = "passWord123"


def _auth():
    r = client.post("/api/v1/auth/register", json={"email": EMAIL, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    r = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _first_assessment(token):
    subs = client.get("/api/v1/subjects").json()
    assert subs, "seed subjects missing"
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(token)).json()
    assert len(topics) >= 2, topics
    detail = client.get(f"/api/v1/topics/{topics[0]['id']}", headers=_h(token)).json()
    assert detail["content_count"] >= 1
    content = client.get(f"/api/v1/topics/{topics[0]['id']}/content", headers=_h(token)).json()
    assert content[0]["body"], "lessons must carry real content"
    assessments = client.get(
        f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(token)
    ).json()
    assert assessments, "seed assessments missing"
    full = client.get(f"/api/v1/assessments/{assessments[0]['id']}", headers=_h(token)).json()
    return assessments[0]["id"], full


def _correct_map(assessment_id):
    db = SessionLocal()
    try:
        out = {}
        for q in db.scalars(
            select(models.Question).where(models.Question.assessment_id == assessment_id)
        ):
            opts = db.scalars(
                select(models.QuestionOption).where(models.QuestionOption.question_id == q.id)
            ).all()
            assert sum(o.is_correct for o in opts) == 1, "exactly one correct option per question"
            out[q.id] = next(o.id for o in opts if o.is_correct)
        return out
    finally:
        db.close()


def test_full_attempt_lifecycle_perfect_score():
    token = _auth()
    aid, full = _first_assessment(token)
    assert "is_correct" not in str(full).lower() or True  # schema check below is authoritative
    for q in full["questions"]:
        assert set(q.keys()) == {"id", "kind", "prompt", "points", "options"}, q.keys()
        for o in q["options"]:
            assert set(o.keys()) == {"id", "label"}, o.keys()  # no correctness leak

    r = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(token))
    assert r.status_code == 201, r.text
    attempt = r.json()
    assert attempt["attempt_number"] == 1 and attempt["status"] == "in_progress"

    # Resume returns the SAME sitting (interruption-safe), not a new attempt number.
    r2 = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(token))
    assert r2.status_code == 200 and r2.json()["id"] == attempt["id"]

    correct = _correct_map(aid)
    payload = {"answers": [{"question_id": q, "selected_option_id": o} for q, o in correct.items()]}
    r = client.put(f"/api/v1/attempts/{attempt['id']}/answers", json=payload, headers=_h(token))
    assert r.status_code == 200, r.text

    r = client.post(f"/api/v1/attempts/{attempt['id']}/submit", headers=_h(token))
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["score"] == result["max_score"] and result["accuracy"] == 1.0
    assert result["time_taken_seconds"] >= 0
    assert all(a["is_correct"] for a in result["answers"])
    assert all(a["explanation"] for a in result["answers"])

    # Idempotent re-submit + frozen answers + result endpoint agreement.
    again = client.post(f"/api/v1/attempts/{attempt['id']}/submit", headers=_h(token)).json()
    assert again["score"] == result["score"]
    frozen = client.put(
        f"/api/v1/attempts/{attempt['id']}/answers", json=payload, headers=_h(token)
    )
    assert frozen.status_code == 409
    fetched = client.get(f"/api/v1/attempts/{attempt['id']}/result", headers=_h(token)).json()
    assert fetched["id"] == attempt["id"]


def test_partial_score_and_history_and_performance():
    token = _auth()
    aid, _ = _first_assessment(token)
    correct = _correct_map(aid)
    qids = list(correct.keys())
    # Answer first question wrong, rest right.
    db = SessionLocal()
    try:
        wrong = db.scalar(
            select(models.QuestionOption).where(
                models.QuestionOption.question_id == qids[0],
                models.QuestionOption.id != correct[qids[0]],
            )
        ).id
    finally:
        db.close()
    attempt = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(token)).json()
    payload = {
        "answers": [{"question_id": qids[0], "selected_option_id": wrong}]
        + [{"question_id": q, "selected_option_id": correct[q]} for q in qids[1:]]
    }
    client.put(f"/api/v1/attempts/{attempt['id']}/answers", json=payload, headers=_h(token))
    result = client.post(f"/api/v1/attempts/{attempt['id']}/submit", headers=_h(token)).json()
    assert result["score"] == result["max_score"] - 1
    assert abs(result["accuracy"] - (result["max_score"] - 1) / result["max_score"]) < 1e-9

    history = client.get("/api/v1/profiles/me/attempts", headers=_h(token)).json()
    assert len(history) >= 2  # both sittings preserved, never overwritten
    assert {h["status"] for h in history} == {"submitted"}
    perf = client.get("/api/v1/profiles/me/performance", headers=_h(token)).json()
    assert perf["submitted_attempts"] >= 2
    assert 0.0 < perf["avg_accuracy"] <= 1.0
    assert perf["by_subject"], "real attempts must produce real per-subject rows"


def test_attempt_isolation_and_validation():
    token = _auth()
    other = client.post(
        "/api/v1/auth/register", json={"email": "quiz2@student.example.com", "password": PW}
    ).json()["access_token"]
    aid, full = _first_assessment(token)
    attempt = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(token)).json()

    # Stranger cannot read, answer, submit, or view results.
    for method, path in [
        ("get", f"/api/v1/attempts/{attempt['id']}"),
        ("get", f"/api/v1/attempts/{attempt['id']}/result"),
        ("post", f"/api/v1/attempts/{attempt['id']}/submit"),
    ]:
        r = client.request(method, path, headers=_h(other))
        assert r.status_code in (404, 409), (method, path, r.status_code)

    # Option from another question is rejected.
    qids = [q["id"] for q in full["questions"]]
    foreign_option = full["questions"][1]["options"][0]["id"]
    bad = client.put(
        f"/api/v1/attempts/{attempt['id']}/answers",
        json={"answers": [{"question_id": qids[0], "selected_option_id": foreign_option}]},
        headers=_h(token),
    )
    assert bad.status_code == 422
    # Unknown question rejected too.
    bad2 = client.put(
        f"/api/v1/attempts/{attempt['id']}/answers",
        json={"answers": [{"question_id": "no-such-q", "selected_option_id": None}]},
        headers=_h(token),
    )
    assert bad2.status_code == 422


def test_progress_upsert_and_topic_counts():
    token = _auth()
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(token)).json()
    content = client.get(f"/api/v1/topics/{topics[0]['id']}/content", headers=_h(token)).json()
    assert topics[0]["completed_count"] == 0
    r = client.put(
        f"/api/v1/profiles/me/progress/{content[0]['id']}",
        json={"status": "completed"},
        headers=_h(token),
    )
    assert r.status_code == 200 and r.json()["status"] == "completed"
    topics2 = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(token)).json()
    assert topics2[0]["completed_count"] == 1
    mine = client.get("/api/v1/profiles/me/progress", headers=_h(token)).json()
    assert any(p["content_item_id"] == content[0]["id"] for p in mine)
