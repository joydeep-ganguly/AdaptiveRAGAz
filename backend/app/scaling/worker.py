# backend/app/scaling/worker.py

"""
Queue worker. Run it as its own process:

    python -m app.scaling.worker

It builds the LangGraph agent once, then loops: take a job id off the Redis
queue, run the question through chat_service.send_message (the same code the
synchronous endpoint uses), and store the outcome on the job.
"""

import logging
import signal

from app.agent.graph import create_adaptive_rag
from app.config import get_settings
from app.db.session import SessionLocal
from app.scaling.budgets import BudgetedGraph, BudgetExceeded, BudgetLimits, TokenBudgetCallback, UserTokenLedger
from app.logging_config import setup_logging
from app.scaling.jobs import JobStore
from app.scaling.redis_client import get_redis
from app.services import chat_service

logger = logging.getLogger(__name__)


def process_job(store: JobStore, graph, job_id: str, budget: BudgetLimits | None = None) -> None:
    job = store.get(job_id)
    if job is None:
        # The job record expired while the id was still waiting in the queue.
        logger.warning("Job %s expired before it was processed; skipping", job_id)
        return

    store.mark_running(job_id)

    # With a budget, the graph is wrapped so every model call in this job is counted.
    callback = None
    if budget is not None:
        callback = TokenBudgetCallback(
            budget.ledger,
            user_id=int(job["user_id"]),
            max_tokens=budget.max_tokens,
            max_llm_calls=budget.max_llm_calls,
        )
        graph = BudgetedGraph(graph, callback)

    def usage_so_far() -> dict | None:
        return callback.usage if callback else None

    db = SessionLocal()  # one database session per job, always closed afterwards
    try:
        assistant_message, media_filenames = chat_service.send_message(
            db,
            graph=graph,
            conversation_id=job["conversation_id"],
            user_id=int(job["user_id"]),
            user_message=job["message"],
        )
        store.mark_succeeded(job_id, {
            "conversation_id": job["conversation_id"],
            "role": assistant_message.role,
            "content": assistant_message.content,
            "media_filenames": media_filenames,
        }, usage=usage_so_far())
        logger.info("Job %s succeeded (usage: %s)", job_id, usage_so_far())
    except chat_service.ConversationNotFoundError:
        store.mark_failed(job_id, "Conversation not found")
    except BudgetExceeded as exc:
        # A limit stopped the run. The client gets a clear reason, not a crash.
        logger.warning("Job %s stopped by budget (%s); usage: %s", job_id, exc.reason, usage_so_far())
        store.mark_failed(job_id, exc.user_message, usage=usage_so_far())
    except Exception:
        # Full details go to the log; the client only sees a generic message.
        logger.exception("Job %s failed", job_id)
        store.mark_failed(job_id, "Internal error while processing the question", usage=usage_so_far())
    finally:
        db.close()


def run_worker() -> None:
    setup_logging()
    settings = get_settings()
    settings.ensure_storage_dirs()

    if settings.scaling_enabled:
        from app.scaling.cache import install_llm_cache

        install_llm_cache(get_redis(), settings.scaling_cache_ttl_seconds)
        logger.info("LLM and embedding caches are on")
    else:
        logger.warning("SCALING_ENABLED is not true: LLM and embedding caches are off for this worker")


    graph = create_adaptive_rag()  # built once per worker process, like the API does at startup
    store = JobStore(get_redis(), settings.scaling_job_result_ttl_seconds)
    budget = BudgetLimits(
        ledger=UserTokenLedger(get_redis(), settings.scaling_max_tokens_per_user_per_day),
        max_tokens=settings.scaling_max_tokens_per_request,
        max_llm_calls=settings.scaling_max_llm_calls_per_request,
    )

    stop_requested = {"value": False}

    def _request_stop(signum, frame):
        # Ctrl+C / docker stop: finish the job in hand, then exit.
        stop_requested["value"] = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    logger.info("Worker started; waiting for jobs")
    while not stop_requested["value"]:
        job_id = store.pop(timeout_seconds=2)  # wakes every 2 s so it notices a stop request
        if job_id is None:
            continue
        process_job(store, graph, job_id, budget)
    logger.info("Worker stopped")


if __name__ == "__main__":
    run_worker()