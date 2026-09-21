"""V2-A2: Question -> Skill mapping validation & evidence semantics.

Validation only: no scoring, no twin changes, no API changes.
"""

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import models
from app.db.seed import seed_reference_data
from app.db.session import SessionLocal
from app.main import app
from app.services import skill_graph

client = TestClient(app)
PW = "passWord123"


def _token(email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    return client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()[
        "access_token"
    ]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def test_audit_passes_on_seed():
    db = SessionLocal()
    try:
        assert skill_graph.audit_skill_graph(db) == []
    finally:
        db.close()


def test_every_edge_resolves_and_chains_to_subject():
    db = SessionLocal()
    try:
        edges = db.scalars(select(models.QuestionSkill)).all()
        assert len(edges) >= 26
        for edge in edges:
            assert edge.question is not None and edge.skill is not None
            skill, topic = edge.skill, edge.skill.topic
            assert topic is not None, f"mapped skill without topic: {skill.name}"
            assessment = edge.question.assessment
            assert assessment is not None
            if assessment.topic_id is not None:
                assert skill.topic_id == assessment.topic_id, (skill.name, topic.title)
                assert topic.subject_id == assessment.subject_id
            else:
                assert topic.subject_id == assessment.subject_id
            assert topic.subject is not None
    finally:
        db.close()


def test_validate_mapping_rejects_bad_edges():
    db = SessionLocal()
    try:
        assert skill_graph.validate_mapping(db, None, None) == ["unknown_question"]
        q = db.scalars(select(models.Question)).first()
        assert skill_graph.validate_mapping(db, q, None) == ["unknown_skill"]
        # Topic-less student-declared skill can never be mapping evidence.
        python = db.scalar(select(models.Skill).where(models.Skill.name == "Python"))
        assert skill_graph.validate_mapping(db, q, python) == ["skill_without_topic"]
        # Cross-topic skill: evidence would point at the wrong topic.
        other = db.scalar(select(models.Skill).where(models.Skill.name == "Sampling Methods"))
        assert skill_graph.validate_mapping(db, q, other) == ["topic_mismatch"]
    finally:
        db.close()


def test_multi_skill_evidence_has_no_weights():
    db = SessionLocal()
    try:
        q = db.scalar(select(models.Question).where(models.Question.prompt == "Mean of 2, 4, 6?"))
        assert q is not None
        assert [s.name for s in skill_graph.evidence_skills_for_question(db, q.id)] == [
            "Measures of Center"
        ]
        extra = models.Skill(name="V2-A2-probe-skill", topic_id=q.assessment.topic_id)
        db.add(extra)
        db.flush()
        db.add(models.QuestionSkill(question_id=q.id, skill_id=extra.id))
        db.commit()
        try:
            got = skill_graph.evidence_skills_for_question(db, q.id)
            assert [s.name for s in got] == ["Measures of Center", "V2-A2-probe-skill"]
            assert skill_graph.audit_skill_graph(db) == []  # same-topic edge is valid
        finally:
            db.delete(extra)  # cascade removes the probe edge, seed state untouched
            db.commit()
    finally:
        db.close()


def test_unmapped_question_stays_fully_valid():
    db = SessionLocal()
    try:
        subject = db.scalars(select(models.Subject)).first()
        topic = db.scalars(
            select(models.Topic).where(models.Topic.subject_id == subject.id)
        ).first()
        assessment = models.Assessment(
            subject_id=subject.id,
            topic_id=topic.id,
            title="V2-A2 probe quiz",
            is_published=True,  # attempts require published; deleted in finally below
            time_limit_seconds=300,
        )
        db.add(assessment)
        db.flush()
        q = models.Question(
            assessment_id=assessment.id,
            kind="single_choice",
            prompt="V2-A2 probe prompt?",
            points=1,
            order_index=0,
        )
        db.add(q)
        db.flush()
        good = models.QuestionOption(question_id=q.id, label="right", is_correct=True)
        bad = models.QuestionOption(question_id=q.id, label="wrong", is_correct=False)
        db.add_all([good, bad])
        db.commit()
        aid, qid, good_id = assessment.id, q.id, good.id
    finally:
        db.close()
    try:
        # No skill evidence, yet grading + twin flow work exactly as before.
        db = SessionLocal()
        try:
            assert skill_graph.evidence_skills_for_question(db, qid) == []
        finally:
            db.close()
        t = _token("v2a2probe@example.com")
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201, att.text
        assert (
            client.put(
                f"/api/v1/attempts/{att.json()['id']}/answers",
                json={"answers": [{"question_id": qid, "selected_option_id": good_id}]},
                headers=_h(t),
            ).status_code
            == 200
        )
        result = client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
        assert result.status_code == 200
        assert result.json()["score"] == 1 and result.json()["max_score"] == 1
        twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
        assert twin["has_evidence"] is True and twin["skills"] == []
    finally:
        db = SessionLocal()
        try:
            probe = db.get(models.Assessment, aid)
            if probe is not None:
                db.delete(probe)  # cascades question/options/attempts/answers
                db.commit()
        finally:
            db.close()


def test_skill_without_questions_stays_valid():
    db = SessionLocal()
    try:
        python = db.scalar(select(models.Skill).where(models.Skill.name == "Python"))
        assert python is not None and python.question_links == []
    finally:
        db.close()
    names = [s["name"] for s in client.get("/api/v1/skills").json()]
    assert "Python" in names and "Time Management" in names
    # Student self-report flow untouched.
    t = _token("v2a2selfreport@example.com")
    r = client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "advanced"}, headers=_h(t)
    )
    assert r.status_code in (200, 201), r.text


def test_twin_provenance_distinguishes_self_report_from_evidence():
    # V2-B2: the self-reported skill keeps its track AND assessed taxonomy
    # skills appear with derived state — provenance explicit on every row.
    t = _token("v2a2twin@example.com")
    client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "beginner"}, headers=_h(t)
    )
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    full = client.get(f"/api/v1/assessments/{quizzes[0]['id']}", headers=_h(t)).json()
    db = SessionLocal()
    try:
        correct = {}
        for q in full["questions"]:
            opts = db.scalars(
                select(models.QuestionOption).where(
                    models.QuestionOption.question_id == q["id"],
                    models.QuestionOption.is_correct.is_(True),
                )
            ).all()
            correct[q["id"]] = opts[0].id
    finally:
        db.close()
    att = client.post(f"/api/v1/assessments/{quizzes[0]['id']}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    answers = [{"question_id": qid, "selected_option_id": oid} for qid, oid in correct.items()]
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": answers}, headers=_h(t)
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
    )
    twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    by_name = {s["name"]: s for s in twin["skills"]}
    assert by_name["Python"]["source"] == "self_report"
    assert by_name["Python"]["mastery"] is None and by_name["Python"]["confidence"] is None
    assessed = [s for n, s in by_name.items() if n != "Python"]
    assert assessed, "evidence-backed taxonomy skills must appear"
    for sk in assessed:
        assert sk["source"] == "assessed"
        assert sk["level"] is None and sk["proficiency"] is None
        assert sk["mastery"] is not None and sk["confidence"] is not None


def test_seed_rerun_adds_no_edges():
    db = SessionLocal()
    try:
        before = db.scalar(select(func.count()).select_from(models.QuestionSkill))
    finally:
        db.close()
    seed_reference_data()
    db = SessionLocal()
    try:
        after = db.scalar(select(func.count()).select_from(models.QuestionSkill))
    finally:
        db.close()
    assert before == after
