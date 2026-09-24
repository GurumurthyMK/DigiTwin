"""V2-C1 twin evolution: structured per-metric change history.

Events complement snapshots (rollup + opaque blob): one row per moved metric,
same >= 0.005 rule, same submit/profile transactions. No endpoint yet.
"""

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import models
from app.db.session import SessionLocal
from app.main import app
from app.services import twin_service

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


def _pid(email: str) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == email))
        return db.scalar(
            select(models.StudentProfile).where(models.StudentProfile.user_id == user.id)
        ).id
    finally:
        db.close()


def _events(email: str, limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        return twin_service.list_evolution_events(db, _pid(email), limit=limit)
    finally:
        db.close()


def _snaps(t: str) -> list[dict]:
    r = client.get("/api/v1/profiles/me/twin/snapshots", headers=_h(t))
    assert r.status_code == 200, r.text
    return r.json()


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


def _probe_reset(tag: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(models.User).where(models.User.email == f"v2c1{tag}@example.com"))
        if user is not None:
            # Cascades profile/attempts/evidence/snapshots/events at the DB level.
            db.delete(user)
            db.flush()
        aids = [
            a.id
            for a in db.scalars(select(models.Assessment)).all()
            if a.title == f"V2-C1 probe {tag}"
        ]
        sids = [
            s.id
            for s in db.scalars(select(models.Skill)).all()
            if s.name.startswith(f"V2-C1-probe-{tag}-")
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
            title=f"V2-C1 probe {tag}",
            is_published=True,
            time_limit_seconds=300,
        )
        db.add(assessment)
        db.flush()
        skills = []
        for i in range(max(i for _, indexes in mapping for i in indexes) + 1):
            s = models.Skill(name=f"V2-C1-probe-{tag}-{i}", topic_id=topic.id)
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


def _link(email: str, skill: str, level: str) -> None:
    t = _token(email)
    r = client.post(
        "/api/v1/profiles/me/skills", json={"name": skill, "level": level}, headers=_h(t)
    )
    assert r.status_code in (200, 201), r.text


def test_mastery_and_confidence_events_with_attempt_trace():
    tag = "mastery"
    try:
        aid, qmap = _probe_quiz(tag, [("Q mastery?", [0])])
        qid = next(iter(qmap))
        email = "v2c1mastery@example.com"
        t = _token(email)
        omap = _options_map(aid)
        first = _submit(t, aid, pick_right=True)
        # First appearance upserts silently: snapshot yes, skill events no.
        assert _events(email) == []
        assert len(_snaps(t)) == 1
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(t))
        assert att.status_code == 201, att.text
        assert (
            client.put(
                f"/api/v1/attempts/{att.json()['id']}/answers",
                json={"answers": [{"question_id": qid, "selected_option_id": omap[qid]["wrong"]}]},
                headers=_h(t),
            ).status_code
            == 200
        )
        second = client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
        assert second.status_code == 200
        evs = _events(email)
        mastery = [e for e in evs if e["metric"] == "mastery" and e["dimension"] == "skill"]
        assert len(mastery) == 1
        ev = mastery[0]
        assert (ev["old_value"], ev["new_value"]) == (0.75, 0.375)
        assert ev["attempt_id"] == att.json()["id"]
        assert ev["trigger_type"] == "assessment_submitted"
        assert ev["trigger_id"] == att.json()["id"]
        assert ev["ref"] == qmap[qid][0]
        conf = [e for e in evs if e["metric"] == "confidence"]
        assert len(conf) == 1
        assert (conf[0]["old_value"], conf[0]["new_value"]) == (0.1813, 0.1648)
        assert conf[0]["attempt_id"] == att.json()["id"]
        assert first["attempt_id"] != att.json()["id"]  # distinct source attempts
    finally:
        _probe_reset(tag)


def test_proficiency_event_on_self_report_level_change():
    email = "v2c1selfreport@example.com"
    t = _token(email)
    _link(email, "Python", "beginner")
    assert _events(email) == []  # first appearance: snapshot, no event
    assert len(_snaps(t)) == 1
    snap = _snaps(t)[0]
    assert snap["trigger_type"] == "profile_updated"
    _link(email, "Python", "advanced")  # 0.25 -> 0.75: meaningful move
    evs = _events(email)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["trigger_type"] == "profile_updated"
    assert ev["attempt_id"] is None  # self-report provenance: no attempt
    assert ev["dimension"] == "skill" and ev["metric"] == "proficiency"
    assert (ev["old_value"], ev["new_value"]) == (0.25, 0.75)


def test_trend_transition_recorded_with_labels():
    tag = "trend"
    try:
        aid, qmap = _probe_quiz(tag, [("Q trend?", [0])])
        qid = next(iter(qmap))
        email = "v2b1trendx@example.com"
        t = _token(email)
        omap = _options_map(aid)
        for pick in ("right", "right", "right", "wrong"):
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
        trends = [e for e in _events(email) if e["metric"] == "trend"]
        assert len(trends) == 1
        assert trends[0]["old_value"] is None and trends[0]["new_value"] is None
        assert (trends[0]["old_label"], trends[0]["new_label"]) == ("stable", "declining")
    finally:
        _probe_reset(tag)


def test_no_change_no_event_but_snapshot_exists():
    email = "v2c1nochange@example.com"
    t = _token(email)
    _link(email, "Python", "beginner")
    assert _events(email) == []
    _link(email, "Python", "beginner")  # identical level: recompute, no move
    assert _events(email) == []
    assert len(_snaps(t)) == 2  # snapshots follow the established always-append rule


def test_multi_metric_records_are_deterministic():
    tag = "multi"
    try:
        aid, _ = _probe_quiz(tag, [("Q-a?", [0]), ("Q-b?", [1])])
        email = "v2c1multi@example.com"
        t = _token(email)
        _submit(t, aid, pick_right=True)
        first = sorted(
            (e["dimension"], e["ref"], e["metric"], e["old_value"], e["new_value"])
            for e in _events(email)
        )
        _submit(t, aid, pick_right=False)
        both = sorted(
            (e["dimension"], e["ref"], e["metric"], e["old_value"], e["new_value"])
            for e in _events(email)
        )
        assert len(both) > len(first)  # second submit moved persisted state
        assert _events(email) == _events(email)  # stable read
        # Every record answers: which metric of which entity changed, from what.
        for dim, ref, metric, old, new in both:
            assert dim in ("subject", "topic", "skill")
            assert metric in ("mastery", "proficiency", "confidence")
            assert old is not None and new is not None and old != new
    finally:
        _probe_reset(tag)


def test_skill_attribution_and_profile_isolation():
    tag = "isolate"
    try:
        aid, qmap = _probe_quiz(tag, [("Q-a?", [0]), ("Q-b?", [1])])
        qids = list(qmap)
        ta = _token("v2c1isoa@example.com")
        _submit(ta, aid, pick_right=True)
        att = client.post(f"/api/v1/assessments/{aid}/attempts", headers=_h(ta))
        assert att.status_code == 201, att.text
        omap = _options_map(aid)
        assert (
            client.put(
                f"/api/v1/attempts/{att.json()['id']}/answers",
                json={
                    "answers": [
                        {
                            "question_id": qids[0],
                            "selected_option_id": omap[qids[0]]["right"],
                        },
                        {
                            "question_id": qids[1],
                            "selected_option_id": omap[qids[1]]["wrong"],
                        },
                    ]
                },
                headers=_h(ta),
            ).status_code
            == 200
        )
        assert (
            client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(ta)).status_code
            == 200
        )
        evs = _events("v2c1isoa@example.com")
        by_ref = {}
        for e in evs:
            by_ref.setdefault(e["ref"], set()).add(e["metric"])
        assert qmap[qids[0]][0] in by_ref and qmap[qids[1]][0] in by_ref
        assert all(e["attempt_id"] == att.json()["id"] for e in evs if e["dimension"] == "skill")
        tb = _token("v2c1isob@example.com")
        assert _events("v2c1isob@example.com") == []
        assert _twin_user(tb)["has_evidence"] is False
    finally:
        _probe_reset(tag)


def _twin_user(t: str) -> dict:
    r = client.get("/api/v1/profiles/me/twin", headers=_h(t))
    assert r.status_code == 200, r.text
    return r.json()


def test_ordering_and_limit():
    tag = "ordering"
    try:
        aid, _ = _probe_quiz(tag, [("Q order?", [0])])
        email = "v2c1ordering@example.com"
        t = _token(email)
        _submit(t, aid, pick_right=True)
        _submit(t, aid, pick_right=False)
        _submit(t, aid, pick_right=True)
        evs = _events(email)
        assert len(evs) >= 3
        stamps = [(e["created_at"], e["id"]) for e in evs]
        assert stamps == sorted(stamps, reverse=True)  # newest first
        assert len(_events(email, limit=2)) == 2
        assert len(_events(email, limit=1000)) == len(evs)  # clamped, complete
    finally:
        _probe_reset(tag)


def test_resubmit_creates_nothing_new():
    email = "v2c1resubmit@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    out = _submit(t, aid, pick_right=True)
    before_events = _events(email)
    before_snaps = _snaps(t)
    again = client.post(f"/api/v1/attempts/{out['attempt_id']}/submit", headers=_h(t))
    assert again.status_code == 200 and again.json() == out["result"]
    assert _events(email) == before_events
    assert _snaps(t) == before_snaps


def test_failed_transaction_rolls_back_events(monkeypatch):
    from app.services import twin_service

    def _boom(db, profile_id, computed, trigger_type, trigger_id):
        raise RuntimeError("persist exploded")

    monkeypatch.setattr(twin_service, "_persist", _boom)
    email = "v2c1rollback@example.com"
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
    snaps_before = _snaps(t)
    try:
        client.post(f"/api/v1/attempts/{att.json()['id']}/submit", headers=_h(t))
        raise AssertionError("submit should have raised")
    except RuntimeError:
        pass
    assert _events(email) == []
    assert _snaps(t) == snaps_before
    pid = _pid(email)
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
        assert db.get(models.Attempt, att.json()["id"]).status == "in_progress"
    finally:
        db.close()


def test_snapshots_and_aggregations_intact():
    email = "v2c1agg@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    out = _submit(t, aid, pick_right=True)
    assert out["result"]["score"] == out["result"]["max_score"]
    tw = _twin_user(t)
    ev = sum(s["evidence_count"] for s in tw["subjects"])
    expected = (
        round(sum(s["mastery"] * s["evidence_count"] for s in tw["subjects"]) / ev, 4)
        if ev
        else None
    )
    assert tw["overall_mastery"] == expected
    assert tw["topics"] and tw["subjects"]
    for snap in _snaps(t):
        assert set(snap) >= {"id", "trigger_type", "changes", "summary"}
        for change in snap["changes"]:
            assert set(change) == {"dimension", "ref", "label", "old", "new", "delta"}


def _scrub(node):
    if isinstance(node, dict):
        return {k: _scrub(v) for k, v in node.items() if k != "generated_at"}
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    return node


def test_ai_behavior_unchanged_and_deterministic():
    email = "v2c1ai@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    first = (
        client.get("/api/v1/profiles/me/insights", headers=_h(t)).json(),
        client.get("/api/v1/profiles/me/recommendations", headers=_h(t)).json(),
        client.get("/api/v1/profiles/me/prediction", headers=_h(t)).json(),
        client.get("/api/v1/profiles/me/career", headers=_h(t)).json(),
    )
    assert all(r is not None for r in first)
    second = (
        client.get("/api/v1/profiles/me/insights", headers=_h(t)).json(),
        client.get("/api/v1/profiles/me/recommendations", headers=_h(t)).json(),
        client.get("/api/v1/profiles/me/prediction", headers=_h(t)).json(),
        client.get("/api/v1/profiles/me/career", headers=_h(t)).json(),
    )
    assert _scrub(list(first)) == _scrub(list(second))


def test_history_readers_work_without_events():
    # Snapshots predating events (or with zero moves) read fine; the events
    # reader handles event-less profiles.
    email = "v2c1legacy@example.com"
    t = _token(email)
    aid = _seeded_quiz(t)
    _submit(t, aid, pick_right=True)
    assert len(_snaps(t)) == 1
    assert isinstance(_events(email), list)


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


def test_fresh_sqlite_migration_includes_events(tmp_path):
    url = f"sqlite:///{tmp_path}/fresh.db"
    _alembic(["upgrade", "head"], url)
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url)
    try:
        assert "twin_evolution_events" in inspect(eng).get_table_names()
        cols = {c["name"] for c in inspect(eng).get_columns("twin_evolution_events")}
        for col in (
            "profile_id",
            "snapshot_id",
            "created_at",
            "trigger_type",
            "trigger_id",
            "attempt_id",
            "dimension",
            "ref",
            "label",
            "metric",
            "old_value",
            "new_value",
            "old_label",
            "new_label",
        ):
            assert col in cols, col
    finally:
        eng.dispose()


def test_legacy_sqlite_upgrades_and_downgrades(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy.db"
    _alembic(["upgrade", "0009_twin_skill_evidence_state"], url)
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url)
    try:
        assert "twin_evolution_events" not in inspect(eng).get_table_names()
    finally:
        eng.dispose()
    _alembic(["upgrade", "head"], url)
    eng = create_engine(url)
    try:
        assert "twin_evolution_events" in inspect(eng).get_table_names()
    finally:
        eng.dispose()
    _alembic(["downgrade", "0009_twin_skill_evidence_state"], url)
    eng = create_engine(url)
    try:
        assert "twin_evolution_events" not in inspect(eng).get_table_names()
    finally:
        eng.dispose()
    _alembic(["upgrade", "head"], url)
