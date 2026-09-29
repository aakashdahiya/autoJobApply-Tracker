"""Tailoring through a chat window instead of the API.

The extension opens claude.ai, pastes the prompt this module builds, reads the
reply back out of the page, and posts it here. That is the only difference from
`rephrase`: where the text came from. Everything that decides whether the text
is allowed onto a resume is shared, because a chat window is exactly as capable
of inventing an employer as an API call is, and the fact bank is what stops it
either way.

So the reply goes through `rephrase.accept_or_discard` unchanged: the same
bullet set, no number its source fact lacks, every bullet traceable to a
`fact_id`. A reply that fails comes back with the reasons rather than being
quietly dropped, because when you are driving this by hand you want to know the
chat invented something.

The prompt asks for a fenced JSON block. Chat replies are prose by nature —
they open with "Here's the tailored version" and close with an offer to help
further — so the parser looks for the block and ignores everything around it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from resume.schema import Profile
from resume.select import Selection
from tailor.rephrase import SYSTEM, RephraseResult, accept_or_discard

# ```json ... ``` , ``` ... ``` , or a bare object. Non-greedy so several blocks
# in one reply each parse rather than merging into one unparseable lump.
FENCED = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


class ChatReplyUnusable(ValueError):
    """The reply held no JSON we could read."""


@dataclass
class ChatPrompt:
    text: str
    fact_ids: list[str]
    shape: str
    title: str


def build_prompt(selection: Selection, *, target_terms: list[str], title: str,
                 shape: str) -> ChatPrompt:
    """The whole instruction, self-contained, for pasting into a chat.

    The API path sends the rules as a system prompt; a chat has no such channel,
    so they are inlined here. The output contract is stricter than the API's for
    the same reason: there is no schema enforcement on the other end, only this
    module's parser and the fact-bank gate behind it.
    """
    lines = [
        SYSTEM,
        "",
        "----",
        "",
        f"Job title: {title or '(not given)'}",
        f"Role shape: {shape}",
        f"Vocabulary the posting uses: {', '.join(sorted(target_terms)) or '(none extracted)'}",
        "",
        "Bullets to consider:",
    ]
    for bullet in selection.bullets:
        lines.append(f"- [{bullet.fact_id}] {bullet.text}")
    lines += [
        "",
        "Reply with one fenced JSON block and nothing that contradicts it:",
        "",
        "```json",
        '{"bullets": [{"fact_id": "<unchanged>", "bullet": "<rewritten or original>"}]}',
        "```",
        "",
        f"Return all {len(selection.bullets)} bullets, each exactly once, with its "
        "fact_id unchanged. Any bullet you add, drop, renumber or attach a new "
        "number to causes the whole reply to be discarded.",
    ]
    return ChatPrompt(
        text="\n".join(lines),
        fact_ids=[b.fact_id for b in selection.bullets],
        shape=shape,
        title=title,
    )


def parse_reply(text: str) -> dict[str, str]:
    """Pull `{fact_id: bullet}` out of whatever the chat said.

    Tries each fenced block, then the raw text, and keeps the last one that
    parses into the expected shape — a chat that corrects itself puts the good
    answer last.
    """
    if not (text or "").strip():
        raise ChatReplyUnusable("the reply was empty")

    candidates = [match.group(1) for match in FENCED.finditer(text)]
    candidates.append(text)

    found: dict[str, str] | None = None
    for candidate in candidates:
        parsed = _as_bullets(candidate)
        if parsed:
            found = parsed

    if not found:
        raise ChatReplyUnusable(
            "no JSON object with a `bullets` list was found in the reply"
        )
    return found


def _as_bullets(candidate: str) -> dict[str, str] | None:
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        # A bare object embedded in prose: take the outermost braces.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(candidate[start : end + 1])
        except (ValueError, TypeError):
            return None

    if not isinstance(data, dict):
        return None
    rows = data.get("bullets")
    if not isinstance(rows, list) or not rows:
        return None

    bullets: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        fact_id, bullet = row.get("fact_id"), row.get("bullet")
        if not isinstance(fact_id, str) or not isinstance(bullet, str):
            return None
        if not fact_id.strip() or not bullet.strip():
            return None
        bullets[fact_id.strip()] = bullet.strip()
    return bullets or None


def apply_reply(profile: Profile, selection: Selection, reply: str) -> RephraseResult:
    """Parse a chat reply and accept it only if the fact bank allows it."""
    proposed = parse_reply(reply)
    return accept_or_discard(profile, selection, proposed)
