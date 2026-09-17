"""Explanation layer. Today: deterministic templates built from evidence rows.
LLMs are an approved FUTURE option for phrasing/conversation ONLY — they must
never invent evidence. The protocol below is the seam: swap TemplateExplainer
for an LLM implementation that receives the same evidence payload.

No LLM dependency is added in Phase 4A (nothing to call, nothing to fake).
"""

from typing import Protocol


class Explainer(Protocol):
    def explain(self, kind: str, context: dict) -> str: ...


class TemplateExplainer:
    """Deterministic, testable phrasing from structured context. Every sentence
    slot is filled from stored rows; unknown slots render as omissions, never guesses."""

    def explain(self, kind: str, context: dict) -> str:
        if kind == "weakness":
            return (
                f"{context['label']} sits at {context['mastery_pct']} across "
                f"{context['evidence']} answered questions. "
                f"Recent work: {context['recent']}. Prioritize it in your next study block."
            )
        if kind == "strength":
            return (
                f"{context['label']} holds at {context['mastery_pct']} across "
                f"{context['evidence']} answered questions. Keep it warm with spaced review."
            )
        if kind == "trend":
            return (
                f"Accuracy is {context['direction']} "
                f"(slope {context['slope']:+.2f} per attempt over the last {context['n']} graded sittings)."
            )
        if kind == "behavior":
            return context["text"]
        if kind == "risk":
            return (
                f"Projected next-attempt range {context['band']} with {context['confidence']} confidence. "
                f"{context['advice']}"
            )
        return context.get("text", "")
