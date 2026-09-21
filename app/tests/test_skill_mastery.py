"""V2-A4 skill mastery engine: deterministic estimates from SkillEvidence.

Pure computation only — the Twin never reads these states (no integration).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services.skill_mastery import (
    calculate_skill_mastery,
    calculate_skill_state,
    calculate_skill_trend,
)

client = TestClient(app)
PW = "passWord123"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _row(skill: str, correct: bool, minutes: int, tag: str) -> models.SkillEvidence:
    return models.SkillEvidence(
        id=f"v2a4-{tag}-{skill}-{minutes}-{int(correct)}",
        profile_id="v2a4-profile",
        skill_id=skill,
        question_id="v2a4-q",
        attempt_id="v2a4-a",
        is_correct=correct,
        created_at=T0 + timedelta(minutes=minutes),
    )


def test_no_evidence_means_neutral_or_absent():
    assert calculate_skill_mastery([]) == 0.5
    assert calculate_skill_state("s", []) is None
    assert calculate_skill_trend([]) == (None, None)


def test_first_observation_exact_values():
    assert calculate_skill_mastery([True]) == 0.75
    assert calculate_skill_mastery([False]) == 0.25


def test_repeated_evidence_exact_values():
    assert calculate_skill_mastery([True, True]) == 0.875
    assert calculate_skill_mastery([True, True, True]) == 0.9375
    assert calculate_skill_mastery([False, False]) == 0.125
    assert calculate_skill_mastery([True, False]) == 0.375
    assert calculate_skill_mastery([False, True]) == 0.625
    assert calculate_skill_mastery([True, False, True, False]) == 0.3438


def test_recent_evidence_dominates_equal_counts():
    # Same 3:1 correct ratio; only order differs.
    assert calculate_skill_mastery([False, True, True, True]) == 0.9062
    assert calculate_skill_mastery([True, True, True, False]) == 0.4688
    assert calculate_skill_mastery([False, True, True, True]) > calculate_skill_mastery(
        [True, True, True, False]
    )


def test_mastery_bounded():
    assert calculate_skill_mastery([True] * 100) <= 1.0
    assert calculate_skill_mastery([False] * 100) >= 0.0
    seq = [True, False] * 100 + [True] * 50 + [False] * 50
    assert 0.0 <= calculate_skill_mastery(seq) <= 1.0


def test_trend_exact_values():
    assert calculate_skill_trend([False, False, False, True, True, True]) == (
        "improving",
        0.2571,
    )
    assert calculate_skill_trend([True, True, True, False, False, False]) == (
        "declining",
        -0.2571,
    )
    assert calculate_skill_trend([True, True, False, False, True, True]) == ("stable", 0.0)
    assert calculate_skill_trend([True, True]) == (None, None)
    assert calculate_skill_trend([True]) == (None, None)


def test_ordering_deterministic_under_ties_and_shuffles():
    rows = [_row("s", c, m, "tie") for m, c in enumerate([True, False, True])]
    same_stamp = [T0] * 3
    for r, stamp in zip(rows, same_stamp):
        r.created_at = stamp
    forward = calculate_skill_state("s", rows)
    backward = calculate_skill_state("s", list(reversed(rows)))
    assert forward == backward
    assert forward is not None and forward["evidence_count"] == 3
    # Same timestamp: id order decides, still deterministic.
    assert calculate_skill_state("s", rows) == calculate_skill_state("s", rows)


def test_state_fields_from_rows():
    rows = [_row("s", True, 0, "f"), _row("s", False, 5, "f"), _row("s", True, 10, "f")]
    state = calculate_skill_state("s", rows)
    assert state == {
        "skill_id": "s",
        "mastery": 0.6875,
        "confidence": 0.3008,  # (1 - exp(-3/5)) x 2/3
        "evidence_count": 3,
        "correct_count": 2,
        "incorrect_count": 1,
        "trend": "stable",
        "trend_slope": 0.0,
        "last_updated": (T0 + timedelta(minutes=10)).isoformat(),
    }


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
    assert result.json()["score"] == (result.json()["max_score"] if pick_right else 0)
    return {"attempt_id": att.json()["id"], "result": result.json()}


def _states(email: str) -> dict:
    from app.services.skill_mastery import skill_states_for_profile

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
    """Remove same-tag leftovers so reruns stay deterministic."""
    db = SessionLocal()
    try:
        aids = [
            a.id
            for a in db.scalars(select(models.Assessment)).all()
            if a.title == f"V2-A4 probe {tag}"
        ]
        sids = [
            s.id
            for s in db.scalars(select(models.Skill)).all()
            if s.name.startswith(f"V2-A4-probe-{tag}-")
        ]
        for ev in db.scalars(select(models.SkillEvidence)).all():
            if ev.attempt_id in {
                at.id for at in db.scalars(select(models.Attempt)).all() if at.assessment_id in aids
            }:
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
    """Temp published quiz. mapping: [(prompt, [skill_indexes])]. Returns
    (assessment_id, {question_id: [skill_ids]}). Caller must _probe_reset(tag)."""
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
            title=f"V2-A4 probe {tag}",
            is_published=True,
            time_limit_seconds=300,
        )
        db.add(assessment)
        db.flush()
        skills = []
        for i in range(max(i for _, indexes in mapping for i in indexes) + 1):
            s = models.Skill(name=f"V2-A4-probe-{tag}-{i}", topic_id=topic.id)
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


def test_db_states_match_pure_recomputation():
    email = "v2a4recompute@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    first = _states(email)
    assert first, "seeded quiz must yield evidence"
    # New evidence on the same quiz; only streams with new rows may change.
    _submit(t, aid, pick_right=False)
    second = _states(email)
    assert set(second) == set(first)
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        profile = db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        )
        for sid, state in second.items():
            rows = db.scalars(
                select(models.SkillEvidence).where(
                    models.SkillEvidence.skill_id == sid,
                    models.SkillEvidence.profile_id == profile.id,
                )
            ).all()
            assert state == calculate_skill_state(
                sid, sorted(rows, key=lambda r: (r.created_at, r.id))
            )
    finally:
        db.close()


def test_independent_streams_per_skill():
    tag = "indep"
    try:
        aid, qmap = _probe_quiz(tag, [("Q-a correct?", [0]), ("Q-b correct?", [1])])
        qids = list(qmap)
        email = "v2a4indep@example.com"
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
        assert set(states) == set(qmap[qids[0]]) | set(qmap[qids[1]])
        assert states[qmap[qids[0]][0]]["mastery"] == 0.75
        assert states[qmap[qids[1]][0]]["mastery"] == 0.25
    finally:
        _probe_reset(tag)


def test_unmapped_and_profile_skills_excluded():
    email = "v2a4excluded@example.com"
    t = _token(email)
    client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "advanced"}, headers=_h(t)
    )
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    states = _states(email)
    assert states, "mapped quiz must yield states"
    db = SessionLocal()
    try:
        python = db.scalar(select(models.Skill).where(models.Skill.name == "Python"))
        assert python.id not in states
        for sid in states:
            skill = db.get(models.Skill, sid)
            assert skill.topic_id is not None  # only curriculum-mapped skills
    finally:
        db.close()
    # Twin still sees only the self-report.
    twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    by_name = {s["name"]: s for s in twin["skills"]}
    # V2-B2: self-report track intact; assessed taxonomy skills appear alongside.
    assert by_name["Python"]["source"] == "self_report"
    assert by_name["Python"]["level"] is not None
    assert by_name["Python"]["proficiency"] is not None
    assert any(s["source"] == "assessed" for s in twin["skills"])


def test_submit_appends_one_row_per_mapped_skill():
    tag = "affonly"
    try:
        aid, qmap = _probe_quiz(tag, [("Q-a correct?", [0]), ("Q-b correct?", [1])])
        qids = list(qmap)
        email = "v2a4affonly@example.com"
        t = _token(email)
        omap = _options_map(aid)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201, att.text
        answers = [{"question_id": qid, "selected_option_id": omap[qid]["right"]} for qid in qids]
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
        # Second attempt answers only Q-a (Q-b left blank -> still graded).
        att2 = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att2.status_code == 201, att2.text
        assert (
            client.put(
                f"/api/v1/attempts/{att2.json()['id']}/answers",
                json={
                    "answers": [
                        {"question_id": qids[0], "selected_option_id": omap[qids[0]]["right"]},
                    ]
                },
                headers=_h(t),
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/v1/attempts/{att2.json()['id']}/submit", headers=_h(t)).status_code
            == 200
        )
        after = _states(email)
        assert after[qmap[qids[0]][0]]["mastery"] == 0.875  # T then T
        assert after[qmap[qids[1]][0]]["mastery"] == 0.375  # T then auto-graded F
    finally:
        _probe_reset(tag)


def test_twin_and_grading_unchanged():
    email = "v2a4twin@example.com"
    t = _token(email)
    client.post(
        "/api/v1/profiles/me/skills", json={"name": "Python", "level": "beginner"}, headers=_h(t)
    )
    aid = _seeded_quiz(t)
    out = _submit(t, aid, pick_right=True)
    n = len(_options_map(aid))
    assert out["result"]["score"] == out["result"]["max_score"] == n
    twin = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    assert twin["has_evidence"] is True
    by_name = {s["name"]: s for s in twin["skills"]}
    # V2-B2: self-report track intact; assessed taxonomy skills appear alongside.
    assert by_name["Python"]["source"] == "self_report"
    assert by_name["Python"]["level"] is not None
    assert by_name["Python"]["proficiency"] is not None
    assert any(s["source"] == "assessed" for s in twin["skills"])
