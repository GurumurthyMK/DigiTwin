"""Phase 2B adversarial tests: break the assessment engine before users do."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


def _quiz(t):
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    full = client.get(f"/api/v1/assessments/{quizzes[0]['id']}", headers=_h(t)).json()
    return quizzes[0]["id"], full


def _options(aid):
    db = SessionLocal()
    try:
        out = {}
        for q in db.scalars(
            select(models.Question)
            .where(models.Question.assessment_id == aid)
            .order_by(models.Question.order_index)
        ):
            opts = db.scalars(
                select(models.QuestionOption).where(models.QuestionOption.question_id == q.id)
            ).all()
            out[q.id] = {
                "correct": next(o.id for o in opts if o.is_correct),
                "wrong": next(o.id for o in opts if not o.is_correct),
                "points": q.points,
            }
        return out
    finally:
        db.close()


def test_zero_score_all_wrong():
    t = _token("zero@example.com")
    aid, _ = _quiz(t)
    info = _options(aid)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [
                {"question_id": q, "selected_option_id": v["wrong"]} for q, v in info.items()
            ]
        },
        headers=_h(t),
    )
    res = client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()
    assert res["score"] == 0 and res["accuracy"] == 0.0 and res["max_score"] > 0


def test_blank_submission_scores_zero_and_records_rows():
    t = _token("blank@example.com")
    aid, full = _quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    res = client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()
    assert res["score"] == 0 and res["accuracy"] == 0.0
    db = SessionLocal()
    try:
        rows = db.scalars(select(models.Answer).where(models.Answer.attempt_id == att["id"])).all()
        assert len(rows) == len(full["questions"]), "every question must leave a raw record"
        assert all(r.is_correct is False for r in rows)
    finally:
        db.close()


def test_reopen_unfinished_preserves_drafts():
    """Simulates page refresh / app kill: drafts must survive on the server."""
    t = _token("reopen@example.com")
    aid, _ = _quiz(t)
    info = _options(aid)
    first_q = next(iter(info))
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [{"question_id": first_q, "selected_option_id": info[first_q]["correct"]}]
        },
        headers=_h(t),
    )
    # Fresh GET = reopened client. Same sitting, draft intact, no correctness leak.
    reopened = client.get(f"/api/v1/attempts/{att['id']}", headers=_h(t)).json()
    assert reopened["id"] == att["id"] and reopened["status"] == "in_progress"
    assert reopened["selected"] == {first_q: info[first_q]["correct"]}
    assert "is_correct" not in str(reopened)


def test_single_active_attempt_enforced_at_db_level():
    """Double-tap Start must never fork two sittings: the DB is the backstop."""
    t = _token("race@example.com")
    aid, _ = _quiz(t)
    profile_id = client.get("/api/v1/profiles/me", headers=_h(t)).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            models.Attempt(
                assessment_id=aid,
                profile_id=profile_id,
                attempt_number=90,
                started_at=datetime.now(UTC),
            )
        )
        db.add(
            models.Attempt(
                assessment_id=aid,
                profile_id=profile_id,
                attempt_number=91,
                started_at=datetime.now(UTC),
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("two in_progress attempts accepted: start race possible")
        live = db.scalars(
            select(models.Attempt).where(
                models.Attempt.assessment_id == aid,
                models.Attempt.profile_id == profile_id,
                models.Attempt.status == "in_progress",
            )
        ).all()
        assert len(live) <= 1
    finally:
        db.close()


def test_overtime_recorded_not_punished():
    t = _token("overtime@example.com")
    aid, _ = _quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    db = SessionLocal()
    try:
        row = db.get(models.Attempt, att["id"])
        row.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        db.commit()
    finally:
        db.close()
    res = client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()
    assert res["overtime"] is True and res["time_taken_seconds"] >= 7000
    assert res["score"] is not None, "late submit still grades"


def test_weighted_multi_point_scoring():
    db = SessionLocal()
    try:
        subj = db.scalars(select(models.Subject)).first()
        a = models.Assessment(subject_id=subj.id, title="2B weighted", is_published=True)
        db.add(a)
        db.flush()
        q1 = models.Question(assessment_id=a.id, prompt="worth 2", points=2, order_index=0)
        q2 = models.Question(assessment_id=a.id, prompt="worth 3", points=3, order_index=1)
        db.add_all([q1, q2])
        db.flush()
        o1 = models.QuestionOption(question_id=q1.id, label="right", is_correct=True, order_index=0)
        o1b = models.QuestionOption(
            question_id=q1.id, label="wrong", is_correct=False, order_index=1
        )
        o2 = models.QuestionOption(
            question_id=q2.id, label="wrong", is_correct=False, order_index=0
        )
        o2b = models.QuestionOption(
            question_id=q2.id, label="right", is_correct=True, order_index=1
        )
        db.add_all([o1, o1b, o2, o2b])
        db.commit()
        aid, qids, right1 = a.id, (q1.id, q2.id), o1.id
    finally:
        db.close()
    t = _token("weighted@example.com")
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    db = SessionLocal()
    try:
        wrong2 = db.scalar(
            select(models.QuestionOption).where(
                models.QuestionOption.question_id == qids[1],
                models.QuestionOption.is_correct.is_(False),
            )
        ).id
    finally:
        db.close()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [
                {"question_id": qids[0], "selected_option_id": right1},
                {"question_id": qids[1], "selected_option_id": wrong2},
            ]
        },
        headers=_h(t),
    )
    res = client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()
    assert (res["score"], res["max_score"]) == (2, 5), res
    assert abs(res["accuracy"] - 0.4) < 1e-9


def test_cookie_and_header_transports_grade_identically():
    """Parity: cookie (web) vs header (mobile) must produce equivalent results."""
    hdr = _token("parity@example.com")
    aid, _ = _quiz(hdr)
    info = _options(aid)
    jar = TestClient(app)
    jar.post("/api/v1/auth/login", json={"email": "parity@example.com", "password": PW})
    att = jar.post(f"/api/v1/assessments/{aid}/attempts").json()
    assert att["status"] == "in_progress", att
    jar.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [
                {"question_id": q, "selected_option_id": v["correct"]} for q, v in info.items()
            ]
        },
    )
    cookie_res = jar.post(f"/api/v1/attempts/{att['id']}/submit").json()
    assert cookie_res["accuracy"] == 1.0

    hdr2 = _token("parity2@example.com")
    att2 = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(hdr2)).json()
    client.put(
        f"/api/v1/attempts/{att2['id']}/answers",
        json={
            "answers": [
                {"question_id": q, "selected_option_id": v["correct"]} for q, v in info.items()
            ]
        },
        headers=_h(hdr2),
    )
    header_res = client.post(f"/api/v1/attempts/{att2['id']}/submit", headers=_h(hdr2)).json()
    assert (header_res["score"], header_res["max_score"]) == (
        cookie_res["score"],
        cookie_res["max_score"],
    )


def test_performance_isolation_and_timestamps():
    a = _token("isoA@example.com")
    b = _token("isoB@example.com")
    aid, _ = _quiz(a)
    info = _options(aid)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(a)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={
            "answers": [
                {"question_id": q, "selected_option_id": v["correct"]} for q, v in info.items()
            ]
        },
        headers=_h(a),
    )
    res = client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(a)).json()
    assert res["submitted_at"] >= att["started_at"], "timestamps must be sane"
    assert res["time_taken_seconds"] >= 0

    perf_b = client.get("/api/v1/profiles/me/performance", headers=_h(b)).json()
    assert (
        perf_b["submitted_attempts"] == 0
        and perf_b["avg_accuracy"] is None
        and perf_b["by_subject"] == []
    )
    hist_b = client.get("/api/v1/profiles/me/attempts", headers=_h(b)).json()
    assert hist_b == []
    r1 = client.get(f"/api/v1/attempts/{att['id']}/result", headers=_h(a)).json()
    r2 = client.get(f"/api/v1/attempts/{att['id']}/result", headers=_h(a)).json()
    assert r1 == r2, "results must be stable reads"
