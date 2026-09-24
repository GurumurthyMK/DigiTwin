"""Set 1: retention / evidence freshness + advanced twin analytics.

Deterministic, fixed-threshold, server-authoritative. No memory claims.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services import retention, skill_mastery, twin_analytics

client = TestClient(app)
PW = "passWord123"


def _token(email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PW})
    if r.status_code == 201:
        return r.json()["access_token"]
    return client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()["access_token"]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _pid(email: str) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        return db.scalar(select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)).id
    finally:
        db.close()


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


# Helper: probe quiz with explicit skill mapping for isolation

def _probe_reset(tag: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == f"set1{tag}@example.com"))
        if user is not None:
            db.delete(user)
            db.flush()
        aids = [
            a.id for a in db.scalars(select(models.Assessment)).all() if a.title == f"Set1 probe {tag}"
        ]
        sids = [
            s.id for s in db.scalars(select(models.Skill)).all() if s.name.startswith(f"Set1-probe-{tag}-")
        ]
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
            for link in db.scalars(select(models.ProfileSkill).where(models.ProfileSkill.skill_id == sid)).all():
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
        topic = db.scalars(select(models.Topic).where(models.Topic.subject_id == subject.id)).first()
        assessment = models.Assessment(
            subject_id=subject.id, topic_id=topic.id, title=f"Set1 probe {tag}", is_published=True, time_limit_seconds=300
        )
        db.add(assessment)
        db.flush()
        skills = []
        max_i = max((i for _, idxs in mapping for i in idxs), default=-1)
        for i in range(max_i + 1):
            s = models.Skill(name=f"Set1-probe-{tag}-{i}", topic_id=topic.id)
            db.add(s)
            db.flush()
            skills.append(s)
        qmap: dict[str, list[str]] = {}
        for order, (prompt, indexes) in enumerate(mapping):
            q = models.Question(assessment_id=assessment.id, kind="single_choice", prompt=prompt, points=1, order_index=order)
            db.add(q)
            db.flush()
            db.add_all([
                models.QuestionOption(question_id=q.id, label="right", is_correct=True),
                models.QuestionOption(question_id=q.id, label="wrong", is_correct=False),
            ])
            for i in indexes:
                db.add(models.QuestionSkill(question_id=q.id, skill_id=skills[i].id))
            qmap[q.id] = [skills[i].id for i in indexes]
        db.commit()
        return assessment.id, qmap
    finally:
        db.close()


def _backdate_evidence(profile_id: str, skill_id: str, days_ago: int, now: datetime) -> None:
    """Move all evidence rows for a skill to `now - days_ago`."""
    db = SessionLocal()
    try:
        target = now - timedelta(days=days_ago)
        for row in db.scalars(select(models.SkillEvidence).where(models.SkillEvidence.profile_id == profile_id, models.SkillEvidence.skill_id == skill_id)).all():
            row.created_at = target.replace(tzinfo=None)  # SQLite naive path
            # also bump updated_at irrelevant
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------
# Retention pure tests (no DB timing flake — injected now)
# ------------------------------------------------------------------

def test_retention_no_evidence_is_unknown():
    # Direct service: no rows => unknown, not 0%
    res = retention.freshness_for_skill_rows([], now=datetime(2026, 1, 10, tzinfo=UTC))
    assert res["retention_state"] == "unknown"
    assert res["retention_score"] is None
    assert res["days_since_last_evidence"] is None
    assert res["last_evidence_at"] is None
    # Same via API twin path: self-report-only skill shows unknown retention
    email = "set1noev@example.com"
    t = _token(email)
    client.post("/api/v1/profiles/me/skills", json={"name": "Python", "level": "beginner"}, headers=_h(t))
    tw = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    py = next(s for s in tw["skills"] if s["name"] == "Python")
    assert py["retention_state"] == "unknown"
    assert py["retention_score"] is None
    assert py["days_since_last_evidence"] is None
    assert py["last_evidence_at"] is None


def test_retention_fresh_evidence():
    tag = "fresh"
    try:
        aid, qmap = _probe_quiz(tag, [("Q fresh?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        # Use server clock for determinism: evidence 1 day ago from real now
        now = datetime.now(UTC)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201
        assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
        assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        _backdate_evidence(_pid(email), skill_id, 1, now)
        db = SessionLocal()
        try:
            got = retention.retention_for_skill(db, _pid(email), skill_id, now=now)
        finally:
            db.close()
        assert got["retention_state"] == "fresh"
        assert got["days_since_last_evidence"] == 1.0
        assert got["retention_score"] == round(1 - 1 / 60, 4)
        # Twin surface also fresh (uses same server clock within tolerance)
        tw = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
        sk = next(s for s in tw["skills"] if s["skill_id"] == skill_id)
        assert sk["retention_state"] == "fresh"
        assert sk["retention_score"] is not None and sk["retention_score"] > 0.8
    finally:
        _probe_reset(tag)


def test_retention_aging_evidence_fading():
    tag = "fading"
    try:
        aid, qmap = _probe_quiz(tag, [("Q fading?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        now = datetime(2026, 3, 10, tzinfo=UTC)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201
        assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
        assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        _backdate_evidence(_pid(email), skill_id, 15, now)
        db = SessionLocal()
        try:
            got = retention.retention_for_skill(db, _pid(email), skill_id, now=now)
        finally:
            db.close()
        assert got["retention_state"] == "fading"
        assert got["days_since_last_evidence"] == 15.0
        assert got["retention_score"] == round(1 - 15 / 60, 4) == 0.75
    finally:
        _probe_reset(tag)


def test_retention_stale_evidence():
    tag = "stale"
    try:
        aid, qmap = _probe_quiz(tag, [("Q stale?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        now = datetime(2026, 4, 1, tzinfo=UTC)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201
        assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
        assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        for days, expected_state in [(30, "stale"), (70, "very_stale")]:
            _backdate_evidence(_pid(email), skill_id, days, now)
            db = SessionLocal()
            try:
                got = retention.retention_for_skill(db, _pid(email), skill_id, now=now)
            finally:
                db.close()
            assert got["retention_state"] == expected_state, (days, got)
            if expected_state == "stale":
                assert got["retention_score"] == round(1 - days / 60, 4)
            else:
                assert got["retention_score"] == 0.0
    finally:
        _probe_reset(tag)


def test_retention_new_evidence_refreshes():
    tag = "refresh"
    try:
        aid, qmap = _probe_quiz(tag, [("Q refresh?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        base_now = datetime(2026, 5, 1, tzinfo=UTC)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201
        assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
        assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        _backdate_evidence(_pid(email), skill_id, 40, base_now)
        db = SessionLocal()
        try:
            before = retention.retention_for_skill(db, _pid(email), skill_id, now=base_now)
        finally:
            db.close()
        assert before["retention_state"] == "stale"
        # New evidence now
        later_now = base_now + timedelta(days=1)
        att2 = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att2.status_code == 201
        assert client.put(f"/api/v1/attempts/{att2.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
        assert client.post(f"/api/v1/attempts/{att2.json()['id']}/submit", headers=_h(t)).status_code == 200
        # Move only the NEW row to later_now (old rows stay old); freshness picks latest
        db = SessionLocal()
        try:
            # Find newest row for this profile/skill (max created_at)
            rows = db.scalars(select(models.SkillEvidence).where(models.SkillEvidence.profile_id == _pid(email), models.SkillEvidence.skill_id == skill_id)).all()
            latest = max(rows, key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC))
            latest.created_at = later_now.replace(tzinfo=None)
            db.commit()
            after = retention.retention_for_skill(db, _pid(email), skill_id, now=later_now)
        finally:
            db.close()
        assert after["retention_state"] == "fresh"
        assert after["days_since_last_evidence"] == 0.0
        assert after["retention_score"] == 1.0
    finally:
        _probe_reset(tag)


def test_retention_self_report_only_separate():
    email = "set1selfonly@example.com"
    t = _token(email)
    client.post("/api/v1/profiles/me/skills", json={"name": "Data Literacy", "level": "advanced"}, headers=_h(t))
    tw = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
    dl = next(s for s in tw["skills"] if s["name"] == "Data Literacy")
    assert dl["source"] == "self_report"
    assert dl["retention_state"] == "unknown"
    assert dl["retention_score"] is None
    assert dl["mastery"] is None  # no assessed mastery
    # Analytics also treats it as unknown freshness, not stale
    an = client.get("/api/v1/profiles/me/twin/analytics", headers=_h(t)).json()
    assert an["observed"]["taxonomy_skill_count"] >= 0
    # self_report-only skill still appears in graph coverage denominator but not in high_mastery_stale
    assert all(f["skill_id"] != dl["skill_id"] for f in an["interpretation"]["high_mastery_stale"])


def test_retention_repeated_evidence_does_not_break():
    tag = "repeat"
    try:
        aid, qmap = _probe_quiz(tag, [("Q repeat?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        for _ in range(3):
            att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
            assert att.status_code == 201
            assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
            assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        db = SessionLocal()
        try:
            state = retention.retention_for_skill(db, _pid(email), skill_id)
            mastery = skill_mastery.skill_states_for_profile(db, _pid(email))[skill_id]
        finally:
            db.close()
        assert state["retention_state"] == "fresh"
        assert state["evidence_count"] == 3
        # Mastery should be near 0.875 after 3 correct: 0.5->0.75->0.875? with alpha 0.5: 0.5->0.75 (1 correct), 0.75->0.875, 0.875->0.9375? Actually 0.5*(1)+0.5*0.5=0.75, then 0.5*1+0.5*0.75=0.875, then 0.9375
        assert mastery["mastery"] == 0.9375
        assert mastery["evidence_count"] == 3
        # Twin still fresh
        tw = client.get("/api/v1/profiles/me/twin", headers=_h(t)).json()
        assert any(s["skill_id"] == skill_id and s["retention_state"] == "fresh" for s in tw["skills"])
    finally:
        _probe_reset(tag)


def test_retention_multi_skill_question():
    tag = "multiskill"
    try:
        aid, qmap = _probe_quiz(tag, [("Q multi?", [0, 1])])
        qid = next(iter(qmap))
        sids = qmap[qid]
        assert len(sids) == 2
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201
        assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
        assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        db = SessionLocal()
        try:
            # Both skills get one row, same timestamp, same freshness
            for sid in sids:
                f = retention.retention_for_skill(db, _pid(email), sid)
                assert f["retention_state"] == "fresh"
                assert f["evidence_count"] == 1
            # Existing evidence semantics untouched: two rows for one answer
            rows = db.scalars(select(models.SkillEvidence).where(models.SkillEvidence.attempt_id == att.json()["id"])).all()
            assert len(rows) == 2
            assert set(r.skill_id for r in rows) == set(sids)
        finally:
            db.close()
    finally:
        _probe_reset(tag)


def test_analytics_high_mastery_stale_flag():
    tag = "highstale"
    try:
        aid, qmap = _probe_quiz(tag, [("Q high?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        # 3 correct submissions -> high mastery 0.9375
        for _ in range(3):
            att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
            assert att.status_code == 201
            assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["right"]}]}, headers=_h(t)).status_code == 200
            assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        now = datetime(2026, 6, 1, tzinfo=UTC)
        _backdate_evidence(_pid(email), skill_id, 45, now)
        db = SessionLocal()
        try:
            an = twin_analytics.build_analytics(db, _pid(email), now=now)
        finally:
            db.close()
        # Mastery high, stale -> flagged
        hit = [r for r in an["interpretation"]["high_mastery_stale"] if r["skill_id"] == skill_id]
        assert len(hit) == 1, hit
        assert hit[0]["retention_state"] == "stale"
        assert hit[0]["mastery"] == 0.9375
        # API also returns it
        api_an = client.get("/api/v1/profiles/me/twin/analytics", headers=_h(t)).json()
        assert "high_mastery_stale" in api_an["interpretation"]
        # Disclaimer present, never predictive
        assert "not a prediction" in api_an["interpretation"]["disclaimer"].lower()
    finally:
        _probe_reset(tag)


def test_analytics_low_mastery_recent_flag():
    tag = "lowrecent"
    try:
        aid, qmap = _probe_quiz(tag, [("Q low?", [0])])
        qid = next(iter(qmap))
        skill_id = qmap[qid][0]
        email = f"set1{tag}@example.com"
        t = _token(email)
        omap = _options_map(aid)
        # 3 incorrect -> low mastery ~0.0625
        now = datetime(2026, 6, 10, tzinfo=UTC)
        for _ in range(3):
            att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
            assert att.status_code == 201
            assert client.put(f"/api/v1/attempts/{att.json()['id']}/answers", json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["wrong"]}]}, headers=_h(t)).status_code == 200
            assert client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t)).status_code == 200
        # Backdate to 2 days ago so recent
        _backdate_evidence(_pid(email), skill_id, 2, now)
        db = SessionLocal()
        try:
            an = twin_analytics.build_analytics(db, _pid(email), now=now)
        finally:
            db.close()
        hit = [r for r in an["interpretation"]["low_mastery_recent"] if r["skill_id"] == skill_id]
        assert len(hit) == 1, hit
        assert hit[0]["retention_state"] in ("fresh", "fading")
        assert hit[0]["mastery"] is not None and hit[0]["mastery"] < 0.45
        # Also appears as needing reinforcement
        need = [r for r in an["interpretation"]["skills_needing_reinforcement"] if r["skill_id"] == skill_id]
        assert len(need) == 1
    finally:
        _probe_reset(tag)


def test_retention_boundary_thresholds():
    # Pure unit, no DB — verifies fixed documented thresholds.
    frozen = datetime(2026, 1, 15, tzinfo=UTC)
    cases = [
        (0, "fresh", 1.0),
        (7, "fresh", round(1 - 7 / 60, 4)),
        (8, "fading", round(1 - 8 / 60, 4)),
        (21, "fading", round(1 - 21 / 60, 4)),
        (22, "stale", round(1 - 22 / 60, 4)),
        (60, "stale", 0.0),
        (61, "very_stale", 0.0),
        (120, "very_stale", 0.0),
    ]
    for days, exp_state, exp_score in cases:
        state, score = retention.freshness_from_days(float(days))
        assert state == exp_state, (days, state, exp_state)
        assert score == exp_score, (days, score, exp_score)
    # None -> unknown
    state, score = retention.freshness_from_days(None)
    assert state == "unknown" and score is None
    # days_since helper clamps negative to 0, never negative
    last = frozen
    neg = retention._days_since(last, frozen - timedelta(days=5))  # future evidence
    assert neg == 0.0
    # Large history via freshness_for_skill_rows respects ordering (latest wins)
    db = SessionLocal()
    try:
        # Build synthetic rows with increasing timestamps
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        rows = []
        for i in range(3):
            r = models.SkillEvidence(
                profile_id="dummy", skill_id="dummy-skill", question_id="dummy-q", attempt_id=f"att-{i}", is_correct=True
            )
            r.created_at = (base + timedelta(days=i)).replace(tzinfo=None)  # naive
            r.id = f"row-{i:04d}"
            rows.append(r)
        out = retention.freshness_for_skill_rows(rows, now=base + timedelta(days=10))
        assert out["days_since_last_evidence"] == 8.0  # last at day2, now day10 -> 8
        assert out["retention_state"] == "fading"
    finally:
        db.close()


def test_analytics_observed_derived_interpretation_layers_and_coverage():
    email = "set1layers@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    an = client.get("/api/v1/profiles/me/twin/analytics", headers=_h(t)).json()
    assert set(an.keys()) == {"observed", "derived", "interpretation", "generated_at"}
    assert "attempts_count" in an["observed"] and an["observed"]["attempts_count"] >= 1
    assert "skill_freshness" in an["derived"] and len(an["derived"]["skill_freshness"]) >= 1
    assert "coverage" in an["derived"]
    cov = an["derived"]["coverage"]
    assert cov["coverage_ratio"] >= 0 and cov["coverage_ratio"] <= 1
    assert cov["taxonomy_skills"] >= 1
    assert "state_counts" in an["interpretation"]
    assert sum(an["interpretation"]["state_counts"].values()) >= 1
    # All retention states are from allowed set, never shown as 0%
    for sf in an["derived"]["skill_freshness"]:
        assert sf["retention_state"] in ("unknown", "fresh", "fading", "stale", "very_stale"), sf
        if sf["retention_state"] == "unknown":
            assert sf["retention_score"] is None
