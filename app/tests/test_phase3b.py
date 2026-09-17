"""Phase 3B validation: does the twin BEHAVE like a twin?

Six controlled scenarios + calculation audit (hand-checked formulas, boundaries,
missing/insufficient data) + cross-student security.
"""

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


def _quiz_on_subject(t, subject_idx=0):
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[subject_idx]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    return subs[subject_idx]["code"], quizzes[0]["id"]


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


def _twin(t):
    return client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()


def _snaps(t):
    return client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t)).json()


def _topic_mastery(tw, title_part):
    match = [x for x in tw["topics"] if title_part.lower() in x["title"].lower()]
    assert match, f"no topic matching {title_part}"
    return match[0]["mastery"]


def test_scenario1_good_performance_improves_state():
    t = _token("s1@example.com")
    _, aid = _quiz_on_subject(t)
    before = _twin(t)["overall_mastery"]
    assert before is None
    _submit(t, aid, correct=True)
    tw = _twin(t)
    assert tw["overall_mastery"] > 0.5 and tw["overall_accuracy"] == 1.0
    assert tw["version"] == 1


def test_scenario2_poor_performance_deteriorates_state():
    t = _token("s2@example.com")
    _, aid = _quiz_on_subject(t)
    _submit(t, aid, correct=False)
    tw = _twin(t)
    assert tw["overall_mastery"] < 0.5 and tw["overall_accuracy"] == 0.0


def test_scenario3_sustained_good_performance():
    t = _token("s3@example.com")
    _, aid = _quiz_on_subject(t)
    levels = []
    for _ in range(3):
        _submit(t, aid, correct=True)
        levels.append(_twin(t)["overall_mastery"])
    assert levels[0] < levels[1] < levels[2], levels  # converges upward
    assert levels[2] > 0.9, levels  # sustained excellence saturates high
    tw = _twin(t)
    assert tw["trend_direction"] in ("improving", "stable")
    assert tw["consistency"] is not None and tw["consistency"] > 0.9


def test_scenario4_sustained_poor_performance():
    t = _token("s4@example.com")
    _, aid = _quiz_on_subject(t)
    levels = []
    for _ in range(3):
        _submit(t, aid, correct=False)
        levels.append(_twin(t)["overall_mastery"])
    assert levels[0] > levels[1] > levels[2], levels  # converges downward
    assert levels[2] < 0.1, levels  # sustained weakness saturates low
    tw = _twin(t)
    assert tw["trend_direction"] in ("declining", "stable")
    assert len(_snaps(t)) == 3  # every step traceable


def test_scenario5_no_new_data_no_change():
    t = _token("s5@example.com")
    _, aid = _quiz_on_subject(t)
    _submit(t, aid, correct=True)
    first, second = _twin(t), _twin(t)
    assert first == second, "reads must be pure: no drift without evidence"
    # Learning progress alone must not fabricate mastery movement.
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    content = client.get(f"/api/v1/topics/{topics[0]['id']}/content", headers=_h(t)).json()
    client.put(
        f"/api/v1/profiles/me/progress/{content[0]['id']}",
        json={"status": "completed"},
        headers=_h(t),
    )
    third = _twin(t)
    assert third["overall_mastery"] == first["overall_mastery"]
    assert third["version"] == first["version"], "no phantom twin versions"


def test_scenario6_unrelated_subjects_untouched():
    """Evidence in subject A must not move subject B: no invented cross-links."""
    t = _token("s6@example.com")
    code_a, aid = _quiz_on_subject(t, subject_idx=0)
    code_b, _ = _quiz_on_subject(t, subject_idx=1)
    assert code_a != code_b
    for _ in range(2):
        _submit(t, aid, correct=False)
    tw = _twin(t)
    codes = {s["code"] for s in tw["subjects"]}
    assert code_a in codes and code_b not in codes, codes
    snaps = _snaps(t)
    refs = {c["ref"] for s in snaps for c in s["changes"]}
    assert code_b not in refs and not any(
        code_b in (c.get("label") or "") for s in snaps for c in s["changes"]
    )


def test_history_traceability_and_timestamps():
    t = _token("hist@example.com")
    _, aid = _quiz_on_subject(t)
    _submit(t, aid, correct=True)
    _submit(t, aid, correct=False)
    snaps = _snaps(t)
    assert len(snaps) == 2 and snaps[0]["created_at"] >= snaps[1]["created_at"]
    for sn in snaps:
        assert sn["trigger_type"] == "assessment_submitted" and sn["trigger_id"]
        assert sn["overall_mastery"] is not None and sn["summary"]
    # Every snapshot trigger resolves to a real stored attempt.
    db = SessionLocal()
    try:
        for sn in snaps:
            assert db.get(models.Attempt, sn["trigger_id"]) is not None
    finally:
        db.close()
    # Raw attempts behind the twin are still all present.
    hist = client.get("/api/v1/profiles/me/attempts", headers=_h(t)).json()
    assert len(hist) == 2 and all(h["status"] == "submitted" for h in hist)


def test_calc_audit_ewma_hand_computed():
    """Single-question quiz: mastery after one correct must equal 0.35*1+0.65*0.5."""
    db = SessionLocal()
    try:
        subj = db.scalars(select(models.Subject)).first()
        topic = db.scalars(select(models.Topic).where(models.Topic.subject_id == subj.id)).first()
        a = models.Assessment(
            subject_id=subj.id, topic_id=topic.id, title="3B handcalc", is_published=True
        )
        db.add(a)
        db.flush()
        q = models.Question(assessment_id=a.id, prompt="2+2?", points=1, order_index=0)
        db.add(q)
        db.flush()
        db.add_all(
            [
                models.QuestionOption(question_id=q.id, label="4", is_correct=True, order_index=0),
                models.QuestionOption(question_id=q.id, label="5", is_correct=False, order_index=1),
            ]
        )
        db.commit()
        aid, qid = a.id, q.id
        topic_title = topic.title
        right = db.scalar(
            select(models.QuestionOption).where(
                models.QuestionOption.question_id == qid, models.QuestionOption.is_correct.is_(True)
            )
        ).id
    finally:
        db.close()
    t = _token("handcalc@example.com")
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={"answers": [{"question_id": qid, "selected_option_id": right}]},
        headers=_h(t),
    )
    client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t))
    m = _topic_mastery(_twin(t), topic_title)
    assert abs(m - 0.675) < 1e-3, m  # 0.35*1 + 0.65*0.5


def test_calc_audit_boundaries_and_missing_data():
    t = _token("bounds@example.com")
    tw = _twin(t)  # no data at all
    assert tw["consistency"] is None and tw["trend_direction"] is None and tw["trend_slope"] is None
    _, aid = _quiz_on_subject(t)
    _submit(t, aid, correct=True)
    tw = _twin(t)
    assert tw["consistency"] is None and tw["trend_direction"] is None  # depth gates hold
    _submit(t, aid, correct=True)  # identical accuracies -> zero variance
    tw = _twin(t)
    assert tw["consistency"] == 1.0, tw["consistency"]
    assert tw["trend_direction"] is None  # still <3 attempts: honestly unknown
    _submit(t, aid, correct=True)
    tw = _twin(t)
    assert tw["trend_direction"] == "stable"  # slope 0 within eps band


def test_twin_cross_student_security():
    a = _token("secA@example.com")
    b = _token("secB@example.com")
    _, aid = _quiz_on_subject(a)
    _submit(a, aid, correct=True)
    assert _twin(a)["has_evidence"] is True
    assert _twin(b)["has_evidence"] is False
    assert _snaps(b) == [] and len(_snaps(a)) == 1
    # B's performance/history reveal nothing of A.
    assert (
        client.get("/api/v1/profiles/me/performance", headers=_h(b)).json()["submitted_attempts"]
        == 0
    )
    assert client.get("/api/v1/profiles/me/attempts", headers=_h(b)).json() == []
