"""V2-B1 skill confidence: evidential support for the mastery estimate.

Confidence is NOT ability: identical counts with opposite outcomes share the
same confidence, while mastery differs. All exact values below follow
  confidence = round((1 - exp(-n/5)) * majority_share, 4).
"""

import math
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services.skill_mastery import (
    calculate_skill_confidence,
    calculate_skill_mastery,
    calculate_skill_state,
    skill_states_for_profile,
)

client = TestClient(app)
PW = "passWord123"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _row(skill: str, correct: bool, minutes: int, tag: str) -> models.SkillEvidence:
    return models.SkillEvidence(
        id=f"v2b1-{tag}-{skill}-{minutes}-{int(correct)}",
        profile_id="v2b1-profile",
        skill_id=skill,
        question_id="v2b1-q",
        attempt_id="v2b1-a",
        is_correct=correct,
        created_at=T0 + timedelta(minutes=minutes),
    )


def test_no_evidence_means_no_confidence():
    assert calculate_skill_confidence([]) is None
    assert calculate_skill_state("s", []) is None


def test_single_observation_is_low_and_symmetric():
    # One row cannot support much of a claim, whichever way it points.
    assert calculate_skill_confidence([True]) == 0.1813
    assert calculate_skill_confidence([False]) == 0.1813


def test_consistent_runs_grow_exact_values():
    assert calculate_skill_confidence([True] * 2) == 0.3297
    assert calculate_skill_confidence([True] * 3) == 0.4512
    assert calculate_skill_confidence([True] * 4) == 0.5507
    assert calculate_skill_confidence([True] * 5) == 0.6321
    assert calculate_skill_confidence([True] * 10) == 0.8647
    assert calculate_skill_confidence([True] * 20) == 0.9817


def test_consistent_beats_single_and_mixed_capped():
    assert calculate_skill_confidence([True] * 10) > calculate_skill_confidence([True])
    # High count with contradiction stays far from maximal.
    assert calculate_skill_confidence([True, False] * 10) == 0.4908
    assert calculate_skill_confidence([True, False] * 10) < calculate_skill_confidence([True] * 5)


def test_confidence_does_not_mirror_mastery():
    # Same counts, opposite outcomes: identical confidence, opposite mastery.
    assert calculate_skill_confidence([False] * 10) == calculate_skill_confidence([True] * 10)
    assert calculate_skill_mastery([False] * 10) < 0.5 < calculate_skill_mastery([True] * 10)
    # Neutral mastery can still carry meaningful support.
    assert calculate_skill_confidence([True, False] * 5) == 0.4323


def test_bounded_finite_everywhere():
    for seq in ([True] * 100, [False] * 100, [True, False] * 100, [True] * 1000):
        c = calculate_skill_confidence(seq)
        assert c is not None and 0.0 <= c <= 1.0 and math.isfinite(c)


def test_deterministic_under_reorder_and_ties():
    rows = [_row("s", c, m, "det") for m, c in enumerate([True, False, True, True])]
    assert calculate_skill_state("s", rows) == calculate_skill_state("s", list(reversed(rows)))
    tied = [_row("s", True, 0, "tie-a"), _row("s", False, 0, "tie-b")]
    assert calculate_skill_state("s", tied) == calculate_skill_state("s", list(reversed(tied)))


# ---- DB-backed behavior (real submits, unique users) ----


def _token(email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    return client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()[
        "access_token"
    ]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


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


def _states(email: str) -> dict:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        profile = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        )
        return skill_states_for_profile(db, profile.id)
    finally:
        db.close()


def _probe_reset(tag: str) -> None:
    db = SessionLocal()
    try:
        aids = [
            a.id
            for a in db.scalars(select(models.Assessment)).all()
            if a.title == f"V2-B1 probe {tag}"
        ]
        sids = [
            s.id
            for s in db.scalars(select(models.Skill)).all()
            if s.name.startswith(f"V2-B1-probe-{tag}-")
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
            title=f"V2-B1 probe {tag}",
            is_published=True,
            time_limit_seconds=300,
        )
        db.add(assessment)
        db.flush()
        skills = []
        for i in range(max(i for _, indexes in mapping for i in indexes) + 1):
            s = models.Skill(name=f"V2-B1-probe-{tag}-{i}", topic_id=topic.id)
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


def test_db_confidence_matches_pure_recomputation():
    email = "v2b1recompute@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    before = _states(email)
    assert before and all(s["confidence"] is not None for s in before.values())
    _submit(t, aid, pick_right=False)
    after = _states(email)
    assert set(after) == set(before)
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        profile = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        )
        for sid, state in after.items():
            rows = db.scalars(
                select(models.SkillEvidence).where(
                    models.SkillEvidence.skill_id == sid,
                    models.SkillEvidence.profile_id == profile.id,
                )
            ).all()
            assert state == calculate_skill_state(sid, rows)
    finally:
        db.close()


def test_mastery_unchanged_by_confidence():
    # V2-A4 values must survive the confidence addition byte-identically.
    assert calculate_skill_state("s", [_row("s", True, 0, "m")]) == {
        "skill_id": "s",
        "mastery": 0.75,
        "confidence": 0.1813,
        "evidence_count": 1,
        "correct_count": 1,
        "incorrect_count": 0,
        "trend": None,
        "trend_slope": None,
        "last_updated": T0.isoformat(),
    }


def test_multi_skill_confidence_independent():
    tag = "indep"
    try:
        aid, qmap = _probe_quiz(tag, [("Q-a correct?", [0]), ("Q-b correct?", [1])])
        qids = list(qmap)
        email = "v2b1indep@example.com"
        t = _token(email)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201, att.text
        omap = _options_map(aid)
        answers = [
            {"question_id": qids[0], "selected_option_id": omap[qids[0]]["right"]},
            {"question_id": qids[1], "selected_option_id": omap[qids[1]]["wrong"]},
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
            client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code
            == 200
        )
        states = _states(email)
        assert states[qmap[qids[0]][0]]["confidence"] == 0.1813
        assert states[qmap[qids[1]][0]]["confidence"] == 0.1813
        assert states[qmap[qids[0]][0]]["mastery"] == 0.75
        assert states[qmap[qids[1]][0]]["mastery"] == 0.25
    finally:
        _probe_reset(tag)


def test_unmapped_profile_and_topic_excluded():
    email = "v2b1excluded@example.com"
    t = _token(email)
    client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "advanced"}, headers=_h(t)
    )
    aid = _seeded_quiz(t)
    out = _submit(t, aid, pick_right=True)
    n = len(_options_map(aid))
    assert out["result"]["score"] == out["result"]["max_score"] == n
    states = _states(email)
    assert states, "mapped quiz must yield states"
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        profile = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        )
        python = db.scalar(select(models.Skill).where(models.Skill.name == "Python"))
        assert python.id not in states
        for sid, state in states.items():
            skill = db.get(models.Skill, sid)
            assert skill.topic_id is not None
            rows = db.scalars(
                select(models.SkillEvidence).where(
                    models.SkillEvidence.skill_id == sid,
                    models.SkillEvidence.profile_id == profile.id,
                )
            ).all()
            assert state == calculate_skill_state(sid, rows)
    finally:
        db.close()
    twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    by_name = {s["name"]: s for s in twin["skills"]}
    # V2-B2: self-report track intact; assessed taxonomy skills appear alongside.
    assert by_name["Python"]["source"] == "self_report"
    assert by_name["Python"]["level"] is not None
    assert by_name["Python"]["proficiency"] is not None
    assert any(s["source"] == "assessed" for s in twin["skills"])


def test_resubmit_keeps_confidence_stable():
    email = "v2b1resubmit@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    out = _submit(t, aid, pick_right=True)
    before = _states(email)
    again = client.post(f"/api/v1/attempts/{out['attempt_id']}/submit", headers=_h(t))
    assert again.status_code == 200 and again.json() == out["result"]
    assert _states(email) == before
