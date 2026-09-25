"""Constrained rephrasing: the one step that uses a model.

What the model may do: reword a bullet that is already on the page so it uses
the job description's vocabulary. What it may not do: add a bullet, drop one,
change a number, or claim a skill. None of that is enforced by asking nicely —
`verify.check_selection` runs on the result, and a response that fails it is
discarded in favour of the original text.

The client is injected, so every test here runs offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import BaseModel, Field

from resume.schema import Profile
from resume.select import Selection
from resume.verify import Problem, check_selection

DEFAULT_MODEL = "claude-opus-5"

SYSTEM = """You rewrite resume bullet points so they use a job description's own \
vocabulary, without changing what they claim.

Rules, in order of importance:
1. Never introduce a fact. No new employer, technology, metric, date or outcome.
2. Never change a number. Every digit in your output must appear in the input \
bullet. Do not derive new figures (a change from 820ms to 190ms is not "a 77% \
improvement" unless the input says so).
3. Only use a job-description term when the input bullet already describes that \
thing under another name. "Built a lead-generation web app" may become "shipped a \
customer-facing web application" if the posting says "customer-facing"; it may not \
become "built a distributed system".
4. Keep each bullet one sentence or two, and keep its `fact_id` unchanged.
5. If a bullet is already well aimed at the posting, return it unchanged. \
Returning the input verbatim is a correct answer.

Return every bullet you were given, exactly once."""


class RewrittenBullet(BaseModel):
    fact_id: str = Field(description="unchanged, exactly as given")
    bullet: str = Field(description="the rewritten text, or the original if already apt")


class Rewrite(BaseModel):
    bullets: list[RewrittenBullet]


@dataclass
class RephraseResult:
    selection: Selection
    changed: int = 0
    used_model: bool = False
    rejected: list[Problem] = None  # type: ignore[assignment]
    note: str = ""

    def __post_init__(self) -> None:
        if self.rejected is None:
            self.rejected = []


def available() -> bool:
    """True when both the SDK and a credential are present."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _build_prompt(selection: Selection, target_terms: list[str], title: str) -> str:
    lines = [
        f"Job title: {title}",
        f"Vocabulary the posting uses: {', '.join(sorted(target_terms)) or '(none extracted)'}",
        "",
        "Bullets to consider:",
    ]
    for bullet in selection.bullets:
        lines.append(f"- [{bullet.fact_id}] {bullet.text}")
    return "\n".join(lines)


def rephrase(
    profile: Profile,
    selection: Selection,
    *,
    target_terms: list[str],
    title: str = "",
    client=None,
    model: str = DEFAULT_MODEL,
) -> RephraseResult:
    """Rewrite the selected bullets toward a posting, or return them untouched.

    Any failure — no credential, an API error, a response that does not validate
    — leaves the original selection in place. A resume that is merely untailored
    is fine; one that is subtly false is not.
    """
    if client is None:
        if not available():
            return RephraseResult(selection, note="no Anthropic credential; left as written")
        import anthropic

        client = anthropic.Anthropic()

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM,
                    # Only takes effect once the prefix passes the model's
                    # minimum cacheable size; harmless below it.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": _build_prompt(selection, target_terms, title)}],
            output_format=Rewrite,
        )
        rewrite = response.parsed_output
    except Exception as error:  # a tailoring failure must never lose the resume
        return RephraseResult(selection, note=f"rephrasing failed ({type(error).__name__})")

    if rewrite is None:
        return RephraseResult(selection, note="model returned nothing usable")

    proposed = {item.fact_id: item.bullet.strip() for item in rewrite.bullets}
    original = {b.fact_id: b.text for b in selection.bullets}

    # Structural checks first: the model must return the same set of bullets.
    if set(proposed) != set(original):
        added = sorted(set(proposed) - set(original))
        dropped = sorted(set(original) - set(proposed))
        return RephraseResult(
            selection,
            note=f"discarded: bullet set changed (added {added}, dropped {dropped})",
        )

    candidate = _apply(selection, proposed)
    problems = check_selection(profile, candidate, strict=False)
    if problems:
        return RephraseResult(
            selection,
            rejected=problems,
            note=f"discarded: {problems[0]}",
        )

    changed = sum(1 for key, text in proposed.items() if text != original[key])
    return RephraseResult(candidate, changed=changed, used_model=True, note="")


def _apply(selection: Selection, proposed: dict[str, str]) -> Selection:
    """A copy of the selection with rewritten text, provenance untouched."""
    import copy
    from dataclasses import replace

    candidate = copy.deepcopy(selection)
    for entry in candidate.entries:
        entry.bullets = [
            replace(bullet, text=proposed.get(bullet.fact_id, bullet.text))
            for bullet in entry.bullets
        ]
    return candidate
