"""V2-A1 Skill Graph foundation: Skill reuse, Skill -> Topic, Question <-> Skill.

Data model only: no scoring, no twin changes, no API changes.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.seed import QUESTION_SKILL_MAP, SKILL_TAXONOMY, seed_reference_data
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


def test_skill_catalog_reuses_single_skill_concept():
    skills = client.get("/api/v1/skills").json()
    names = [s["name"] for s in skills]
    assert "Python" in names  # pre-existing V1 vocabulary still present
    assert len(names) == len(set(names)), "skill names must stay unique"
    for s in skills:
        assert set(s.keys()) == {"id", "name"}, s.keys()  # contract unchanged


def test_skill_topic_relationship():
    db = SessionLocal()
    try:
        skill = db.scalar(
            select(models.Skill).where(models.Skill.name == "Solving Linear Equations")
        )
        assert skill is not None and skill.topic_id is not None
        assert skill.topic is not None
        assert skill.topic.title == "Linear Equations"
        assert skill.topic.subject.code == "MATH-101"
        assert skill in skill.topic.skills
        # Pre-existing generic skills stay topic-less (student-declared vocabulary).
        python = db.scalar(select(models.Skill).where(models.Skill.name == "Python"))
        assert python is not None and python.topic_id is None
    finally:
        db.close()


def test_question_skill_mapping_present():
    db = SessionLocal()
    try:
        q = db.scalar(
            select(models.Question).where(models.Question.prompt == "If x + 7 = 15, what is x?")
        )
        assert q is not None
        linked = sorted(link.skill.name for link in q.skill_links)
        assert linked == ["Solving Linear Equations"], linked
    finally:
        db.close()


def test_duplicate_mapping_prevented():
    db = SessionLocal()
    try:
        edge = db.scalars(select(models.QuestionSkill)).first()
        assert edge is not None
        db.add(models.QuestionSkill(question_id=edge.question_id, skill_id=edge.skill_id))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_multiple_questions_share_one_skill():
    db = SessionLocal()
    try:
        skill = db.scalar(
            select(models.Skill).where(models.Skill.name == "Solving Linear Equations")
        )
        assert skill is not None and len(skill.question_links) == 3
    finally:
        db.close()


def test_question_supports_multiple_skills():
    db = SessionLocal()
    extra = models.Skill(name="V2-A1-probe-skill")
    db.add(extra)
    db.flush()
    q = db.scalar(
        select(models.Question).where(models.Question.prompt == "If x + 7 = 15, what is x?")
    )
    assert q is not None
    db.add(models.QuestionSkill(question_id=q.id, skill_id=extra.id))
    db.commit()
    try:
        names = sorted(link.skill.name for link in q.skill_links)
        assert names == ["Solving Linear Equations", "V2-A1-probe-skill"], names
    finally:
        db.delete(extra)  # FK cascade removes the probe edge, seed state untouched
        db.commit()
        db.close()


def test_seed_mapping_coverage():
    # Other suites create throwaway questions in the shared DB, so assert on
    # the seed-curriculum scope: every mapped prompt resolves to its skill.
    db = SessionLocal()
    try:
        assert len(SKILL_TAXONOMY) == 11
        assert len(QUESTION_SKILL_MAP) == 26
        for prompt, skill_name in QUESTION_SKILL_MAP.items():
            q = db.scalar(select(models.Question).where(models.Question.prompt == prompt))
            assert q is not None, prompt
            linked = sorted(link.skill.name for link in q.skill_links)
            assert linked == [skill_name], (prompt, linked)
    finally:
        db.close()


def test_submission_and_twin_unchanged():
    t = _token("skillgraph@example.com")
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(t)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(t)).json()
    att = client.post(f"/api/v1/assessments/{quizzes[0]['id']}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    # Answer every question with its first option, then submit (grading untouched).
    full = client.get(f"/api/v1/assessments/{quizzes[0]['id']}", headers=_h(t)).json()
    answers = [
        {"question_id": q["id"], "selected_option_id": q["options"][0]["id"]}
        for q in full["questions"]
    ]
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": answers}, headers=_h(t)
        ).status_code
        == 200
    )
    result = client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
    assert result.status_code == 200 and result.json()["max_score"] == len(answers)
    # V2-B2: no profile skills linked, so every twin skill is evidence-derived
    # (source "assessed", no self-report track) — never fabricated, never empty.
    twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    assert twin["has_evidence"] is True
    assert twin["skills"], "assessed skills must appear in the twin"
    for sk in twin["skills"]:
        assert sk["source"] == "assessed"
        assert sk["level"] is None and sk["proficiency"] is None
        assert sk["mastery"] is not None and sk["confidence"] is not None
        assert sk["skill_evidence_count"] > 0
    assert twin["subjects"] and twin["topics"]


def test_seed_idempotent():
    db = SessionLocal()
    try:
        before = (
            db.scalar(select(func.count()).select_from(models.Skill)),
            db.scalar(select(func.count()).select_from(models.QuestionSkill)),
            db.scalar(select(func.count()).select_from(models.Topic)),
            db.scalar(select(func.count()).select_from(models.Question)),
        )
    finally:
        db.close()
    seed_reference_data()
    db = SessionLocal()
    try:
        after = (
            db.scalar(select(func.count()).select_from(models.Skill)),
            db.scalar(select(func.count()).select_from(models.QuestionSkill)),
            db.scalar(select(func.count()).select_from(models.Topic)),
            db.scalar(select(func.count()).select_from(models.Question)),
        )
    finally:
        db.close()
    assert before == after, (before, after)


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


def test_fresh_sqlite_migration_to_head(tmp_path):
    url = f"sqlite:///{tmp_path}/fresh.db"
    _alembic(["upgrade", "head"], url)
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url)
    try:
        insp = inspect(eng)
        assert "question_skills" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("skills")}
        assert "topic_id" in cols
        uq = insp.get_unique_constraints("question_skills")
        assert any(sorted(c["column_names"]) == ["question_id", "skill_id"] for c in uq), uq
    finally:
        eng.dispose()


def test_existing_sqlite_upgrades_and_keeps_rows(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy.db"
    _alembic(["upgrade", "0006_account"], url)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    eng = create_engine(url)
    try:
        with Session(eng) as s:
            # Legacy-shape skill row, as written before V2-A1 existed.
            s.execute(
                models.Skill.__table__.insert().values(id="legacy-skill-1", name="Legacy Skill")
            )
            s.commit()
        _alembic(["upgrade", "head"], url)
        with Session(eng) as s:
            row = s.scalar(select(models.Skill).where(models.Skill.name == "Legacy Skill"))
            assert row is not None and row.topic_id is None
            assert s.scalar(select(func.count()).select_from(models.QuestionSkill)) == 0
    finally:
        eng.dispose()
