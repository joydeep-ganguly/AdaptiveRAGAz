"""Gemini embeddings for page texts, the same model and settings as the app's ingestion."""

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from skills import config
from skills.errors import SkillError

RETRY_DELAYS = (2, 5, 10)
RETRYABLE_CODES = (429, 500, 502, 503, 504)
# gemini-embedding-001 accepts 2,048 tokens. A page that is refused as too long is retried once
# with only its first characters, and the record gets a warning.
FALLBACK_CHARS = 6000


@dataclass
class Embedded:
    vector: list[float]
    warning: "str | None" = None


@lru_cache
def _client():
    from google import genai

    return genai.Client(api_key=config.gemini_api_key())


def _call(client, model: str, texts: list[str], sleep: Callable[[float], None]) -> list[list[float]]:
    from google.genai import errors, types

    config_ = types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
    contents = [{"parts": [{"text": text}]} for text in texts]
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            result = client.models.embed_content(model=model, contents=contents, config=config_)
            return [list(item.values) for item in result.embeddings]
        except errors.APIError as exc:
            if exc.code in RETRYABLE_CODES and attempt < len(RETRY_DELAYS):
                sleep(RETRY_DELAYS[attempt])
                continue
            raise
    raise AssertionError("unreachable")


def embed_texts(
    texts: list[str],
    client=None,
    model: "str | None" = None,
    sleep: Callable[[float], None] = time.sleep,
) -> "list[Embedded | SkillError]":
    """One result per text, in order. A text that cannot be embedded gets a SkillError."""
    from google.genai import errors

    client = client or _client()
    model = model or config.embedding_model()

    try:
        vectors = _call(client, model, texts, sleep)
        if len(vectors) != len(texts):
            raise SkillError(f"Gemini returned {len(vectors)} embeddings for {len(texts)} texts")
        return [Embedded(vector) for vector in vectors]
    except errors.APIError as exc:
        if exc.code != 400:
            raise SkillError(f"Gemini embedding failed ({exc.code}): {exc}") from exc

    # A refused batch: find out which text it was, one by one.
    outcomes: "list[Embedded | SkillError]" = []
    for text in texts:
        try:
            outcomes.append(Embedded(_call(client, model, [text], sleep)[0]))
            continue
        except errors.APIError as exc:
            if exc.code != 400:
                outcomes.append(SkillError(f"Gemini embedding failed ({exc.code}): {exc}"))
                continue
        try:
            shortened = text[:FALLBACK_CHARS]
            outcomes.append(Embedded(
                _call(client, model, [shortened], sleep)[0],
                f"Text was refused by the embedding model; embedded its first {len(shortened)} characters only",
            ))
        except errors.APIError as exc:
            outcomes.append(SkillError(f"Gemini embedding failed ({exc.code}): {exc}"))
    return outcomes
