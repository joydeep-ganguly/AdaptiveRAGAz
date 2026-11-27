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
| 5 | Token budgets per request and per user; cap on the grade/rewrite loop | Done |
| 6 | Retries with exponential backoff and jitter on model and search calls | Done |
| 7 | Observability: latency, queue depth, 429 rate, tokens per request | Done |
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

### Step 5 detail

| Sub-step | What | Files | Status |
|---|---|---|---|
| 5a | Budget machinery: per-user daily token ledger in Redis, `TokenBudgetCallback` (checks before each model call, counts tokens after), `BudgetedGraph` wrapper; cached calls cost 0 tokens; 10 tests | `app/scaling/budgets.py`, `cache.py` (small edit), `tests/unit/test_scaling_budgets.py` | Done |
| 5b | Wiring: worker applies per-request limits (tokens and model calls) and stores usage on the job; submit returns 429 + `Retry-After` when the user's daily budget is used up; rewrite loop capped by `SCALING_MAX_REWRITES_PER_REQUEST` in the scale profile; `usage` returned by `GET /chat/jobs/{id}`; 13 new tests (suite 373 passed); real check done (see results below) | `worker.py`, `router.py`, `jobs.py`, `schemas.py`, `config.py`, `rag_nodes.py`, tests | Done |

Design notes for Step 5:
- Three limits: tokens per request, model calls per request (stops runaway loops), tokens per user per UTC day. A request that crosses a limit has already made the call that crossed it, so it can overshoot by at most one call.
- When a limit stops a job, the job ends as `failed` with a clear message and its usage; the client is not shown a generic error.
- The plain profile keeps the original rewrite limit (`MAX_ITERATIONS = 3`); the scale profile uses `SCALING_MAX_REWRITES_PER_REQUEST`.
- Limits of this design: only LangChain model calls are counted. The Foundry web-search agent tokens and embeddings are not counted. Daily limits reset at UTC midnight (05:30 IST), a simplification. The daily check on submit uses tokens already recorded, so jobs already in the queue can push a user slightly over. A budget-stopped job leaves the user's message saved without an assistant reply, like other failed jobs.

### Step 6 detail

| Sub-step | What | Files | Status |
|---|---|---|---|
| 6 | Retry layer for the scale profile: jittered backoff helper, retry wrapper for embeddings, jittered retry policy for Azure AI Search, attempt count for Gemini calls taken from settings; 28 new tests (suite 401 passed); real check done (see results below) | `app/scaling/retries.py`, `app/agent/llm.py`, `app/agent/retrieval/azure_search_store.py`, `tests/unit/test_scaling_retries.py` | Done |

Design notes for Step 6:
- Reading the libraries first changed the plan. Gemini chat calls already retry in the Google SDK (exponential backoff with jitter); the Azure SDK retries with exponential backoff but no jitter and up to 10 retries (waits up to 120 s); the Gemini embeddings client does not retry at all. A second retry layer on top of the Gemini one would multiply attempts (4 x 6), so it was not added.
- What was added: `SCALING_RETRY_MAX_ATTEMPTS` is passed to the Gemini models as `max_retries` (it counts total attempts, including the first); a jittered policy for Azure AI Search with the same attempt limit; a retry wrapper for embeddings, placed outside the Redis cache so a cache hit never reaches it.
- Full jitter: each wait is a random time between 0 and min(max delay, base x 2^n), so workers that fail together do not retry together.
- Only transient errors are retried (408, 429, 500, 502, 503, 504, timeouts, dropped connections). Bad requests and authentication errors fail at once.
- Foundry web search is deliberately not retried: it has a time limit and a DuckDuckGo fallback, and a retry would hold a worker longer.
- All of it applies only when `SCALING_ENABLED=true`.
- Not tested with real failures: the Gemini chat retries and the Azure Search policy were checked in unit tests and by a normal end-to-end question, not by causing real outages. The embeddings wrapper was checked against the real embeddings model with simulated 503 errors.
- Library versions checked: `langchain-google-genai` 4.4.0, `google-genai` 2.28.0, `azure-core` 1.41.0, `azure-search-documents` 12.1.0.

### Step 7 detail

| Sub-step | What | Files | Status |
|---|---|---|---|
| 7a | Metric definitions in the Prometheus text format; queue depth and cache hits/misses read from Redis at scrape time; `GET /metrics` on the API (scale profile only); 7 tests | `app/scaling/metrics.py`, `metrics_router.py`, `main.py`, `requirements/scaling.txt` | Done |
| 7b | Submit outcomes counted in the submit endpoint; worker records queue wait, run time, tokens, model calls, jobs in progress and outcome, and serves its own `/metrics` on port 9100 (`SCALING_WORKER_METRICS_PORT`, 0 = off); 14 tests (suite 422 passed) | `router.py`, `worker.py`, `metrics.py`, `config.py`, tests | Done |

Metrics (all names start with `adaptiverag_`):

| Question | Metric |
|---|---|
| Is the queue backing up? | `queue_depth` (read from Redis at scrape time) |
| How often are users refused? | `submit_requests_total{outcome}`; 429 rate = `rate_limited` + `budget_exhausted` over all submits |
| How long do users wait, how long does a job run? | `job_queue_wait_seconds`, `job_duration_seconds` (histograms) |
| What does a question cost? | `job_tokens`, `job_llm_calls` (histograms) |
| Are the caches working? | `cache_requests_total{cache,outcome}` |
| How busy is a worker? | `jobs_in_progress`, `jobs_total{outcome}` |

Design notes for Step 7:
- Each process serves its own event numbers (API: submit outcomes; worker: job timings and tokens); shared state (queue depth, cache stats) is read from Redis when the API is scraped. Prometheus scrapes the API on port 8000 and each worker on port 9100.
- No label holds a user id, job id or question, so the number of time series stays small.
- Metrics never break a request: if Redis is down the queue and cache numbers are skipped for that scrape.
- Queue depth is also the signal the optional Container Apps/KEDA stage would scale workers on.

Known limits of this design:
- One API process per container. With several uvicorn processes in one container, each would report only its own counts (prometheus_client multiprocess mode is not set up). Scaling is by container replicas.
- Queue wait is measured with the clock of the machine that submitted the job and the clock of the worker; on separate hosts, clock differences add error.
- `/metrics` needs no login; keep it off the public internet.
- Two workers on one machine cannot both use port 9100; the second logs a warning and runs without metrics. In containers each worker has its own network, so this does not arise.
- Retry counts and Foundry web-search tokens are not exported.

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

### Token usage and budget behaviour (real services, 26 Nov 2026)

Question: "What is Amazon's revenue for the year 2023?" (document question, cache cleared first so every call is counted).

| Run | Limit set | Outcome | Model calls | Tokens |
|---|---|---|---|---|
| A (measure) | 1,000,000 per request | Succeeded in 42.6 s | 7 | 17,999 |
| B (per-question limit) | 1,000 per request | Failed in 6.3 s with "needed more processing than the per-question limit allows" | 3 | 1,175 |
| C (daily limit) | 1,000 per user per day | Submit refused: 429, `Retry-After` 41428 s (seconds to UTC midnight); no job queued | 0 | counter stayed at 1,175 |

A normal document question uses about 7 model calls and 18,000 tokens, mostly because prompts carry the retrieved passages. The per-request default is set to about 3x that (60,000 tokens); the per-call default of 20 is about 3x the 7 calls. These are single-question measurements, not an average over many questions.

### Retry check (real embeddings model, simulated outage, 26 Nov 2026)

First calls made to fail with a simulated 503; settings: 4 attempts, base 1.0 s, max 20 s.

| Case | Calls | Waits before retries | Outcome |
|---|---|---|---|
| 2 failures, then the real service answers | 3 | 0.6 s, 1.1 s | Succeeded (3072-number vector) in 2.7 s |
| Service stays down | 4 | 0.9 s, 0.3 s, 0.9 s | Gave up, error raised to the caller, 2.1 s |

The waits are random each time (jitter), so a repeat run shows different numbers. After the retry layer was added, one normal document question still succeeded: 7 model calls, 21,640 tokens, 78.2 s (the earlier run took 42.6 s with 17,999 tokens; the answer text differs, and Gemini latency varies between runs, so this is not read as an effect of the retry layer).

### Metrics check (real services, 27 Nov 2026)

Phase 1, no worker running, burst of 15 submissions by one user (bucket capacity 10):

| Metric | Value |
|---|---|
| `submit_requests_total{outcome="accepted"}` | 10 |
| `submit_requests_total{outcome="rate_limited"}` | 5 (the burst script showed 429 with Retry-After 2 for requests 11 to 15) |
| `queue_depth` | 10 (dropped to 0 after the queue was cleared) |

Phase 2, worker running, one real document question (Amazon revenue 2023):

| Metric (worker, port 9100) | Value |
|---|---|
| `jobs_total{outcome="succeeded"}` | 1 |
| `job_queue_wait_seconds_sum` | 0.008 s (the worker was idle) |
| `job_duration_seconds_sum` | 35.2 s |
| `job_tokens_sum` | 23,912 |
| `job_llm_calls_sum` | 7 |
| `jobs_in_progress` | 0 after the job |

The API side showed `accepted` rising from 10 to 11 and `queue_depth` back at 0. Tokens for the same question across three real runs: 17,999, 21,640 and 23,912, always 7 model calls, so the 60,000 default is still about 2.5x to 3.3x a normal question.

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
| 26 Nov 2026 | Step 5a done: token ledger, budget callback and graph wrapper (cached calls cost 0 tokens); suite 360 passed |
| 26 Nov 2026 | Step 5b done: per-request token and call limits in the worker, daily token budget with 429 on submit, rewrite cap in the scale profile, usage shown on the job; suite 373 passed. Real check: 7 calls and 17,999 tokens for a normal question; per-question and daily limits both enforced. Per-request default raised from 50,000 to 60,000 tokens. Step 5 complete |
| 26 Nov 2026 | Step 6 done: retry layer for the scale profile (jittered backoff; embeddings wrapper; jittered Azure Search policy; Gemini attempt count from settings; Foundry web search deliberately not retried); suite 401 passed; embeddings retry checked against the real model with simulated 503 errors; a normal question still succeeds end to end |
| 27 Nov 2026 | Step 7a done: `/metrics` on the API with queue depth and cache hits/misses read from Redis; suite 408 passed |
| 27 Nov 2026 | Step 7b done: submit outcomes counted; worker records queue wait, duration, tokens, model calls, jobs in progress and outcome, and serves `/metrics` on port 9100; suite 422 passed; real check matches the unit tests (10 accepted and 5 rate-limited in a burst of 15; one real job: 23,912 tokens, 7 calls, 35.2 s). Step 7 complete |
