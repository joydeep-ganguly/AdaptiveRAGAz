# backend/app/scaling/budgets.py

"""
Token and call budgets for the scale profile.

A TokenBudgetCallback watches every model call made while one question runs
(router, grading, answer, rewrite, SQL, ...). Before each call it checks:
  * the question has not used more model calls than allowed (stops runaway loops),
  * the question has not used more tokens than allowed,
  * the user has not used up their daily tokens.
After each call it adds the tokens that call used. A check that fails raises
BudgetExceeded, which stops the run; the worker turns it into a failed job with
a clear message. The call that crosses a limit has already happened, so a request
can overshoot by at most one call.

Answers served from the LLM cache cost no tokens, so they are not counted.
Limits: only LangChain model calls are counted. The Foundry web-search agent runs
inside Azure and is not visible here; embeddings are not counted (very small).
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from langchain_core.callbacks import BaseCallbackHandler
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

TOKENS_KEY_PREFIX = "adaptiverag:tokens:"

_USER_MESSAGES = {
    "request_tokens": "This question needed more processing than the per-question limit allows.",
    "llm_calls": "This question needed more steps than the per-question limit allows.",
    "daily_tokens": "Your daily usage limit has been reached. It resets at midnight UTC.",
}


class BudgetExceeded(Exception):
    """A token or call budget ran out. `reason` is one of the keys of _USER_MESSAGES."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

    @property
    def user_message(self) -> str:
        return _USER_MESSAGES.get(self.reason, "A usage limit was reached.")


class UserTokenLedger:
    """Tokens used per user per UTC day, kept in Redis."""

    def __init__(self, client, daily_limit: int):
        self.client = client
        self.daily_limit = daily_limit

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    def _key(self, user_id: int) -> str:
        return f"{TOKENS_KEY_PREFIX}{user_id}:{self._today()}"

    def used_today(self, user_id: int) -> int:
        return int(self.client.get(self._key(user_id)) or 0)

    def is_exhausted(self, user_id: int) -> bool:
        return self.used_today(user_id) >= self.daily_limit

    def add(self, user_id: int, tokens: int) -> None:
        if tokens <= 0:
            return
        key = self._key(user_id)
        pipe = self.client.pipeline()
        pipe.incrby(key, tokens)
        pipe.expire(key, 2 * 24 * 3600)  # the day's counter cleans itself up
        pipe.execute()

    @staticmethod
    def seconds_until_reset() -> int:
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((tomorrow - now).total_seconds()))


@dataclass(frozen=True)
class BudgetLimits:
    """Everything the worker needs to budget one job."""

    ledger: UserTokenLedger
    max_tokens: int
    max_llm_calls: int


def _tokens_in(response) -> int:
    """Total tokens reported by the model for one call (0 if it reports none)."""
    total = 0
    found = False
    for generations in response.generations:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                found = True
                total += usage.get("total_tokens") or (
                    usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                )
    if not found and response.llm_output:
        total = (response.llm_output.get("token_usage") or {}).get("total_tokens", 0)
    return int(total)


class TokenBudgetCallback(BaseCallbackHandler):
    # Without this, LangChain logs an error from a callback and carries on.
    raise_error = True

    def __init__(self, ledger: UserTokenLedger, user_id: int, max_tokens: int, max_llm_calls: int):
        self.ledger = ledger
        self.user_id = user_id
        self.max_tokens = max_tokens
        self.max_llm_calls = max_llm_calls
        self.tokens_used = 0
        self.llm_calls = 0
        self._lock = threading.Lock()

    def _check_before_call(self) -> None:
        with self._lock:
            self.llm_calls += 1
            if self.llm_calls > self.max_llm_calls:
                raise BudgetExceeded("llm_calls")
            if self.tokens_used >= self.max_tokens:
                raise BudgetExceeded("request_tokens")
        try:
            if self.ledger.is_exhausted(self.user_id):
                raise BudgetExceeded("daily_tokens")
        except RedisError:
            logger.warning("Token ledger unavailable; skipping the daily budget check")

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._check_before_call()

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._check_before_call()

    def on_llm_end(self, response, **kwargs):
        tokens = _tokens_in(response)
        with self._lock:
            self.tokens_used += tokens
        try:
            self.ledger.add(self.user_id, tokens)
        except RedisError:
            logger.warning("Token ledger unavailable; usage not recorded")

    @property
    def usage(self) -> dict:
        return {"tokens": self.tokens_used, "llm_calls": self.llm_calls}


class BudgetedGraph:
    """
    Wraps the compiled graph so every run carries a TokenBudgetCallback.
    chat_service.send_message only needs get_state() and invoke(), so it runs
    unchanged: the plain and scaled profiles use the same graph and service code.
    """

    def __init__(self, graph, callback: TokenBudgetCallback):
        self._graph = graph
        self.callback = callback

    def get_state(self, config=None):
        return self._graph.get_state(config)

    def invoke(self, input, config=None, **kwargs):
        config = dict(config or {})
        config["callbacks"] = [*(config.get("callbacks") or []), self.callback]
        return self._graph.invoke(input, config=config, **kwargs)