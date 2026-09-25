"""Scoring, shape classification, and the guards on constrained rephrasing."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from resume.schema import Shape
from resume.select import select
from tailor import vocab
from tailor.jd import analyse, classify_shape
from tailor.rephrase import RewrittenBullet, Rewrite, rephrase
from tailor.score import capabilities, score, years_of_experience


# --- vocabulary -------------------------------------------------------------

def test_terms_are_found_with_their_common_spellings():
    found = vocab.terms_in("We use FastAPI, Postgres and retrieval-augmented generation.")
    assert {"fastapi", "postgres", "rag"} <= found


def test_a_name_containing_another_name_does_not_match_both():
    """"js" lives inside "Next.js"; matching it would credit JavaScript to
    every Next.js posting that never mentioned it."""
    assert "javascript" not in vocab.terms_in("Our frontend is Next.js")
    assert "nextjs" in vocab.terms_in("Our frontend is Next.js")


def test_punctuation_at_the_end_of_a_sentence_does_not_hide_a_term():
    assert "postgres" in vocab.terms_in("The database is Postgres.")
    assert "react" in vocab.terms_in("Built in React, mostly.")


def test_ordinary_english_does_not_register_as_technology():
    assert vocab.terms_in("We are going to work together as a team") == set()
    assert "node" not in vocab.terms_in("Kubernetes nodes are cheap")


# --- job descriptions -------------------------------------------------------

JD = """Senior AI Engineer

What you'll need:
- 3+ years of Python
- Experience with LLMs, RAG and embeddings
- FastAPI and Postgres in production

Nice to have:
- Next.js and React
- Kubernetes

Benefits:
- Dental, and a Kubernetes sticker
"""


def test_requirements_and_bonuses_are_separated():
    jd = analyse(JD)
    assert {"python", "llm", "rag", "embeddings", "fastapi", "postgres"} <= jd.required
    assert {"nextjs", "react", "kubernetes"} <= jd.preferred
    assert "kubernetes" not in jd.required, "a bonus is not also a requirement"


def test_years_required_is_read_from_the_posting():
    assert analyse(JD).years_required == 3


def test_a_posting_with_no_headings_treats_everything_as_required():
    jd = analyse("We need Python, FastAPI and Postgres experience.")
    assert {"python", "fastapi", "postgres"} <= jd.required


def test_shape_classification_picks_the_dominant_signal():
    ai, _, _ = classify_shape("Build RAG pipelines with LLMs, embeddings and a vector database")
    back, _, _ = classify_shape("Design FastAPI microservices, SQLAlchemy models, REST APIs")
    front, _, _ = classify_shape("Ship Next.js and React features in TypeScript with Supabase")
    assert (ai, back, front) == (Shape.ai_engineer, Shape.backend_python, Shape.fullstack)


def test_shape_confidence_is_low_when_the_posting_is_mixed():
    _, _, focused = classify_shape("LLM RAG embeddings vector database agents fine-tuning")
    _, _, mixed = classify_shape("Python React LLM FastAPI Next.js embeddings")
    assert focused > mixed


# --- scoring ----------------------------------------------------------------

def test_capabilities_come_from_the_fact_bank_not_a_wishlist(profile):
    have = capabilities(profile)
    assert {"python", "fastapi", "rag", "embeddings"} <= have
    assert "kubernetes" not in have, "nothing in the fact bank mentions it"


def test_years_of_experience_counts_closed_and_open_roles(profile):
    years = years_of_experience(profile, today=dt.date(2026, 6, 1))
    assert years > 0


def test_a_good_match_passes_and_a_wrong_stack_does_not(profile):
    good = score(profile, "Requirements: Python, FastAPI, RAG, embeddings, Postgres.")
    bad = score(profile, "Requirements: Java, Spring Boot, Scala, Terraform, Kubernetes.")
    assert good.passes and good.total > bad.total
    assert not bad.passes


def test_seniority_gap_is_penalised_even_when_the_keywords_line_up(profile):
    """The mismatch a keyword score would otherwise miss completely."""
    junior = score(profile, "Requirements: Python, FastAPI, RAG, embeddings.")
    senior = score(profile, "Requirements: 10+ years Python, FastAPI, RAG, embeddings.")
    assert senior.total < junior.total
    assert senior.years_penalty > 0
    assert "years" in senior.reason or senior.years_required == 10


def test_gaps_report_what_is_missing_and_never_suggest_claiming_it(profile):
    result = score(profile, "Requirements: Python, Kubernetes, Terraform, Rust.")
    assert "kubernetes" in result.gaps and "terraform" in result.gaps
    assert "python" not in result.gaps


def test_threshold_is_adjustable(profile):
    jd = "Requirements: Python, Kubernetes, Terraform."
    assert not score(profile, jd, threshold=90).passes
    assert score(profile, jd, threshold=1).passes


def test_a_vague_posting_scores_neutral_rather_than_perfect(profile):
    """Naming no technology is not the same as matching everything."""
    result = score(profile, "We want a passionate engineer to join our dynamic team.")
    assert 0 < result.total < 80


# --- constrained rephrasing -------------------------------------------------

class FakeClient:
    """Stands in for the Anthropic client. Records the request, returns a script."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.seen = reply, error, None
        self.messages = self

    def parse(self, **kwargs):
        self.seen = kwargs
        if self.error:
            raise self.error
        return type("Response", (), {"parsed_output": self.reply})()


def rewrite_of(selection, transform=lambda fact_id, text: text):
    return Rewrite(
        bullets=[
            RewrittenBullet(fact_id=b.fact_id, bullet=transform(b.fact_id, b.text))
            for b in selection.bullets
        ]
    )


def test_a_clean_rewrite_is_accepted(profile):
    selection = select(profile, Shape.ai_engineer)
    reply = rewrite_of(selection, lambda _i, text: text.replace("Built", "Shipped"))
    result = rephrase(profile, selection, target_terms=["llm"], client=FakeClient(reply))

    assert result.used_model and result.changed > 0
    assert any("Shipped" in b.text for b in result.selection.bullets)


def test_an_invented_number_is_discarded_and_the_original_survives(profile):
    """The guarantee the whole pipeline rests on."""
    selection = select(profile, Shape.fullstack)
    reply = rewrite_of(selection, lambda _i, text: text + " Delivered a 92% uplift.")
    result = rephrase(profile, selection, target_terms=[], client=FakeClient(reply))

    assert not result.used_model
    assert "invented_number" in {p.kind for p in result.rejected}
    assert result.selection.bullets[0].text == selection.bullets[0].text


def test_a_dropped_bullet_is_discarded(profile):
    selection = select(profile, Shape.ai_engineer)
    reply = rewrite_of(selection)
    reply.bullets.pop()
    result = rephrase(profile, selection, target_terms=[], client=FakeClient(reply))
    assert not result.used_model and "dropped" in result.note


def test_an_extra_bullet_is_discarded(profile):
    selection = select(profile, Shape.ai_engineer)
    reply = rewrite_of(selection)
    reply.bullets.append(RewrittenBullet(fact_id="f_invented", bullet="Led a team of 40."))
    result = rephrase(profile, selection, target_terms=[], client=FakeClient(reply))
    assert not result.used_model and "added" in result.note


def test_an_api_failure_leaves_the_resume_intact(profile):
    selection = select(profile, Shape.ai_engineer)
    result = rephrase(
        profile, selection, target_terms=[], client=FakeClient(error=RuntimeError("boom"))
    )
    assert not result.used_model
    assert result.selection.bullets == selection.bullets
    assert "failed" in result.note


def test_returning_the_input_unchanged_is_a_valid_answer(profile):
    selection = select(profile, Shape.ai_engineer)
    result = rephrase(profile, selection, target_terms=[], client=FakeClient(rewrite_of(selection)))
    assert result.used_model and result.changed == 0


def test_the_prompt_carries_the_fact_ids_and_the_posting_vocabulary(profile):
    selection = select(profile, Shape.ai_engineer)
    client = FakeClient(rewrite_of(selection))
    rephrase(profile, selection, target_terms=["rag", "llm"], title="AI Engineer", client=client)

    prompt = client.seen["messages"][0]["content"]
    assert "AI Engineer" in prompt and "rag" in prompt
    for bullet in selection.bullets:
        assert bullet.fact_id in prompt


def test_no_credential_means_no_call_and_no_change(profile, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    selection = select(profile, Shape.ai_engineer)
    result = rephrase(profile, selection, target_terms=[])
    assert not result.used_model and "credential" in result.note


# --- cache ------------------------------------------------------------------

def test_jd_analysis_is_cached_by_description_hash(tmp_path, monkeypatch):
    """The same posting arrives from three sources; parse it once."""
    from tailor import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "jd")
    first = cache.analyse_cached(JD, "abc123")
    assert (tmp_path / "jd" / "abc123.json").exists()

    second = cache.analyse_cached("completely different text", "abc123")
    assert second.required == first.required, "the hash, not the text, is the key"
    assert second.shape is first.shape


def test_a_corrupt_cache_entry_is_reparsed_rather_than_fatal(tmp_path, monkeypatch):
    from tailor import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "jd")
    (tmp_path / "jd").mkdir(parents=True)
    (tmp_path / "jd" / "bad.json").write_text("{not json")

    result = cache.analyse_cached(JD, "bad")
    assert "python" in result.required


def test_no_hash_means_no_cache_write(tmp_path, monkeypatch):
    from tailor import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "jd")
    cache.analyse_cached(JD, None)
    assert not (tmp_path / "jd").exists()
