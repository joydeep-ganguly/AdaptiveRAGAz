# AdaptiveRAGAz — Scaling Layer Plan

Branch: `feature/scaling-layer` (restore point: tag `pre-scaling-baseline`, commit `e7f6dcd`)
Started: 21 Nov 2026

## 1. Goal

Show how the agent is scaled for many concurrent users, and keep the scaling parts completely separate from the plain implementation.

- **Small setup (default):** `SCALING_ENABLED=false`. Synchronous `POST /chat/{id}/query`, no Redis, no worker, no extra latency. Exactly the app as it was before this branch.
- **Scale profile:** `SCALING_ENABLED=true`. Questions go through a queue, rate limits, budgets, caching and workers. Run with `docker-compose.scale.yml`.
- Both profiles run the **same graph code** (`chat_service.send_message` is reused unchanged), so any difference measured in load tests comes from the scaling layer alone.

## 2. Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Where the work lives | Same repo, new branch; all new code in `backend/app/scaling/` |
| 2 | Queue broker | Redis (list-based queue; also used for rate limits, cache, idempotency keys). Azure Service Bus described as the cloud swap-in |
| 3 | Database for the scale profile | Postgres for app data and the LangGraph checkpointer, config-driven. SQLite stays the default for the small setup |
| 4 | Result delivery | Polling first (`GET /chat/jobs/{id}`), SSE later if time allows |
| 5 | Worker logic | Reuses `chat_service.send_message` unchanged |
| 6 | Scaling dependencies | Separate `requirements/scaling.txt`; not needed for the small setup |

## 3. Rules for this work

- Exact edits, one step at a time; wait for the go-ahead at each step.
- Keep the 292 existing tests passing. Keep Chroma as a fallback.
- Never `git add .` / `git add -A` on this branch (the tracked `chroma.sqlite3` changes whenever the app runs). Add files by name.
- Keep scripts simple (PowerShell is not a strong area); explain each command.
- Credentials and keys are entered by the owner.
- Honest framing: load-tested at small scale, with a written design for how it extends. Never claim production at lakhs of users.

## 4. Steps and status

| Step | What | Status |
|---|---|---|
| 0 | Restore tag, branch, baseline tests (292 passed) | Done |
| 1 | Scaling settings in `config.py` and `.env.example` (all off by default) | Done |
| 2 | Queue and worker (submit, queue, work, poll) | Done |
| 3 | Per-user token-bucket rate limit (429 + `Retry-After`), bounded queue with load shedding | Done |
| 4 | Idempotency keys; caches for embeddings and repeated LLM calls | Done |
| 5 | Token budgets per request and per user; cap on the grade/rewrite loop | Not started |
| 6 | Retries with exponential backoff and jitter on model and search calls | Not started |
| 7 | Observability: latency, queue depth, 429 rate, tokens per request | Not started |
| 8 | Postgres support; `docker-compose.scale.yml` (API, Redis, workers, Postgres) | Not started |
| 9 | Load tests (stubbed LLM/search first, then a small real-services run); results table | Not started |
| 10 | Architecture doc with diagram and "how this scales to 100,000 concurrent users" | Not started |
| Optional | SSE results; Container Apps with KEDA on queue depth; ~30-question evaluation set | Not started |

### Step 2 detail

| Sub-step | What | Files | Status |
|---|---|---|---|
| 2a | Dependencies (`redis`, `fakeredis`) and Docker available | `requirements/scaling.txt`, `requirements/dev.txt` | Done |
| 2b | Redis connection and job store; start local Redis | `app/scaling/redis_client.py`, `jobs.py` | Done |
| 2c | Worker process (`python -m app.scaling.worker`) | `app/scaling/worker.py` | Done |
| 2d | Router and schemas, mounted in `main.py` only when scaling is enabled | `app/scaling/router.py`, `schemas.py`, `main.py` | Done |
| 2e | Unit tests with fake Redis (21 tests); real end-to-end run done in 2d | `tests/unit/conftest.py`, `test_scaling_jobs.py`, `test_scaling_worker.py`, `test_scaling_router.py` | Done |

Design notes for Step 2:
- Submit: `POST /chat/{conversation_id}/jobs` checks ownership, enqueues, returns `202` with `job_id`.
- Poll: `GET /chat/jobs/{job_id}` returns `queued | running | succeeded | failed` and the answer; only the owner can read a job.
- Jobs are Redis hashes with an expiry; the queue is a Redis list.
- One worker only until Step 8 (SQLite cannot handle several writer processes).
- Test note: this FastAPI version lists included routers as wrapper objects on `app.routes`, so route checks use `app.openapi()["paths"]`.
- Known gap to address and document: two jobs on the same conversation at once would race on the same LangGraph thread; needs a per-conversation lock.
- Known gap to document: a job being worked on is lost if the worker dies mid-job; a processing list with a visibility timeout is the production answer.

### Step 3 detail

| Sub-step | What | Files | Status |
|---|---|---|---|
| 3a | Token-bucket limiter as one atomic Lua script in Redis; 5 unit tests (needs `fakeredis[lua]`); checked on real Redis | `app/scaling/rate_limit.py`, `tests/unit/test_scaling_rate_limit.py` | Done |
| 3b | Wired into `POST /chat/{id}/jobs`: 429 + `Retry-After` per user, 503 + `Retry-After` when the queue is full; 3 router tests; real burst check | `app/scaling/router.py`, tests | Done |

Order of checks on submit, cheapest first: rate limit, then queue depth, then database ownership check, then enqueue.

Real burst check (queue limit 5, bucket 10, no worker): requests 1-5 got 202, 6-10 got 503 (`Retry-After: 5`), 11-15 got 429 (`Retry-After: 2`).

Design notes for Step 3:
- A request refused with 503 still uses a rate-limit token. The limiter measures what a user sends, not what is accepted.
- The queue depth check is approximate: a few extra jobs can slip in while requests race. An atomic version is a small Lua script.
- Polling (`GET /chat/jobs/{id}`) has no limit of its own yet. To address in the architecture doc (caching, SSE, or its own bucket).
- Redis errors on submit return 503 "Queue unavailable".

### Step 4 detail

| Sub-step | What | Files | Status |
|---|---|---|---|
| 4a | Idempotency keys on submit (`Idempotency-Key` header); 14 new tests; real check: 3 requests, 1 job queued | `app/scaling/idempotency.py`, `jobs.py`, `router.py`, tests | Done |
| 4b | Embedding cache and LLM-response cache in Redis; 15 new tests; real check done (see results below) | `app/scaling/cache.py`, `app/agent/llm.py` (small hook), `worker.py`, tests | Done |

Design notes for Step 4:
- Idempotency: the key is scoped per user. A repeated key returns the original job id and does not consume a rate-limit token or queue a second job. The same key with a different message or conversation is rejected with 422. The key is claimed atomically (`SET NX`) so two simultaneous retries cannot both create a job.
- Caching works at the level of single calls, not whole answers. A cached final answer would skip the LangGraph run, and the conversation memory (checkpoint) would then miss that turn, breaking follow-up questions. The LLM cache key is the full prompt (including the conversation so far), so a hit is always correct. Message and tool-call ids are removed before building the key, because they change on every run.
- Both caches fail open: if Redis is down the real call is made and the question still works. The caches are active only when `SCALING_ENABLED=true` (the worker and the API both need the flag).
- Hit and miss counts are kept in Redis (`cache_stats`) for the results table.
- Expected hit pattern: an identical question in a new conversation hits on the early deterministic steps; the same question inside a long conversation usually misses because the history differs. State this honestly in the write-up.

## 5. Local environment notes

- Start local Redis: `docker run -d --name redis-adaptive -p 6379:6379 redis:7`
- Check it: `docker exec redis-adaptive redis-cli ping` (expect `PONG`)
- Later start/stop: `docker start redis-adaptive` / `docker stop redis-adaptive`
- Inside Docker Compose the Redis host is `redis`, not `localhost`; the compose file sets `REDIS_URL` itself.

## 6. Results table (filled in Step 9)

| Test | Mode | Concurrency | Requests/s | p95 latency | 429 rate | Peak queue depth | Tokens/request |
|---|---|---|---|---|---|---|---|
| Baseline (sync) | LLM/search stubbed | | | | | n/a | |
| Scaled (1 worker) | LLM/search stubbed | | | | | | |
| Scaled (N workers) | LLM/search stubbed | | | | | | |
| Scaled (small run) | Real services | | | | | | |

State clearly in the write-up which rows used stubs and which used real services (Free-tier AI Search and Gemini rate limits will throttle the real run).

### Cache effect (real services, single user, 1 worker, 24-25 Nov 2026)

Same question asked in two new conversations: "What is Amazon's revenue for the year 2023?" (document question over the indexed filings). The cache was cleared before the first run.

| Run | Time | LLM cache | Embedding cache |
|---|---|---|---|
| 1 (cold) | 39.2 s | 0 hits, 7 misses | 0 hits, 1 miss |
| 2 (repeat) | 0.6 s | 7 hits (total 7 hits, 7 misses) | 1 hit (total 1 hit, 1 miss) |

Both runs returned the same answer. This is the best case: an identical question, no earlier conversation history, within the cache lifetime (`SCALING_CACHE_TTL_SECONDS`, 1 hour by default). A question asked inside a long conversation usually misses, because the prompt includes the history. This was one user and one worker, not a load test.

## 7. Change log

| Date | Change |
|---|---|
| 21 Nov 2026 | Plan agreed; Step 0 done (tag `pre-scaling-baseline`, branch `feature/scaling-layer`, 292 tests passing) |
| 21 Nov 2026 | Step 1 done: scaling settings added, disabled by default; committed and pushed |
| 22 Nov 2026 | Step 2a done: `redis` and `fakeredis` installed; Docker 29.8.0 available. Plan saved to this file |
| 22 Nov 2026 | Step 2b done: Redis connection and `JobStore` (queue = Redis list, jobs = Redis hashes with expiry) |
| 23 Nov 2026 | Step 2c done: worker (`python -m app.scaling.worker`) reuses `chat_service.send_message`; unexpected errors are logged, clients see a generic message |
| 23 Nov 2026 | Step 2d done: `POST /chat/{id}/jobs` (202) and `GET /chat/jobs/{job_id}`; mounted only when `SCALING_ENABLED=true`; verified end to end with a real question and with the flag off |
| 23 Nov 2026 | Step 2e done: 21 unit tests; full suite 313 passed |
| 24 Nov 2026 | Step 3a done: per-user token bucket (Lua, atomic); suite 318 passed |
| 24 Nov 2026 | Step 3b done: 429 rate limit and 503 load shedding on submit; real burst check matched; suite 321 passed. Step 3 complete |
| 24 Nov 2026 | Step 4 started (idempotency keys, then caches) |
| 24 Nov 2026 | Step 4a done: `Idempotency-Key` on submit (replay returns the original job, no rate-limit token used; same key with a different request gets 422; key released if queueing fails); suite 335 passed |
| 25 Nov 2026 | Step 4b done: Redis embedding cache and LLM-response cache (fail open, enabled only with `SCALING_ENABLED=true`); suite 350 passed; repeat of a document question went from 39.2 s to 0.6 s with 7/7 LLM and 1/1 embedding hits. Step 4 complete |
