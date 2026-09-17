"""Mentor: deterministic, evidence-bound answers to a fixed question set.

This is NOT a conversational LLM — it cannot invent facts because it can only
render stored rows through templates. Free-text questions are rejected (404):
an LLM phrasing layer may sit in front later via the Explainer protocol, but
the evidence payload stays exactly this.
"""

from app.ai import engine

QUESTIONS = [
    ("performance_summary", "How am I doing overall?"),
    ("why_weak", "What is my biggest weakness and why?"),
    ("what_next", "What should I study next?"),
    ("priorities", "What are my top priorities?"),
    ("explain_prediction", "What does my projection mean?"),
    ("career_advice", "What career direction fits my evidence?"),
]

FOLLOW_UPS = {
    "performance_summary": ["why_weak", "what_next", "explain_prediction"],
    "why_weak": ["what_next", "priorities"],
    "what_next": ["priorities", "performance_summary"],
    "priorities": ["what_next", "career_advice"],
    "explain_prediction": ["priorities", "performance_summary"],
    "career_advice": ["what_next", "priorities"],
}


def _ev_topic(tid, t):
    return {
        "kind": "topic",
        "id": tid,
        "label": t["title"],
        "detail": f"mastery {t['mastery']:.0%} over {t['evidence_count']} answers",
    }


def answer(features: dict, key: str) -> dict:
    twin = features["twin"]
    if key not in dict(QUESTIONS):
        from app.core.errors import AppError

        raise AppError("unknown_question", "Mentor answers a fixed set of questions only.", 404)
    labels = dict(QUESTIONS)
    ev: list[dict] = []
    if key == "performance_summary":
        if twin["attempts_count"] == 0:
            text = (
                "No graded quizzes yet, so there is nothing to summarize. "
                "Take any quiz and I will have real numbers for you."
            )
        else:
            acc = twin["overall_accuracy"] or 0.0
            trend = twin["trend_direction"] or "unknown (needs 3+ sittings)"
            text = (
                f"Across {twin['attempts_count']} graded sittings you average {acc:.0%} accuracy, "
                f"overall mastery {twin['overall_mastery']:.0%}, trend {trend}. "
                f"Consistency reads {twin['consistency']:.0%}."
                if twin["consistency"] is not None
                else f"Across {twin['attempts_count']} graded sittings you average {acc:.0%} accuracy, "
                f"overall mastery {twin['overall_mastery']:.0%}, trend {trend}."
            )
    elif key == "why_weak":
        cands = [(tid, t) for tid, t in twin["topics"].items() if t["evidence_count"] >= 2]
        if not cands:
            text = "Nothing qualifies as a weakness yet — that needs a topic with 2+ answered questions below 45% mastery."
        else:
            tid, t = min(cands, key=lambda kv: kv[1]["mastery"])
            seq = features.get("topic_recent", {}).get(tid, [])
            marks = " ".join("✓" if b else "✗" for b in seq)
            text = (
                f"{t['title']} at {t['mastery']:.0%} over {t['evidence_count']} answers. "
                f"Recent on this topic: {marks}. "
                + (
                    "That is your lowest evidenced topic — start there."
                    if t["mastery"] < 0.45
                    else "Nothing is critically weak; this is simply your lowest area."
                )
            )
            ev = [_ev_topic(tid, t)]
    elif key == "what_next":
        plan = engine.recommendations(features)
        top = plan["today"][0] if plan["today"] else None
        if not top:
            text = "Nothing queued."
        else:
            text = f"{top['title']} (~{top['est_minutes']} min). {top['reason']}"
            ev = top["evidence"]
    elif key == "priorities":
        plan = engine.recommendations(features)
        items = plan["today"]
        if not items:
            text = "No priorities yet — take a quiz first."
        else:
            text = (
                "Your priorities: "
                + "; ".join(f"{i + 1}. {r['title']}" for i, r in enumerate(items))
                + "."
            )
            for r in items:
                ev.extend(r["evidence"])
    elif key == "explain_prediction":
        pred = engine.predict(features)
        if pred["status"] != "ready":
            text = f"No projection yet — {pred['requires']}. The band appears automatically once you qualify."
        else:
            bt = (
                f" On your own history this rule missed by ~{pred['backtest_mae']:.0%} on average."
                if pred["backtest_mae"] is not None
                else ""
            )
            text = (
                f"Projected next sitting: {pred['expected']:.0%}, plausibly {pred['low']:.0%}–{pred['high']:.0%} "
                f"({pred['confidence']} confidence, {pred['evidence_n']} sittings). "
                f"It blends your latest result with the longer trend — a baseline, not a forecast.{bt}"
            )
            ev = [
                {
                    "kind": "prediction",
                    "id": "",
                    "label": "baseline projection",
                    "detail": f"expected {pred['expected']:.0%}",
                }
            ]
    else:  # career_advice
        matches = engine.career_matches(features)["matches"]
        top = matches[0] if matches else None
        if not top or top["coverage"] == 0:
            text = (
                "No career reads yet — the signal starts once you have graded work and skills on your profile. "
                "Add a career goal and keep quizzing."
            )
        else:
            why = ", ".join(f"{f['label']} {f['value']:.0%}" for f in top["factors"][:3])
            text = (
                f"Strongest exploratory fit: {top['title']} ({top['score']:.0%} on {top['coverage']:.0%} signal, "
                f"{top['confidence']} confidence) — driven by {why}. Fit signal only, never an outcome."
            )
            ev = [
                {
                    "kind": "career",
                    "id": top["career_id"],
                    "label": top["title"],
                    "detail": f"fit {top['score']:.0%}, coverage {top['coverage']:.0%}",
                }
            ]
    return {
        "key": key,
        "question": labels[key],
        "answer": text,
        "evidence": ev,
        "follow_ups": [{"key": k, "question": labels[k]} for k in FOLLOW_UPS[key]],
    }
