"""Read a job description into the few things that decide an application.

Everything here is deterministic. The expensive, non-deterministic step
(rephrasing bullets) happens later and only for jobs that clear the gate, which
is the whole reason this part exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from resume.schema import Shape
from tailor import vocab

# Headings that start a "you must have this" block, and one that ends it.
REQUIRED_HEADING = re.compile(
    r"^\s*(?:what you(?:'| a)?ll need|requirements?|qualifications?|must[- ]haves?|"
    r"who you are|about you|we(?:'|)re looking for|basic qualifications|you have)\b",
    re.I,
)
OPTIONAL_HEADING = re.compile(
    r"^\s*(?:nice[- ]to[- ]haves?|bonus|preferred|pluses?|a plus|"
    r"preferred qualifications|icing on the cake|even better)\b",
    re.I,
)
CLOSING_HEADING = re.compile(
    r"^\s*(?:benefits?|perks|compensation|about (?:us|the company)|equal opportunit|"
    r"how to apply|our values|diversity)\b",
    re.I,
)

YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?year", re.I)

# Weighted signals per shape. A term can point at more than one shape; the
# weights say how strongly.
SHAPE_SIGNALS: dict[Shape, dict[str, float]] = {
    Shape.ai_engineer: {
        "llm": 3.0, "rag": 3.0, "embeddings": 2.5, "vector-db": 2.5, "agents": 2.5,
        "prompt-engineering": 2.0, "fine-tuning": 2.0, "semantic-search": 2.0,
        "anthropic": 1.5, "openai-api": 1.5, "transformers": 1.5, "nlp": 1.5,
        "computer-vision": 1.5, "pytorch": 1.0, "model-evaluation": 1.0,
        "tensorflow": 1.0, "scikit-learn": 0.5, "mlops": 0.5,
    },
    Shape.backend_python: {
        "fastapi": 3.0, "django": 2.5, "flask": 2.0, "sqlalchemy": 2.0,
        "rest-apis": 2.0, "microservices": 2.0, "postgres": 1.5, "sql": 1.5,
        "python": 1.5, "kafka": 1.5, "redis": 1.0, "data-pipelines": 1.0,
        "airflow": 1.0, "docker": 0.5, "kubernetes": 0.5, "observability": 0.5,
    },
    Shape.fullstack: {
        "nextjs": 3.0, "react": 3.0, "frontend": 2.5, "typescript": 2.0,
        "supabase": 2.0, "node": 1.5, "javascript": 1.5, "html-css": 1.5,
        "vue": 1.0, "graphql": 1.0, "analytics": 0.5,
    },
}


@dataclass
class JobDescription:
    """What a posting actually asks for."""

    required: set[str] = field(default_factory=set)
    preferred: set[str] = field(default_factory=set)
    all_terms: set[str] = field(default_factory=set)
    years_required: int | None = None
    shape: Shape = Shape.backend_python
    shape_scores: dict[str, float] = field(default_factory=dict)
    shape_confidence: float = 0.0


def _sections(text: str) -> tuple[str, str]:
    """Split into (required, preferred) prose. Either may be empty."""
    required: list[str] = []
    preferred: list[str] = []
    bucket: list[str] | None = None

    for line in (text or "").splitlines():
        if CLOSING_HEADING.match(line):
            bucket = None
            continue
        if OPTIONAL_HEADING.match(line):
            bucket = preferred
            continue
        if REQUIRED_HEADING.match(line):
            bucket = required
            continue
        if bucket is not None:
            bucket.append(line)

    return "\n".join(required), "\n".join(preferred)


def classify_shape(text: str) -> tuple[Shape, dict[str, float], float]:
    """Which of the three shapes is this posting?

    One shape per rendered resume, so this decision has to be made somewhere;
    making it from weighted vocabulary hits keeps it cheap and inspectable.
    """
    present = vocab.terms_in(text)
    scores = {
        shape: sum(weight for term, weight in signals.items() if term in present)
        for shape, signals in SHAPE_SIGNALS.items()
    }
    total = sum(scores.values())
    best = max(scores, key=lambda s: (scores[s], -list(Shape).index(s)))
    confidence = scores[best] / total if total else 0.0
    return best, {s.value: round(v, 2) for s, v in scores.items()}, round(confidence, 3)


def analyse(text: str) -> JobDescription:
    required_text, preferred_text = _sections(text)
    all_terms = vocab.terms_in(text)

    # No recognisable requirements heading: treat the whole posting as required
    # rather than reporting that it asks for nothing.
    required = vocab.terms_in(required_text) if required_text.strip() else set(all_terms)
    preferred = vocab.terms_in(preferred_text)
    required -= preferred  # a term listed as a bonus is not also a must

    years = [int(m.group(1)) for m in YEARS.finditer(text or "")]
    shape, scores, confidence = classify_shape(text)

    return JobDescription(
        required=required,
        preferred=preferred,
        all_terms=all_terms,
        years_required=min(years) if years else None,
        shape=shape,
        shape_scores=scores,
        shape_confidence=confidence,
    )
