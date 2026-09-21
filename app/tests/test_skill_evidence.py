"""V2-A3 skill evidence engine: submitted answers x mappings -> evidence rows.

Evidence only, no scoring: the Twin never reads these rows (V2-A4 owns that).
"""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)
PW = "passWord123"
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _token(email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    return client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()[
        "access_token"
    ]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _correct_map(aid: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        return {
            q.id: next(o.id for o in q.options if o.is_correct)
            for q in db.scalars(select(models.Question).where(models.Question.assessment_id == aid))
        }
    finally:
        db.close()


def _wrong_map(aid: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        return {
            q.id: next(o.id for o in q.options if not o.is_correct)
            for q in db.scalars(select(models.Question).where(models.Question.assessment_id == aid))
        }
    finally:
        db.close()


def _seeded_quiz(token: str) -> str:
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(token)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(token)).json()
    return quizzes[0]["id"]


def _submit(token: str, aid: str, picks: dict[str, str]) -> dict:
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(token))
    assert att.status_code == 201, att.text
    answers = [{"question_id": qid, "selected_option_id": oid} for qid, oid in picks.items()]
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers",
            json={"answers": answers},
            headers=_h(token),
        ).status_code
        == 200
    )
    result = client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(token))
    assert result.status_code == 200, result.text
    return {"attempt_id": att.json()["id"], "result": result.json()}


def _evidence_for_attempt(attempt_id: str) -> list[models.SkillEvidence]:
    db = SessionLocal()
    try:
        return db.scalars(
            select(models.SkillEvidence).where(models.SkillEvidence.attempt_id == attempt_id)
        ).all()
    finally:
        db.close()


def _profile_id_for(email: str) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        profile = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        )
        return profile.id
    finally:
        db.close()


def test_correct_submit_creates_positive_attributed_evidence():
    email = "v2a3correct@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    out = _submit(t, aid, _correct_map(aid))
    n = len(_correct_map(aid))
    assert out["result"]["score"] == out["result"]["max_score"] == n
    rows = _evidence_for_attempt(out["attempt_id"])
    assert len(rows) == n
    assert all(r.is_correct is True for r in rows)
    pid = _profile_id_for(email)
    assert all(r.profile_id == pid for r in rows)
    assert all(r.attempt_id == out["attempt_id"] for r in rows)
    assert {r.question_id for r in rows} == set(_correct_map(aid))
    db = SessionLocal()
    try:
        expected = sorted(
            link.skill.name
            for qid in _correct_map(aid)
            for link in db.scalars(
                select(models.QuestionSkill).where(models.QuestionSkill.question_id == qid)
            )
        )
        names = sorted(
            db.scalar(select(models.Skill).where(models.Skill.id == r.skill_id)).name for r in rows
        )
    finally:
        db.close()
    assert names == expected and len(names) == n


def test_incorrect_submit_creates_negative_evidence():
    email = "v2a3wrong@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    out = _submit(t, aid, _wrong_map(aid))
    n = len(_wrong_map(aid))
    assert out["result"]["score"] == 0 and out["result"]["max_score"] == n
    rows = _evidence_for_attempt(out["attempt_id"])
    assert len(rows) == n
    assert all(r.is_correct is False for r in rows)


def test_correctness_follows_server_grading_per_question():
    email = "v2a3mixed@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    correct, wrong = _correct_map(aid), _wrong_map(aid)
    qids = sorted(correct)
    # Client merely picks options; positivity follows authoritative grading.
    picks = {qids[0]: correct[qids[0]], qids[1]: wrong[qids[1]], qids[2]: wrong[qids[2]]}
    out = _submit(t, aid, picks)
    by_q = {r.question_id: r.is_correct for r in _evidence_for_attempt(out["attempt_id"])}
    assert by_q == {qids[0]: True, qids[1]: False, qids[2]: False}


def _probe_assessment(db, tag: str, n_skills: int, mapped: bool):
    """Temp published assessment + question (+ skills/edges). Caller cleans up."""
    subject = db.scalars(select(models.Subject)).first()
    topic = db.scalars(select(models.Topic).where(models.Topic.subject_id == subject.id)).first()
    assessment = models.Assessment(
        subject_id=subject.id,
        topic_id=topic.id,
        title=f"V2-A3 probe {tag}",
        is_published=True,
        time_limit_seconds=300,
    )
    db.add(assessment)
    db.flush()
    q = models.Question(
        assessment_id=assessment.id,
        kind="single_choice",
        prompt=f"V2-A3 probe {tag}?",
        points=1,
        order_index=0,
    )
    db.add(q)
    db.flush()
    good = models.QuestionOption(question_id=q.id, label="right", is_correct=True)
    bad = models.QuestionOption(question_id=q.id, label="wrong", is_correct=False)
    db.add_all([good, bad])
    skills = []
    if mapped:
        for i in range(n_skills):
            s = models.Skill(name=f"V2-A3-probe-{tag}-{i}", topic_id=topic.id)
            db.add(s)
            db.flush()
            db.add(models.QuestionSkill(question_id=q.id, skill_id=s.id))
            skills.append(s)
    db.commit()
    return assessment, q, good, bad, skills


def _cleanup_probe(aid: str, skill_ids: list[str]) -> None:
    db = SessionLocal()
    try:
        # Phased flushes: RESTRICT on skill_evidence.skill_id must see the
        # evidence deletes before the skill delete executes.
        for row in db.scalars(
            select(models.SkillEvidence).where(
                models.SkillEvidence.attempt_id.in_(
                    select(models.Attempt.id).where(models.Attempt.assessment_id == aid)
                )
            )
        ).all():
            db.delete(row)
        db.flush()
        for edge in db.scalars(
            select(models.QuestionSkill).where(models.QuestionSkill.skill_id.in_(skill_ids))
        ).all():
            db.delete(edge)
        db.flush()
        probe = db.get(models.Assessment, aid)
        if probe is not None:
            db.delete(probe)
        db.flush()
        for sid in skill_ids:
            skill = db.get(models.Skill, sid)
            if skill is not None:
                db.delete(skill)
        db.commit()
    finally:
        db.close()


def test_multi_skill_answer_creates_one_row_per_skill():
    db = SessionLocal()
    try:
        assessment, q, good, _, skills = _probe_assessment(db, "multi", 2, True)
        aid, qid, good_id = assessment.id, q.id, good.id
        sids = [s.id for s in skills]
    finally:
        db.close()
    try:
        t = _token("v2a3multi@example.com")
        out = _submit(t, aid, {qid: good_id})
        rows = _evidence_for_attempt(out["attempt_id"])
        assert len(rows) == 2
        assert sorted(r.skill_id for r in rows) == sorted(sids)
        assert all(r.is_correct is True for r in rows)  # same outcome, no weights
    finally:
        _cleanup_probe(aid, sids)


def test_unmapped_question_creates_zero_evidence():
    db = SessionLocal()
    try:
        assessment, q, good, _, _ = _probe_assessment(db, "unmapped", 0, False)
        aid, qid, good_id = assessment.id, q.id, good.id
    finally:
        db.close()
    try:
        t = _token("v2a3unmapped@example.com")
        out = _submit(t, aid, {qid: good_id})
        assert out["result"]["score"] == 1 and out["result"]["max_score"] == 1
        assert _evidence_for_attempt(out["attempt_id"]) == []
        twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
        assert twin["has_evidence"] is True
    finally:
        _cleanup_probe(aid, [])


def test_draft_and_abandoned_attempts_create_no_evidence():
    email = "v2a3draft@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    picks = _correct_map(aid)
    qid = min(picks)
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers",
            json={"answers": [{"question_id": qid, "selected_option_id": picks[qid]}]},
            headers=_h(t),
        ).status_code
        == 200
    )
    assert _evidence_for_attempt(att.json()["id"]) == []  # draft: nothing
    email2 = "v2a3abandoned@example.com"
    t2 = _token(email2)
    att2 = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t2))
    assert att2.status_code == 201, att2.text
    assert _evidence_for_attempt(att2.json()["id"]) == []  # abandoned: nothing


def test_resubmit_does_not_duplicate_evidence():
    email = "v2a3resubmit@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    out = _submit(t, aid, _correct_map(aid))
    key = lambda rows: sorted((r.attempt_id, r.question_id, r.skill_id, r.is_correct) for r in rows)
    before = key(_evidence_for_attempt(out["attempt_id"]))
    again = client.post(f"/api/v1/attempts/{out['attempt_id']}/submit", headers=_h(t))
    assert again.status_code == 200 and again.json() == out["result"]
    assert key(_evidence_for_attempt(out["attempt_id"])) == before
    assert len(before) == len(_correct_map(aid))


def test_profile_skill_alone_creates_no_evidence():
    email = "v2a3profile@example.com"
    t = _token(email)
    r = client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "advanced"}, headers=_h(t)
    )
    assert r.status_code in (200, 201), r.text
    aid = _seeded_quiz(t)
    out = _submit(t, aid, _correct_map(aid))
    rows = _evidence_for_attempt(out["attempt_id"])
    assert len(rows) == 3  # from QuestionSkill edges, not the self-report
    db = SessionLocal()
    try:
        python = db.scalar(select(models.Skill).where(models.Skill.name == "Python"))
        assert all(r.skill_id != python.id for r in rows)
    finally:
        db.close()


def test_edge_removal_preserves_historical_evidence():
    db = SessionLocal()
    try:
        assessment, q, good, _, skills = _probe_assessment(db, "edge", 1, True)
        aid, qid, good_id = assessment.id, q.id, good.id
        sids = [s.id for s in skills]
    finally:
        db.close()
    try:
        t = _token("v2a3edge@example.com")
        out = _submit(t, aid, {qid: good_id})
        assert len(_evidence_for_attempt(out["attempt_id"])) == 1
        db = SessionLocal()
        try:
            edge = db.scalar(
                select(models.QuestionSkill).where(models.QuestionSkill.question_id == qid)
            )
            assert edge is not None
            db.delete(edge)
            db.commit()
        finally:
            db.close()
        rows = _evidence_for_attempt(out["attempt_id"])
        assert len(rows) == 1  # history survives edge removal, still attributable
        assert rows[0].question_id == qid and rows[0].skill_id == sids[0]
    finally:
        _cleanup_probe(aid, sids)


def test_failed_submit_leaves_no_orphaned_evidence(monkeypatch):
    from app.services import twin_service

    def _boom(db, profile_id, attempt):
        raise RuntimeError("twin exploded")

    monkeypatch.setattr(twin_service, "update_after_submit", _boom)
    email = "v2a3rollback@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    picks = _correct_map(aid)
    answers = [{"question_id": qid, "selected_option_id": oid} for qid, oid in picks.items()]
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers",
            json={"answers": answers},
            headers=_h(t),
        ).status_code
        == 200
    )
    with pytest.raises(RuntimeError, match="twin exploded"):
        client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
    # Transaction rolled back: attempt still open, grades unstamped, no evidence.
    assert _evidence_for_attempt(att.json()["id"]) == []
    db = SessionLocal()
    try:
        attempt = db.get(models.Attempt, att.json()["id"])
        assert attempt.status == "in_progress"
        assert all(a.is_correct is None for a in attempt.answers)
    finally:
        db.close()


def test_twin_output_ignores_skill_evidence():
    email = "v2a3twin@example.com"
    t = _token(email)
    client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "beginner"}, headers=_h(t)
    )
    aid = _seeded_quiz(t)
    _submit(t, aid, _correct_map(aid))
    twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    assert twin["has_evidence"] is True
    by_name = {s["name"]: s for s in twin["skills"]}
    # V2-B2: self-report track intact; assessed taxonomy skills appear alongside.
    assert by_name["Python"]["source"] == "self_report"
    assert by_name["Python"]["level"] is not None
    assert by_name["Python"]["proficiency"] is not None
    assert any(s["source"] == "assessed" for s in twin["skills"])


def test_evidence_constraints_and_indexes():
    db = SessionLocal()
    try:
        row = db.scalars(select(models.SkillEvidence)).first()
        assert row is not None
        db.add(
            models.SkillEvidence(
                profile_id=row.profile_id,
                skill_id=row.skill_id,
                question_id=row.question_id,
                attempt_id=row.attempt_id,
                is_correct=not row.is_correct,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(
            models.SkillEvidence(
                profile_id=row.profile_id,
                skill_id="no-such-skill",
                question_id=row.question_id,
                attempt_id=row.attempt_id,
                is_correct=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
    from sqlalchemy import inspect

    from app.db.session import engine

    insp = inspect(engine)
    idx_cols = {tuple(sorted(c["column_names"])) for c in insp.get_indexes("skill_evidence")}
    for col in ("profile_id", "skill_id", "question_id", "attempt_id"):
        assert (col,) in idx_cols, idx_cols
    uq = insp.get_unique_constraints("skill_evidence")
    assert any(
        sorted(c["column_names"]) == ["attempt_id", "question_id", "skill_id"] for c in uq
    ), uq


def _alembic(args: list[str], db_url: str) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={"DATABASE_URL": db_url, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stderr


def test_fresh_sqlite_migration_includes_evidence(tmp_path):
    url = f"sqlite:///{tmp_path}/fresh.db"
    _alembic(["upgrade", "head"], url)
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url)
    try:
        assert "skill_evidence" in inspect(eng).get_table_names()
    finally:
        eng.dispose()


def test_legacy_sqlite_upgrades_to_evidence(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy.db"
    _alembic(["upgrade", "0007_skill_graph"], url)
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url)
    try:
        assert "skill_evidence" not in inspect(eng).get_table_names()
    finally:
        eng.dispose()
    _alembic(["upgrade", "head"], url)
    eng = create_engine(url)
    try:
        assert "skill_evidence" in inspect(eng).get_table_names()
    finally:
        eng.dispose()
