"""Tailoring read back out of a chat window.

Two jobs here. Parsing has to survive prose, because a chat replies with
"Here's the tailored version!" wrapped around the JSON. And the fact bank has
to hold, because a chat is exactly as capable of inventing an employer as an
API call is — the whole point of routing this through the same gate is that
where the text came from changes nothing about what it may claim.
"""

from __future__ import annotations

import pytest

from resume.schema import Shape
from resume.select import select
from tailor.chat import ChatReplyUnusable, apply_reply, build_prompt, parse_reply


@pytest.fixture
def selection(profile):
    return select(profile, Shape.ai_engineer)


def reply_for(selection, **overrides):
    """A well-formed reply that returns every bullet unchanged."""
    rows = [{"fact_id": b.fact_id, "bullet": overrides.get(b.fact_id, b.text)}
            for b in selection.bullets]
    import json
    return "Here's the tailored version!\n\n```json\n" + json.dumps({"bullets": rows}) + "\n```\n\nLet me know if you'd like changes."


# --- the prompt -------------------------------------------------------------

def test_the_prompt_carries_every_bullet_with_its_fact_id(profile, selection):
    built = build_prompt(selection, target_terms=["rag", "python"],
                         title="Senior AI Engineer", shape="ai_engineer")

    assert built.fact_ids == [b.fact_id for b in selection.bullets]
    for bullet in selection.bullets:
        assert f"[{bullet.fact_id}]" in built.text
    assert "Senior AI Engineer" in built.text
    assert "rag" in built.text


def test_the_prompt_inlines_the_rules_since_a_chat_has_no_system_channel(selection):
    built = build_prompt(selection, target_terms=[], title="", shape="ai_engineer")
    assert "Never introduce a fact" in built.text
    assert "```json" in built.text
    assert str(len(selection.bullets)) in built.text


# --- parsing whatever a chat says -------------------------------------------

def test_reads_json_out_of_surrounding_prose():
    text = (
        "Sure! Here are the tailored bullets:\n\n"
        '```json\n{"bullets": [{"fact_id": "f1", "bullet": "Shipped it."}]}\n```\n\n'
        "Want me to adjust the tone?"
    )
    assert parse_reply(text) == {"f1": "Shipped it."}


def test_reads_an_unfenced_object():
    text = 'Here you go: {"bullets": [{"fact_id": "f1", "bullet": "Shipped it."}]} — done.'
    assert parse_reply(text) == {"f1": "Shipped it."}


def test_a_bare_fence_without_the_json_tag_still_parses():
    text = '```\n{"bullets": [{"fact_id": "f1", "bullet": "Shipped it."}]}\n```'
    assert parse_reply(text) == {"f1": "Shipped it."}


def test_a_correction_later_in_the_reply_wins():
    """A chat that catches its own mistake puts the good answer last."""
    text = (
        '```json\n{"bullets": [{"fact_id": "f1", "bullet": "First try."}]}\n```\n'
        "Actually, that overstated it. Here is a better version:\n"
        '```json\n{"bullets": [{"fact_id": "f1", "bullet": "Second try."}]}\n```'
    )
    assert parse_reply(text) == {"f1": "Second try."}


def test_an_empty_reply_says_so():
    with pytest.raises(ChatReplyUnusable, match="empty"):
        parse_reply("   ")


def test_a_reply_with_no_json_says_so():
    with pytest.raises(ChatReplyUnusable, match="no JSON"):
        parse_reply("I'd be happy to help! Could you tell me more about the role?")


def test_a_refusal_is_not_mistaken_for_bullets():
    with pytest.raises(ChatReplyUnusable):
        parse_reply("I can't help with fabricating work experience.")


def test_malformed_rows_are_rejected_rather_than_half_read():
    with pytest.raises(ChatReplyUnusable):
        parse_reply('```json\n{"bullets": [{"fact_id": "f1"}]}\n```')
    with pytest.raises(ChatReplyUnusable):
        parse_reply('```json\n{"bullets": [{"fact_id": "", "bullet": "x"}]}\n```')
    with pytest.raises(ChatReplyUnusable):
        parse_reply('```json\n{"bullets": []}\n```')


# --- the gate, which is the point -------------------------------------------

def test_an_unchanged_reply_is_accepted(profile, selection):
    result = apply_reply(profile, selection, reply_for(selection))
    assert result.rejected == []
    assert result.changed == 0


def test_a_chat_that_invents_a_bullet_is_rejected(profile, selection):
    import json

    rows = [{"fact_id": b.fact_id, "bullet": b.text} for b in selection.bullets]
    rows.append({"fact_id": "invented", "bullet": "Led a team of 40 at Google."})
    reply = "```json\n" + json.dumps({"bullets": rows}) + "\n```"

    result = apply_reply(profile, selection, reply)
    assert "bullet set changed" in result.note
    assert result.selection is selection, "the original bullets survive a bad reply"


def test_a_chat_that_drops_a_bullet_is_rejected(profile, selection):
    import json

    rows = [{"fact_id": b.fact_id, "bullet": b.text} for b in selection.bullets][:-1]
    reply = "```json\n" + json.dumps({"bullets": rows}) + "\n```"

    result = apply_reply(profile, selection, reply)
    assert "bullet set changed" in result.note


def test_a_chat_that_invents_a_number_is_rejected(profile, selection):
    """The failure that matters most: fluent, plausible, and false."""
    first = selection.bullets[0]
    reply = reply_for(selection, **{first.fact_id: f"{first.text} Cut latency by 97%."})

    result = apply_reply(profile, selection, reply)
    assert result.rejected, "a number with no source fact must not reach a resume"
    assert result.selection is selection


def test_the_rejection_says_which_bullet_and_why(profile, selection):
    first = selection.bullets[0]
    reply = reply_for(selection, **{first.fact_id: f"{first.text} Saved $4.2M annually."})

    result = apply_reply(profile, selection, reply)
    assert result.rejected
    assert "discarded" in result.note
