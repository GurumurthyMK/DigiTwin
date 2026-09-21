"""V2-B2: evidence-derived skill state integrated into the Digital Twin.

Provenance (self_report | assessed | self_report+assessed) is explicit;
self-report track semantics and all other twin behavior are unchanged.
"""

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

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


def _twin(t: str) -> dict:
    r = client.get("/api/v1/profiles/me/twin", headers=_h(t))
    assert r.status_code == 200, r.text
    return r.json()


def _by_name(tw: dict) -> dict:
    return {s["name"]: s for s in tw["skills"]}


def _options_map(aid: str) -> dict[str, dict[str, str]]:
    db = SessionLocal()
    try:
        out = {}
        for q in db.scalars(select(models.Question).where(models.Question.assessment_id == aid)):
            by_correct = {o.is_correct: o.id for o in q.options}
            out[q.id] = {"right": by_correct[True], "wrong": by_correct[False]}
        return out
    finally:
        db.close()


def _seeded_quiz(token: str) -> str:
    subs = client.get("/api/v1/subjects").json()
    topics = client.get(f"/api/v1/subjects/{subs[0]['id']}/topics", headers=_h(token)).json()
    quizzes = client.get(f"/api/v1/topics/{topics[0]['id']}/assessments", headers=_h(token)).json()
    return quizzes[0]["id"]


def _submit(token: str, aid: str, pick_right: bool = True) -> dict:
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(token))
    assert att.status_code == 201, att.text
    omap = _options_map(aid)
    answers = [
        {"question_id": qid, "selected_option_id": opts["right" if pick_right else "wrong"]}
        for qid, opts in omap.items()
    ]
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


def _profile_id(email: str) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        return db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        ).id
    finally:
        db.close()


def _probe_reset(tag: str) -> None:
    db = SessionLocal()
    try:
        aids = [
            a.id
            for a in db.scalars(select(models.Assessment)).all()
            if a.title == f"V2-B2 probe {tag}"
        ]
        sids = [
            s.id
            for s in db.scalars(select(models.Skill)).all()
            if s.name.startswith(f"V2-B2-probe-{tag}-")
        ]
        attempt_ids = {
            at.id for at in db.scalars(select(models.Attempt)).all() if at.assessment_id in aids
        }
        for ev in db.scalars(select(models.SkillEvidence)).all():
            if ev.attempt_id in attempt_ids:
                db.delete(ev)
        db.flush()
        for e in db.scalars(select(models.QuestionSkill)).all():
            if e.skill_id in sids:
                db.delete(e)
        db.flush()
        for aid in aids:
            probe = db.get(models.Assessment, aid)
            if probe is not None:
                db.delete(probe)
        db.flush()
        for sid in sids:
            # Pre-existing V1 rule: Skill.profile_links has no delete cascade,
            # so drop self-report links before dropping a probe skill.
            for link in db.scalars(
                select(models.ProfileSkill).where(models.ProfileSkill.skill_id == sid)
            ).all():
                db.delete(link)
            skill = db.get(models.Skill, sid)
            if skill is not None:
                db.delete(skill)
        db.commit()
    finally:
        db.close()


def _probe_quiz(tag: str, mapping: list[tuple[str, list[int]]]) -> tuple[str, dict]:
    _probe_reset(tag)
    db = SessionLocal()
    try:
        subject = db.scalars(select(models.Subject)).first()
        topic = db.scalars(
            select(models.Topic).where(models.Topic.subject_id == subject.id)
        ).first()
        assessment = models.Assessment(
            subject_id=subject.id,
            topic_id=topic.id,
            title=f"V2-B2 probe {tag}",
            is_published=True,
            time_limit_seconds=300,
        )
        db.add(assessment)
        db.flush()
        skills = []
        for i in range(max(i for _, indexes in mapping for i in indexes) + 1):
            s = models.Skill(name=f"V2-B2-probe-{tag}-{i}", topic_id=topic.id)
            db.add(s)
            db.flush()
            skills.append(s)
        qmap = {}
        for order, (prompt, indexes) in enumerate(mapping):
            q = models.Question(
                assessment_id=assessment.id,
                kind="single_choice",
                prompt=prompt,
                points=1,
                order_index=order,
            )
            db.add(q)
            db.flush()
            db.add_all(
                [
                    models.QuestionOption(question_id=q.id, label="right", is_correct=True),
                    models.QuestionOption(question_id=q.id, label="wrong", is_correct=False),
                ]
            )
            for i in indexes:
                db.add(models.QuestionSkill(question_id=q.id, skill_id=skills[i].id))
            qmap[q.id] = [skills[i].id for i in indexes]
        db.commit()
        return assessment.id, qmap
    finally:
        db.close()


def test_self_report_without_evidence_preserved():
    # MANDATORY backward-compat regression: ProfileSkill with zero evidence.
    email = "v2b2legacy@example.com"
    t = _token(email)
    r = client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "advanced"}, headers=_h(t)
    )
    assert r.status_code in (200, 201), r.text
    tw = _twin(t)
    assert _by_name(tw)["Python"]["source"] == "self_report"
    py = _by_name(tw)["Python"]
    assert py["level"] == "advanced" and py["proficiency"] == 0.75
    assert py["mastery"] is None and py["confidence"] is None
    assert py["skill_evidence_count"] == 0
    assert py["correct_count"] == 0 and py["incorrect_count"] == 0
    assert py["trend"] is None and py["trend_slope"] is None and py["last_updated"] is None


def test_case_b_combined_sources():
    tag = "combined"
    try:
        aid, qmap = _probe_quiz(tag, [("Q combined?", [0])])
        qid = next(iter(qmap))
        skill_name = f"V2-B2-probe-{tag}-0"
        email = "v2b2combined@example.com"
        t = _token(email)
        r = client.post(
            "/api/v1/profiles/me/skills",
            json={"name": skill_name, "level": "intermediate"},
            headers=_h(t),
        )
        assert r.status_code in (200, 201), r.text
        omap = _options_map(aid)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201, att.text
        assert (
            client.put(
                f"/api/v1/attempts/{att.json()['id']}/answers",
                json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]},
                headers=_h(t),
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code
            == 200
        )
        sk = _by_name(_twin(t))[skill_name]
        assert sk["source"] == "self_report+assessed"
        assert sk["level"] == "intermediate" and sk["proficiency"] is not None
        assert sk["mastery"] == 0.75 and sk["confidence"] == 0.1813
        assert sk["skill_evidence_count"] == 1
    finally:
        _probe_reset(tag)


def test_case_c_assessed_only_discovery():
    email = "v2b2discovery@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    by_name = _by_name(_twin(t))
    assessed = [s for s in by_name.values() if s["source"] == "assessed"]
    assert assessed, "evidence-backed skills must be discovered"
    for sk in assessed:
        assert sk["level"] is None and sk["proficiency"] is None
        assert sk["mastery"] is not None and sk["confidence"] is not None
        assert sk["skill_evidence_count"] > 0


def test_correct_and_incorrect_move_derived_mastery():
    tag = "move"
    try:
        aid, qmap = _probe_quiz(tag, [("Q move?", [0])])
        qid = next(iter(qmap))
        email = "v2b2move@example.com"
        t = _token(email)
        omap = _options_map(aid)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201, att.text
        assert (
            client.put(
                f"/api/v1/attempts/{att.json()['id']}/answers",
                json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]},
                headers=_h(t),
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code
            == 200
        )
        first = _by_name(_twin(t))[f"V2-B2-probe-{tag}-0"]
        assert first["mastery"] == 0.75 and first["confidence"] == 0.1813
        att2 = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att2.status_code == 201, att2.text
        assert (
            client.put(
                f"/api/v1/attempts/{att2.json()['id']}/answers",
                json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["wrong"]}]},
                headers=_h(t),
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/v1/attempts/{att2.json()['id']}/submit", headers=_h(t)).status_code
            == 200
        )
        second = _by_name(_twin(t))[f"V2-B2-probe-{tag}-0"]
        assert second["mastery"] == 0.375
        assert second["skill_evidence_count"] == 2
        assert second["correct_count"] == 1 and second["incorrect_count"] == 1
    finally:
        _probe_reset(tag)


def test_trend_and_confidence_follow_evidence():
    tag = "trend"
    try:
        aid, qmap = _probe_quiz(tag, [("Q trend?", [0])])
        qid = next(iter(qmap))
        email = "v2b2trend@example.com"
        t = _token(email)
        omap = _options_map(aid)
        for pick in ("wrong", "wrong", "right", "right"):
            att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
            assert att.status_code == 201, att.text
            assert (
                client.put(
                    f"/api/v1/attempts/{att.json()['id']}/answers",
                    json={"answers": [{"question_id": qid, "selected_option_id": omap[qid][pick]}]},
                    headers=_h(t),
                ).status_code
                == 200
            )
            assert (
                client.post(
                    f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)
                ).status_code
                == 200
            )
        sk = _by_name(_twin(t))[f"V2-B2-probe-{tag}-0"]
        assert sk["mastery"] == 0.7812
        assert sk["confidence"] == 0.2753
        assert sk["trend"] == "improving"
    finally:
        _probe_reset(tag)


def test_only_affected_values_change_and_scoping_holds():
    tag = "scope"
    try:
        aid, qmap = _probe_quiz(tag, [("Q-a?", [0]), ("Q-b?", [1])])
        qids = list(qmap)
        email_a = "v2b2scopea@example.com"
        ta = _token(email_a)
        omap = _options_map(aid)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(ta))
        assert att.status_code == 201, att.text
        answers = [
            {"question_id": qids[0], "selected_option_id": omap[qids[0]]["right"]},
            {"question_id": qids[1], "selected_option_id": omap[qids[1]]["wrong"]},
        ]
        assert (
            client.put(
                f"/api/v1/attempts/{att.json()['id']}/answers",
                json={"answers": answers},
                headers=_h(ta),
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(ta)).status_code
            == 200
        )
        states = _by_name(_twin(ta))
        assert states[f"V2-B2-probe-{tag}-0"]["mastery"] == 0.75
        assert states[f"V2-B2-probe-{tag}-1"]["mastery"] == 0.25
        before_b = dict(states[f"V2-B2-probe-{tag}-1"])
        # Second student shares the taxonomy skill: strictly isolated.
        email_b = "v2b2scopeb@example.com"
        tb = _token(email_b)
        client.post(
            "/api/v1/profiles/me/skills",
            json={"name": f"V2-B2-probe-{tag}-1", "level": "advanced"},
            headers=_h(tb),
        )
        other = _by_name(_twin(tb))[f"V2-B2-probe-{tag}-1"]
        assert other["source"] == "self_report"
        assert other["mastery"] is None and other["confidence"] is None
        # First student's B row untouched by the second student's session.
        db = SessionLocal()
        try:
            pid_a = _profile_id(email_a)
            row = db.scalar(
                select(models.TwinSkillProficiency).where(
                    models.TwinSkillProficiency.profile_id == pid_a,
                    models.TwinSkillProficiency.skill_id == qmap[qids[1]][0],
                )
            )
            assert (row.mastery, row.confidence) == (before_b["mastery"], before_b["confidence"])
        finally:
            db.close()
    finally:
        _probe_reset(tag)


def _profile_id(email: str) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        return db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        ).id
    finally:
        db.close()


def test_unmapped_creates_no_twin_skill_state():
    tag = "unmapped"
    try:
        db = SessionLocal()
        try:
            subject = db.scalars(select(models.Subject)).first()
            topic = db.scalars(
                select(models.Topic).where(models.Topic.subject_id == subject.id)
            ).first()
            assessment = models.Assessment(
                subject_id=subject.id,
                topic_id=topic.id,
                title=f"V2-B2 probe {tag}",
                is_published=True,
                time_limit_seconds=300,
            )
            db.add(assessment)
            db.flush()
            q = models.Question(
                assessment_id=assessment.id,
                kind="single_choice",
                prompt="V2-B2 unmapped?",
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
        t = _token("v2b2unmapped@example.com")
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
        assert result.status_code == 200 and result.json()["score"] == 1
        assert _twin(t)["skills"] == []
    finally:
        _probe_reset(tag)


def test_resubmit_and_failure_leave_twin_stable():
    email = "v2b2stable@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    omap = _options_map(aid)
    answers = [
        {"question_id": qid, "selected_option_id": opts["right"]} for qid, opts in omap.items()
    ]
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers",
            json={"answers": answers},
            headers=_h(t),
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
    )
    before = _twin(t)
    snaps_before = client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t)).json()
    again = client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
    assert again.status_code == 200
    assert _twin(t) == before  # identical payload, no duplicate evolution
    snaps_after = client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t)).json()
    assert len(snaps_after) == len(snaps_before)
    for snap in snaps_after:
        for change in snap["changes"]:
            assert set(change) == {"dimension", "ref", "label", "old", "new", "delta"}


def test_failed_transaction_leaves_no_partial_skill_state(monkeypatch):
    from app.services import twin_service

    def _boom(db, profile_id, attempt):
        raise RuntimeError("persist exploded")

    monkeypatch.setattr(twin_service, "update_after_submit", _boom)
    email = "v2b2rollback@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
    assert att.status_code == 201, att.text
    omap = _options_map(aid)
    answers = [
        {"question_id": qid, "selected_option_id": opts["right"]} for qid, opts in omap.items()
    ]
    assert (
        client.put(
            f"/api/v1/attempts/{att.json()['id']}/answers",
            json={"answers": answers},
            headers=_h(t),
        ).status_code
        == 200
    )
    try:
        client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
        raise AssertionError("submit should have raised")
    except RuntimeError:
        pass
    pid = _profile_id(email)
    db = SessionLocal()
    try:
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.SkillEvidence)
                .where(models.SkillEvidence.profile_id == pid)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(models.TwinSkillProficiency)
                .where(models.TwinSkillProficiency.profile_id == pid)
            )
            == 0
        )
        assert db.get(models.Attempt, att.json()["id"]).status == "in_progress"
    finally:
        db.close()


def test_subject_topic_overall_derivation_intact():
    email = "v2b2agg@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit_pass = _submit(t, aid, pick_right=True)
    assert _submit_pass["result"]["score"] == _submit_pass["result"]["max_score"]
    tw = _twin(t)
    ev = sum(s["evidence_count"] for s in tw["subjects"])
    expected = (
        round(sum(s["mastery"] * s["evidence_count"] for s in tw["subjects"]) / ev, 4)
        if ev
        else None
    )
    assert tw["overall_mastery"] == expected
    assert tw["topics"] and all(x["evidence_count"] >= 0 for x in tw["topics"])


def test_dashboard_overview_stays_valid():
    email = "v2b2dash@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    r = client.get("/api/v1/profiles/me/overview", headers=_h(t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attempts_count"] >= 1 and "today" in body


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


def test_fresh_sqlite_migration_has_skill_state_columns(tmp_path):
    url = f"sqlite:///{tmp_path}/fresh.db"
    _alembic(["upgrade", "head"], url)
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url)
    try:
        cols = {c["name"] for c in inspect(eng).get_columns("twin_skill_proficiency")}
        for col in (
            "source",
            "mastery",
            "confidence",
            "skill_evidence_count",
            "correct_count",
            "incorrect_count",
            "trend",
            "trend_slope",
            "last_updated",
        ):
            assert col in cols, col
    finally:
        eng.dispose()


def test_legacy_rows_upgrade_with_self_report_backfill(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy.db"
    _alembic(["upgrade", "0008_skill_evidence"], url)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    eng = create_engine(url)
    try:
        from sqlalchemy import text

        with Session(eng) as s:
            # Legacy-shape row: only columns that existed at 0008.
            s.execute(
                text(
                    "INSERT INTO twin_skill_proficiency "
                    "(id, profile_id, skill_id, proficiency, evidence_count, "
                    "created_at, updated_at) VALUES "
                    "('legacy-twin-skill-1', 'legacy-profile-1', 'legacy-skill-1', "
                    "0.5, 4, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
                )
            )
            s.commit()
        _alembic(["upgrade", "head"], url)
        with Session(eng) as s:
            row = s.scalars(select(models.TwinSkillProficiency)).one()
            assert row.proficiency == 0.5 and row.evidence_count == 4
            assert row.source == "self_report"  # all pre-V2-B2 rows were link-derived
            assert row.mastery is None and row.confidence is None
            assert row.skill_evidence_count == 0
    finally:
        eng.dispose()
