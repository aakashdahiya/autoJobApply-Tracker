"""The technology vocabulary, and how to spot it in prose.

Deliberately a curated list rather than "every capitalised word". A job
description is full of nouns; only some of them are claims you can be matched
against, and a keyword report full of "collaboration" and "ownership" is noise
that hides the two terms that actually decide the application.
"""

from __future__ import annotations

import re

# canonical -> the spellings a posting might use
ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("python", "python3"),
    "javascript": ("javascript", "js", "es6"),
    "typescript": ("typescript", "ts"),
    "sql": ("sql", "ansi sql"),
    "go": ("golang",),
    "java": ("java",),
    "html-css": ("html", "css", "html/css", "scss", "tailwind"),
    "fastapi": ("fastapi",),
    "django": ("django",),
    "flask": ("flask",),
    "rest-apis": ("rest", "restful", "rest api", "rest apis", "http api"),
    "graphql": ("graphql",),
    "microservices": ("microservice", "microservices"),
    "postgres": ("postgres", "postgresql", "psql"),
    "mysql": ("mysql",),
    "sqlite": ("sqlite",),
    "supabase": ("supabase",),
    "mongodb": ("mongodb", "mongo"),
    "redis": ("redis",),
    "kafka": ("kafka",),
    "airflow": ("airflow",),
    "spark": ("spark", "pyspark"),
    "sqlalchemy": ("sqlalchemy",),
    "docker": ("docker", "containerised", "containerized"),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud"),
    "azure": ("azure",),
    "ci-cd": ("ci/cd", "cicd", "continuous integration", "continuous delivery"),
    "observability": ("observability", "monitoring", "tracing", "logging", "datadog"),
    "testing": ("pytest", "unit test", "unit tests", "integration test", "test coverage"),
    "llm": ("llm", "llms", "large language model", "large language models", "genai",
            "generative ai", "foundation model"),
    "anthropic": ("anthropic", "claude"),
    "openai-api": ("openai", "gpt-4", "gpt4"),
    "rag": ("rag", "retrieval augmented generation", "retrieval-augmented generation"),
    "embeddings": ("embedding", "embeddings", "sentence-transformers", "sentence transformers"),
    "vector-db": ("vector database", "vector databases", "vector db", "vector dbs",
                  "pinecone", "weaviate", "pgvector", "chroma", "faiss", "qdrant"),
    "semantic-search": ("semantic search", "similarity search", "cosine similarity"),
    "prompt-engineering": ("prompt engineering", "prompting", "prompt design"),
    "agents": ("ai agent", "ai agents", "agentic", "tool use", "function calling"),
    "fine-tuning": ("fine-tune", "fine-tuning", "finetuning", "lora", "peft"),
    "pytorch": ("pytorch", "torch"),
    "tensorflow": ("tensorflow", "keras"),
    "scikit-learn": ("scikit-learn", "sklearn", "scikit learn"),
    "transformers": ("transformer", "transformers", "bert", "hugging face", "huggingface"),
    "nlp": ("nlp", "natural language processing"),
    "computer-vision": ("computer vision", "image classification", "ocr", "multimodal",
                        "vision model"),
    "model-evaluation": ("model evaluation", "evaluation harness", "eval", "evals",
                         "benchmark", "benchmarks", "offline evaluation"),
    "mlops": ("mlops", "mlflow", "model registry", "model serving", "feature store"),
    "recommendations": ("recommendation", "recommender", "ranking"),
    "ab-testing": ("a/b test", "a/b testing", "ab testing", "experimentation"),
    "data-pipelines": ("etl", "elt", "data pipeline", "data pipelines", "dbt"),
    "analytics": ("analytics", "dashboard", "dashboards", "bi"),
    "react": ("react", "react.js", "reactjs"),
    "nextjs": ("next.js", "nextjs"),
    "node": ("node.js", "nodejs", "node"),
    "vue": ("vue", "vue.js"),
    "frontend": ("frontend", "front-end", "front end", "ui development"),
}

# Words that are always in a posting and never distinguish one from another.
STOPWORDS = frozenset(
    """team teams work working collaborate collaboration communication stakeholder
    ownership passionate fast-paced dynamic exciting opportunity culture benefits
    salary equity remote hybrid onsite candidate candidates applicants experience
    years strong excellent proven ability skills knowledge understanding""".split()
)

_WORD = re.compile(r"[a-z0-9][a-z0-9+#./-]*")


def _prepare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold())


def terms_in(text: str) -> set[str]:
    """Canonical technology terms present in a block of prose."""
    blob = _prepare(text)
    found: set[str] = set()
    for canonical, spellings in ALIASES.items():
        for spelling in sorted(spellings, key=len, reverse=True):
            # Boundaries have to tell two uses of "." apart: inside a name
            # ("Next.js") and as punctuation ("...and Postgres."). A plain
            # \b boundary makes every Next.js posting claim JavaScript;
            # banning "." outright makes a sentence-ending "Postgres." vanish.
            # So: never adjacent to an alphanumeric, and never adjacent to a
            # separator that is itself joined to one. Longest spelling first,
            # so "node.js" is tried before "node".
            pattern = (
                rf"(?<![a-z0-9+#])(?<![a-z0-9][./-])"
                rf"{re.escape(spelling)}"
                rf"(?![a-z0-9+#])(?![./-][a-z0-9])"
            )
            if re.search(pattern, blob):
                found.add(canonical)
                break
    return found


def canonicalise(term: str) -> str:
    """Map a free-form skill tag onto the shared vocabulary where possible."""
    folded = term.casefold().strip()
    if folded in ALIASES:
        return folded
    for canonical, spellings in ALIASES.items():
        if folded in spellings:
            return canonical
    return folded
