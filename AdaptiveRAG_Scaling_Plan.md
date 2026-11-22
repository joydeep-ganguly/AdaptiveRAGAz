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
| 2 | Queue and worker (submit, queue, work, poll) | In progress |
| 3 | Per-user token-bucket rate limit (429 + `Retry-After`), bounded queue with load shedding | Not started |
| 4 | Idempotency keys; cache for repeated questions and embeddings | Not started |
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
| 2b | Redis connection and job store; start local Redis | `app/scaling/redis_client.py`, `jobs.py` | Next |
| 2c | Worker process (`python -m app.scaling.worker`) | `app/scaling/worker.py` | Not started |
| 2d | Router and schemas, mounted in `main.py` only when scaling is enabled | `app/scaling/router.py`, `schemas.py`, `main.py` | Not started |
| 2e | Unit tests with fake Redis; real end-to-end run | `tests/unit/scaling/` | Not started |

Design notes for Step 2:
- Submit: `POST /chat/{conversation_id}/jobs` checks ownership, enqueues, returns `202` with `job_id`.
- Poll: `GET /chat/jobs/{job_id}` returns `queued | running | succeeded | failed` and the answer; only the owner can read a job.
- Jobs are Redis hashes with an expiry; the queue is a Redis list.
- One worker only until Step 8 (SQLite cannot handle several writer processes).
- Known gap to address and document: two jobs on the same conversation at once would race on the same LangGraph thread; needs a per-conversation lock.
- Known gap to document: a job being worked on is lost if the worker dies mid-job; a processing list with a visibility timeout is the production answer.

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

## 7. Change log

| Date | Change |
|---|---|
| 21 Nov 2026 | Plan agreed; Step 0 done (tag `pre-scaling-baseline`, branch `feature/scaling-layer`, 292 tests passing) |
| 21 Nov 2026 | Step 1 done: scaling settings added, disabled by default; committed and pushed |
| 22 Nov 2026 | Step 2a done: `redis` and `fakeredis` installed; Docker 29.8.0 available. Plan saved to this file |
