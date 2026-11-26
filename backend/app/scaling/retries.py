# backend/app/scaling/retries.py

"""
Retries with exponential backoff and jitter, for the scale profile.

Why jitter: when a service has a hiccup, every worker fails at about the same
moment. If they all retry after exactly 1 s, then 2 s, then 4 s, they hit the
service together again and again. Waiting a RANDOM time between 0 and the
exponential ceiling spreads the retries out ("full jitter").

What already retries, and what this module adds:
  * Gemini chat calls: the Google SDK retries with exponential backoff and jitter.
    llm.py only sets how many attempts (SCALING_RETRY_MAX_ATTEMPTS).
  * Azure AI Search: the Azure SDK retries with exponential backoff but WITHOUT
    jitter and up to 10 times. build_search_retry_policy() adds jitter and the same
    attempt limit.
  * Embeddings: the Gemini embeddings client does not retry at all.
    RetryingEmbeddings adds it.
  * Foundry web search: deliberately NOT retried. It already has a time limit and a
    DuckDuckGo fallback, and a retry would hold a worker for longer.

Only transient errors are retried (429, 408, 5xx, timeouts, dropped connections).
A bad request or an authentication error fails immediately.
"""

import logging
import random
import time
from collections.abc import Callable

import httpx
from azure.core.pipeline.policies import RetryPolicy
from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def backoff_delay(attempt: int, base_delay: float, max_delay: float, rng: Callable[[], float] = random.random) -> float:
    """
    Seconds to wait before retry number `attempt` (0 = the first retry).
    A random value between 0 and min(max_delay, base_delay * 2**attempt).
    """
    ceiling = min(max_delay, base_delay * (2 ** attempt))
    return rng() * ceiling


def is_transient_error(exc: BaseException) -> bool:
    """True for errors that can succeed if tried again."""
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)):
        return True
    # Google API errors carry the HTTP status in `code`; other clients use `status_code`.
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in TRANSIENT_STATUS_CODES


def call_with_retry(
    func: Callable[[], object],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    is_retryable: Callable[[BaseException], bool] = is_transient_error,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
    description: str = "call",
):
    """
    Run func(); on a transient error wait (exponential backoff with jitter) and try
    again, up to max_attempts tries in total. The last error is raised unchanged.
    """
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as exc:
            is_last = attempt == max_attempts - 1
            if is_last or not is_retryable(exc):
                raise
            delay = backoff_delay(attempt, base_delay, max_delay, rng)
            logger.warning(
                "%s failed (%s); retry %d of %d in %.1f s",
                description, type(exc).__name__, attempt + 1, max_attempts - 1, delay,
            )
            sleep(delay)


class RetryingEmbeddings(Embeddings):
    """Wraps any embeddings object and retries transient failures."""

    def __init__(self, inner: Embeddings, max_attempts: int, base_delay: float, max_delay: float):
        self.inner = inner
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    def _retry(self, func, description: str):
        return call_with_retry(
            func,
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            description=description,
        )

    def embed_query(self, text: str) -> list[float]:
        return self._retry(lambda: self.inner.embed_query(text), "Embedding query")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._retry(lambda: self.inner.embed_documents(texts), "Embedding documents")


class JitteredRetryPolicy(RetryPolicy):
    """
    Azure's own retry policy with full jitter added to the wait between retries.
    A Retry-After header sent by the service is still obeyed as it is.
    """

    def get_backoff_time(self, settings):
        return random.random() * super().get_backoff_time(settings)


def build_search_retry_policy(max_attempts: int, base_delay: float, max_delay: float) -> JitteredRetryPolicy:
    retries = max(0, max_attempts - 1)  # max_attempts counts the first try; Azure counts only the retries
    return JitteredRetryPolicy(
        retry_total=retries,
        retry_connect=retries,
        retry_read=retries,
        retry_status=retries,
        retry_backoff_factor=base_delay,
        retry_backoff_max=max_delay,
    )


def retrying_embeddings(inner: Embeddings, settings) -> Embeddings:
    """Used by app/agent/llm.py when the scale profile is on."""
    return RetryingEmbeddings(
        inner,
        max_attempts=settings.scaling_retry_max_attempts,
        base_delay=settings.scaling_retry_base_delay_seconds,
        max_delay=settings.scaling_retry_max_delay_seconds,
    )


def search_retry_policy(settings) -> JitteredRetryPolicy:
    """Used by azure_search_store.py when the scale profile is on."""
    return build_search_retry_policy(
        settings.scaling_retry_max_attempts,
        settings.scaling_retry_base_delay_seconds,
        settings.scaling_retry_max_delay_seconds,
    )