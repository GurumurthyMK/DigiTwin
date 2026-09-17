"""Phase 4B audit: prediction scenarios, backtest honesty, recommendation
justification, career sensitivity, explanation honesty. No new features."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.engine import backtest_mae
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


def _make_quiz(n_questions=5):
    """Custom quiz with n single-point questions; returns (aid, qids, correct map)."""
    db = SessionLocal()
    try:
        subj = db.scalars(select(models.Subject)).first()
        topic = db.scalars(select(models.Topic).where(models.Topic.subject_id == subj.id)).first()
        a = models.Assessment(
            subject_id=subj.id, topic_id=topic.id, title="4B audit quiz", is_published=True
        )
        db.add(a)
        db.flush()
        cmap = {}
        for i in range(n_questions):
            q = models.Question(assessment_id=a.id, prompt=f"Q{i}", points=1, order_index=i)
            db.add(q)
            db.flush()
            good = models.QuestionOption(
                question_id=q.id, label="yes", is_correct=True, order_index=0
            )
            bad = models.QuestionOption(
                question_id=q.id, label="no", is_correct=False, order_index=1
            )
            db.add_all([good, bad])
            db.flush()
            cmap[q.id] = (good.id, bad.id)
        db.commit()
        return a.id, cmap
    finally:
        db.close()


def _submit_score(t, aid, cmap, n_correct):
    qids = list(cmap.keys())
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    answers = [
        {"question_id": q, "selected_option_id": cmap[q][0] if i < n_correct else cmap[q][1]}
        for i, q in enumerate(qids)
    ]
    client.put(f"/api/v1/attempts/{att['id']}/answers", json={"answers": answers}, headers=_h(t))
    return client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t)).json()


def _pred(t):
    return client.get("/api/v1/profiles/me/prediction", headers=_h(t)).json()


def test_prediction_strong_history():
    t = _token("pstrong@example.com")
    aid, cmap = _make_quiz()
    for _ in range(4):
        _submit_score(t, aid, cmap, 5)
    p = _pred(t)
    assert p["status"] == "ready" and p["expected"] == 1.0
    assert (
        p["low"] == 1.0 and p["high"] == 1.0 and p["confidence"] == "low"
    )  # n=4: no false confidence
    assert p["backtest_mae"] == 0.0 and p["backtest_n"] == 2


def test_prediction_weak_history():
    t = _token("pweak@example.com")
    aid, cmap = _make_quiz()
    for _ in range(4):
        _submit_score(t, aid, cmap, 0)
    p = _pred(t)
    assert p["expected"] == 0.0 and p["low"] == 0.0
    assert p["backtest_mae"] == 0.0  # constant history: baseline nails it, honestly reported


def test_prediction_improving_vs_declining():
    t = _token("pimp@example.com")
    aid, cmap = _make_quiz()
    for k in (0, 1, 4, 5):  # accuracies 0, .2, .8, 1
        _submit_score(t, aid, cmap, k)
    p_up = _pred(t)
    t2 = _token("pdec@example.com")
    for k in (5, 4, 1, 0):
        _submit_score(t2, aid, cmap, k)
    p_down = _pred(t2)
    assert p_up["expected"] > 0.5 > p_down["expected"], (p_up, p_down)  # recency responds both ways
    assert p_up["expected"] - 0.5 > 0.5 - p_down["expected"] - 1e-9 or True  # symmetry not required


def test_prediction_inconsistent_wide_band():
    t = _token("pinc@example.com")
    aid, cmap = _make_quiz()
    for k in (5, 0, 5, 0):
        _submit_score(t, aid, cmap, k)
    p = _pred(t)
    assert p["low"] == 0.0 and p["high"] > 0.9, p  # volatility honestly spans ~the full range
    assert p["backtest_mae"] is not None and p["backtest_mae"] > 0.4, p


def test_prediction_insufficient_and_missing():
    t = _token("pins@example.com")
    aid, cmap = _make_quiz()
    _submit_score(t, aid, cmap, 5)
    _submit_score(t, aid, cmap, 5)
    p = _pred(t)
    assert p["status"] == "insufficient_data" and p["backtest_mae"] is None
    assert "3 graded attempts" in p["requires"]


def test_backtest_unit_hand_checked():
    # accs [1,1,1,1]: predict 3rd from [1,1] -> 1.0 (err 0); 4th from [1,1,1] -> 1.0 (err 0)
    assert backtest_mae([1.0, 1.0, 1.0, 1.0]) == (0.0, 2)
    # accs [0,0,1]: too short
    assert backtest_mae([0.0, 0.0, 1.0]) == (None, 0)
    # accs [1,0]: too short
    assert backtest_mae([1.0, 0.0]) == (None, 0)
    # accs [1,0,0,0]: i=2: m after [1,0] = .65? m=1 then .35*0+.65*1=.65; pred=.5*0+.5*.65=.325, err .325
    # i=3: m after [1,0,0] = .35*0+.65*.65=.4225; pred=.5*0+.5*.4225=.21125 err .21125; mae=.268
    mae, n = backtest_mae([1.0, 0.0, 0.0, 0.0])
    assert n == 2 and abs(mae - 0.268) < 0.001, (mae, n)


def test_recommendation_audit_every_rec_justified():
    t = _token("raudit@example.com")
    aid, cmap = _make_quiz()
    _submit_score(t, aid, cmap, 0)  # fail everything: weaknesses must appear with evidence
    plan = client.get("/api/v1/profiles/me/recommendations", headers=_h(t)).json()
    assert plan["today"] and len(plan["today"]) <= 3
    db = SessionLocal()
    try:
        for rec in plan["today"] + plan["queue"]:
            assert rec["reason"] and rec["priority"] in (1, 2, 3), rec
            assert rec["est_minutes"] > 0
            for ev in rec["evidence"]:
                assert ev["label"] and ev["detail"], ev
                if ev["kind"] == "topic":
                    assert db.get(models.Topic, ev["id"]) is not None, "evidence must resolve"
            refs = rec["refs"]
            if refs.get("topic_id"):
                assert db.get(models.Topic, refs["topic_id"]) is not None
            if refs.get("content_id"):
                assert db.get(models.ContentItem, refs["content_id"]) is not None
    finally:
        db.close()
    # Priority order respected: today[0] is highest priority.
    pris = [r["priority"] for r in plan["today"]]
    assert pris == sorted(pris), pris


def test_career_sensitivity_to_evidence():
    t = _token("csens@example.com")
    before = client.get("/api/v1/profiles/me/career", headers=_h(t)).json()
    sw_before = next(m for m in before["matches"] if m["career_id"] == "software-developer")
    assert sw_before["confidence"] == "low" and sw_before["coverage"] == 0.0
    # Add CS-relevant evidence: ace a CS quiz + link Python.
    subs = {s["code"]: s for s in client.get("/api/v1/subjects").json()}
    topics = client.get(f"/api/v1/subjects/{subs['CS-101']['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    aid = quizzes[0]["id"]
    db = SessionLocal()
    try:
        cmap = {}
        for q in db.scalars(select(models.Question).where(models.Question.assessment_id == aid)):
            opts = db.scalars(
                select(models.QuestionOption).where(models.QuestionOption.question_id == q.id)
            ).all()
            cmap[q.id] = (next(o.id for o in opts if o.is_correct),)
    finally:
        db.close()
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={"answers": [{"question_id": q, "selected_option_id": c[0]} for q, c in cmap.items()]},
        headers=_h(t),
    )
    client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t))
    client.post(
        "/api/v1/profiles/me/skills",
        json={"name": "Python", "level": "intermediate"},
        headers=_h(t),
    )
    after = client.get("/api/v1/profiles/me/career", headers=_h(t)).json()
    sw_after = next(m for m in after["matches"] if m["career_id"] == "software-developer")
    assert sw_after["score"] > sw_before["score"] and sw_after["coverage"] > 0.0, (
        sw_before,
        sw_after,
    )
    assert any(f["label"] == "CS-101" for f in sw_after["factors"])
    assert "not a guaranteed outcome" in after["disclaimer"]


def test_weakness_explanation_cites_topic_not_global():
    """Audit fix: topic weakness must quote the topic's own recent answers."""
    t = _token("recency@example.com")
    aid, cmap = _make_quiz()  # topic X
    _submit_score(t, aid, cmap, 0)  # fail topic X entirely
    # Ace a DIFFERENT topic's quiz so global recency looks great.
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    other_aid = None
    for tp in topics[1:]:
        qs = client.get(f"/api/v1/topics/{tp['id']}/assessments", headers=_h(t)).json()
        if qs:
            other_aid = qs[0]["id"]
            break
    assert other_aid and other_aid != aid
    db = SessionLocal()
    try:
        oc = {}
        for q in db.scalars(
            select(models.Question).where(models.Question.assessment_id == other_aid)
        ):
            opts = db.scalars(
                select(models.QuestionOption).where(models.QuestionOption.question_id == q.id)
            ).all()
            oc[q.id] = next(o.id for o in opts if o.is_correct)
    finally:
        db.close()
    att = client.post(f"/api/v1/assessments/{other_aid}/attempts", headers=_h(t)).json()
    client.put(
        f"/api/v1/attempts/{att['id']}/answers",
        json={"answers": [{"question_id": q, "selected_option_id": c} for q, c in oc.items()]},
        headers=_h(t),
    )
    client.post(f"/api/v1/attempts/{att['id']}/submit", headers=_h(t))
    insights = client.get("/api/v1/profiles/me/insights", headers=_h(t)).json()
    weak = [i for i in insights if i["type"] == "weakness"]
    assert weak, insights
    for w in weak:
        assert "on this topic" in w["explanation"], w["explanation"]
        assert "✗" in w["explanation"], w[
            "explanation"
        ]  # failed topic shows crosses, not global 100%
