"""Intelligence engine v4a.1: deterministic analytics over twin evidence.

No training, no black boxes: every output cites the rows behind it, carries a
confidence tied to evidence depth, and degrades to explicit insufficient-data
states. Prediction is a statistical BASELINE (documented, unvalidated) —
never presented as a validated forecast.
"""

import statistics as _stats
from datetime import UTC, datetime

from app.ai import careers as taxonomy
from app.ai.explain import TemplateExplainer

ENGINE_VERSION = "4a.1"
ALPHA = 0.35  # same EWMA family as topic mastery
WEAK_BELOW = 0.45
STRONG_ABOVE = 0.70
MIN_EVIDENCE_TOPIC = 2

_explainer = TemplateExplainer()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _confidence(evidence: int) -> str:
    if evidence >= 8:
        return "high"
    if evidence >= 4:
        return "medium"
    return "low"


def _topic_ev(topic_id: str, title: str, mastery: float, evidence: int) -> dict:
    return {
        "kind": "topic",
        "id": topic_id,
        "label": title,
        "detail": f"mastery {mastery:.0%} over {evidence} answers",
    }


def insights(features: dict) -> list[dict]:
    """Strength / weakness / trend / behavior / risk findings with evidence."""
    twin = features["twin"]
    out: list[dict] = []
    if twin["attempts_count"] == 0:
        return out

    for tid, t in twin["topics"].items():
        ev = t["evidence_count"]
        if ev < MIN_EVIDENCE_TOPIC:
            continue
        if t["mastery"] < WEAK_BELOW:
            seq = features.get("topic_recent", {}).get(tid, [])
            marks = " ".join("✓" if b else "✗" for b in seq)
            out.append(
                {
                    "type": "weakness",
                    "title": f"Weak area: {t['title']}",
                    "explanation": _explainer.explain(
                        "weakness",
                        {
                            "label": t["title"],
                            "mastery_pct": f"{t['mastery']:.0%}",
                            "evidence": ev,
                            "recent": (
                                f"last {len(seq)} answers on this topic: {marks}"
                                if seq
                                else "no graded work yet"
                            ),
                        },
                    ),
                    "evidence": [_topic_ev(tid, t["title"], t["mastery"], ev)],
                    "confidence": _confidence(ev),
                    "priority": 1 if t["mastery"] < 0.3 else 2,
                    "generated_at": _now_iso(),
                }
            )
        elif t["mastery"] > STRONG_ABOVE and ev >= 3:
            out.append(
                {
                    "type": "strength",
                    "title": f"Strength: {t['title']}",
                    "explanation": _explainer.explain(
                        "strength",
                        {
                            "label": t["title"],
                            "mastery_pct": f"{t['mastery']:.0%}",
                            "evidence": ev,
                        },
                    ),
                    "evidence": [_topic_ev(tid, t["title"], t["mastery"], ev)],
                    "confidence": _confidence(ev),
                    "priority": 4,
                    "generated_at": _now_iso(),
                }
            )

    if twin["trend_direction"] in ("improving", "declining"):
        out.append(
            {
                "type": "trend",
                "title": f"Performance {twin['trend_direction']}",
                "explanation": _explainer.explain(
                    "trend",
                    {
                        "direction": twin["trend_direction"],
                        "slope": twin["trend_slope"] or 0.0,
                        "n": min(len(features["accuracies"]), 10),
                    },
                ),
                "evidence": [
                    {
                        "kind": "attempts",
                        "id": "",
                        "label": "recent graded attempts",
                        "detail": f"{len(features['accuracies'])} sittings",
                    }
                ],
                "confidence": _confidence(len(features["accuracies"])),
                "priority": 2 if twin["trend_direction"] == "declining" else 4,
                "generated_at": _now_iso(),
            }
        )

    if features["in_progress_count"] > 0:
        out.append(
            {
                "type": "behavior",
                "title": "Unfinished sitting waiting",
                "explanation": _explainer.explain(
                    "behavior",
                    {
                        "text": f"{features['in_progress_count']} quiz attempt(s) started but not submitted. "
                        "Drafts are saved — resume from History to bank the evidence."
                    },
                ),
                "evidence": [],
                "confidence": "high",
                "priority": 2,
                "generated_at": _now_iso(),
            }
        )
    if features["overtime_count"] >= 2:
        out.append(
            {
                "type": "behavior",
                "title": "Pacing signal: frequent overtime",
                "explanation": _explainer.explain(
                    "behavior",
                    {
                        "text": f"{features['overtime_count']} sittings ran past the time limit. "
                        "Consider timed practice in short blocks; overtime is recorded, never punished."
                    },
                ),
                "evidence": [],
                "confidence": "medium",
                "priority": 3,
                "generated_at": _now_iso(),
            }
        )

    pred = predict(features)
    if pred["status"] == "ready" and pred["low"] is not None and pred["low"] < 0.5:
        out.append(
            {
                "type": "risk",
                "title": "At-risk band on next sitting",
                "explanation": _explainer.explain(
                    "risk",
                    {
                        "band": f"{pred['low']:.0%}–{pred['high']:.0%}",
                        "confidence": pred["confidence"],
                        "advice": "Short, focused revision on the weakest topic below usually moves this fastest.",
                    },
                ),
                "evidence": [
                    {
                        "kind": "prediction",
                        "id": "",
                        "label": "baseline projection",
                        "detail": f"expected {pred['expected']:.0%}",
                    }
                ],
                "confidence": pred["confidence"],
                "priority": 1,
                "generated_at": _now_iso(),
            }
        )

    return sorted(out, key=lambda i: (i["priority"], i["title"]))


def backtest_mae(accs: list[float]) -> tuple[float | None, int]:
    """Walk-forward one-step-ahead MAE of the baseline rule: for each sitting
    from the 3rd on, predict it from strictly earlier sittings and score the
    miss. Needs >=4 sittings for at least two test points. Pure, testable."""
    if len(accs) < 4:
        return None, 0
    errs = []
    for i in range(2, len(accs)):
        m = accs[0]
        for a in accs[1:i]:
            m = ALPHA * a + (1 - ALPHA) * m
        errs.append(abs((0.5 * accs[i - 1] + 0.5 * m) - accs[i]))
    return round(sum(errs) / len(errs), 3), len(errs)


def predict(features: dict) -> dict:
    """Baseline next-attempt projection. Method: 0.5*last + 0.5*EWMA(all),
    80% band from recent volatility. UNVALIDATED — see docs/ai-models.md."""
    accs = features["accuracies"]
    base = {"method": "baseline-ewma-4a.1", "engine": ENGINE_VERSION, "generated_at": _now_iso()}
    if len(accs) < 3:
        return {
            **base,
            "status": "insufficient_data",
            "requires": "at least 3 graded attempts",
            "expected": None,
            "low": None,
            "high": None,
            "confidence": None,
            "evidence_n": len(accs),
            "disclaimer": "No projection without a minimal track record.",
        }
    m = accs[0]
    for a in accs[1:]:
        m = ALPHA * a + (1 - ALPHA) * m
    expected = 0.5 * accs[-1] + 0.5 * m
    vol = _stats.stdev(accs[-8:]) if len(accs[-8:]) >= 2 else 0.0
    low, high = max(0.0, expected - 1.28 * vol), min(1.0, expected + 1.28 * vol)
    n = len(accs)
    bt_mae, bt_n = backtest_mae(accs)
    return {
        **base,
        "status": "ready",
        "expected": round(expected, 2),
        "low": round(low, 2),
        "high": round(high, 2),
        "confidence": "high" if n >= 8 else "medium" if n >= 5 else "low",
        "evidence_n": n,
        "backtest_mae": bt_mae,
        "backtest_n": bt_n,
        "disclaimer": (
            "Baseline statistical projection, not a validated forecast. "
            "Treat the band as uncertainty, not a promise."
        ),
    }


def recommendations(features: dict) -> dict:
    """Evidence-linked study actions. No evidence -> honest starter plan."""
    twin = features["twin"]
    recs: list[dict] = []
    lessons_by_topic: dict[str, list[dict]] = {}
    for lesson in features["incomplete_lessons"]:
        lessons_by_topic.setdefault(lesson["topic_id"], []).append(lesson)

    weak = sorted(
        [
            (tid, t)
            for tid, t in twin["topics"].items()
            if t["evidence_count"] >= MIN_EVIDENCE_TOPIC and t["mastery"] < WEAK_BELOW
        ],
        key=lambda kv: kv[1]["mastery"],
    )
    for tid, t in weak[:3]:
        step = (lessons_by_topic.get(tid) or [None])[0]
        recs.append(
            {
                "kind": "study_next" if step else "revise",
                "title": f"{'Study' if step else 'Revise'}: {t['title']}",
                "reason": f"Mastery {t['mastery']:.0%} over {t['evidence_count']} answers — lowest in your map.",
                "evidence": [_topic_ev(tid, t["title"], t["mastery"], t["evidence_count"])],
                "priority": 1,
                "est_minutes": (step["duration_minutes"] or 10) if step else 15,
                "refs": {"topic_id": tid, "content_id": step["id"] if step else None},
                "generated_at": _now_iso(),
            }
        )

    # V2-B2 compat: recommendations still use self-reported proficiency only;
    # assessed-only rows (proficiency None) are not self-reports. No-op on
    # pre-V2-B2 data where every row carries proficiency.
    _self_reported = {
        skid: sk for skid, sk in twin["skills"].items() if sk["proficiency"] is not None
    }
    for skid, sk in sorted(_self_reported.items(), key=lambda kv: kv[1]["proficiency"])[:2]:
        if sk["proficiency"] < 0.5:
            recs.append(
                {
                    "kind": "skill",
                    "title": f"Build skill: {sk['name']}",
                    "reason": f"Proficiency {sk['proficiency']:.0%} (stated level: {sk['level']}).",
                    "evidence": [
                        {
                            "kind": "skill",
                            "id": skid,
                            "label": sk["name"],
                            "detail": f"proficiency {sk['proficiency']:.0%}",
                        }
                    ],
                    "priority": 2,
                    "est_minutes": 20,
                    "refs": {"skill_id": skid},
                    "generated_at": _now_iso(),
                }
            )

    if not recs:
        if twin["attempts_count"] == 0:
            for lesson in features["incomplete_lessons"][:3]:
                recs.append(
                    {
                        "kind": "study_next",
                        "title": f"Start: {lesson['title']}",
                        "reason": "No graded evidence yet — this starter plan walks your enrolled topics in order.",
                        "evidence": [],
                        "priority": 1,
                        "est_minutes": lesson["duration_minutes"] or 10,
                        "refs": {"topic_id": lesson["topic_id"], "content_id": lesson["id"]},
                        "generated_at": _now_iso(),
                    }
                )
            if not recs:
                recs.append(
                    {
                        "kind": "study_next",
                        "title": "Take your first quiz",
                        "reason": "The engine needs graded evidence before it can personalize. Any quiz counts.",
                        "evidence": [],
                        "priority": 1,
                        "est_minutes": 10,
                        "refs": {},
                        "generated_at": _now_iso(),
                    }
                )
        else:
            recs.append(
                {
                    "kind": "revise",
                    "title": "Spaced review: your strongest topic",
                    "reason": "No weak areas right now — protect the lead with light review.",
                    "evidence": [],
                    "priority": 3,
                    "est_minutes": 10,
                    "refs": {},
                    "generated_at": _now_iso(),
                }
            )
    ordered = sorted(recs, key=lambda r: (r["priority"], r["title"]))
    return {
        "today": ordered[:3],
        "queue": ordered[3:],
        "engine": ENGINE_VERSION,
        "generated_at": _now_iso(),
    }


def career_matches(features: dict) -> dict:
    """Transparent fit scores vs the starter taxonomy. Scores are weighted means
    of matched masteries/proficiencies — every factor shown, gaps shown too."""
    twin = features["twin"]
    # compute_profile_twin returns id-keyed dicts (read_twin returns lists;
    # the engine always works from compute output for determinism).
    subj_mastery = {v["code"]: v["mastery"] for v in twin["subjects"].values()}
    subj_ev = {v["code"]: v["evidence_count"] for v in twin["subjects"].values()}
    skill_prof = {
        v["name"]: v["proficiency"] for v in twin["skills"].values() if v["proficiency"] is not None
    }

    results = []
    for c in taxonomy.CAREERS:
        num = den = matched = 0.0
        factors, missing = [], []
        for code, w in c["subjects"].items():
            den += w
            if code in subj_mastery:
                num += w * subj_mastery[code]
                matched += w
                factors.append(
                    {
                        "dimension": "subject",
                        "label": code,
                        "value": round(subj_mastery[code], 2),
                        "weight": w,
                        "evidence": subj_ev.get(code, 0),
                    }
                )
            else:
                missing.append(
                    {
                        "dimension": "subject",
                        "label": code,
                        "weight": w,
                        "hint": "no graded evidence in this subject yet",
                    }
                )
        for name, w in c["skills"].items():
            den += w
            if name in skill_prof:
                num += w * skill_prof[name]
                matched += w
                factors.append(
                    {
                        "dimension": "skill",
                        "label": name,
                        "value": round(skill_prof[name], 2),
                        "weight": w,
                        "evidence": None,
                    }
                )
            else:
                missing.append(
                    {
                        "dimension": "skill",
                        "label": name,
                        "weight": w,
                        "hint": "skill not on your profile yet",
                    }
                )
        coverage = matched / den if den else 0.0
        score = round(num / matched, 2) if matched else 0.0
        aligned = [
            g["title"]
            for g in features["goals"]
            if any(k in g["title"].lower() for k in c["keywords"])
        ]
        confidence = (
            "low"
            if coverage < 0.5 or twin["attempts_count"] < 3
            else "medium"
            if coverage < 0.8
            else "high"
        )
        results.append(
            {
                "career_id": c["id"],
                "title": c["title"],
                "blurb": c["blurb"],
                "score": score,
                "coverage": round(coverage, 2),
                "confidence": confidence,
                "factors": sorted(factors, key=lambda f: f["weight"], reverse=True)[:4],
                "missing": sorted(missing, key=lambda f: f["weight"], reverse=True)[:3],
                "aligned_goals": aligned,
                "generated_at": _now_iso(),
            }
        )
    results.sort(key=lambda r: (r["score"], r["coverage"]), reverse=True)
    return {
        "matches": results,
        "taxonomy_version": taxonomy.TAXONOMY_VERSION,
        "engine": ENGINE_VERSION,
        "generated_at": _now_iso(),
        "disclaimer": (
            "Exploratory fit signal, not a guaranteed outcome. Scores combine your demonstrated "
            "mastery with stated skills against a small starter taxonomy; coverage shows how "
            "much signal backs each score."
        ),
    }
