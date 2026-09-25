"""Inline bold markup, and the guarantee that it never hides a claim."""

from __future__ import annotations

from resume.markup import parse_runs, strip_markup


def test_strip_markup_leaves_the_words():
    assert strip_markup("Increased conversion by **35%** overall") == (
        "Increased conversion by 35% overall"
    )
    assert strip_markup("no markup here") == "no markup here"


def test_parse_runs_splits_on_bold():
    runs = parse_runs("Built with **Next.js, Supabase** last year")
    assert runs == [
        {"text": "Built with ", "bold": False},
        {"text": "Next.js, Supabase", "bold": True},
        {"text": " last year", "bold": False},
    ]


def test_parse_runs_round_trips_to_the_plain_text():
    """Whatever the runs say, the words must equal the stripped source."""
    text = "**Bold start**, then plain, then **bold end**"
    assert "".join(r["text"] for r in parse_runs(text)) == strip_markup(text)


def test_parse_runs_handles_no_markup():
    assert parse_runs("plain") == [{"text": "plain", "bold": False}]
