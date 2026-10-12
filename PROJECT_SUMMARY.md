# AdaptiveRAG — Project Summary (context for the deployment work)

Written 2026-10-04 from the full project folder (`D:\CVProjects\AdaptiveRAG`).
Everything under "Current state" was read from the files on disk at that time. Items marked **(observation)** or
**(open question)** are readings of the code, not decisions that have been made.
Updated 2026-10-06 for Azure Sub-Plan 1 (code-execution datasets moved to Azure Blob Storage). The Azure working copy
is `D:\CVProjects\AdaptiveRAGAz`; section 16 is the progress record, and sections 2-15 are adjusted where Sub-Plan 1
changed things. The original folder was not modified.
Updated 2026-10-07 for Azure Sub-Plan 2 (web search through a Microsoft Foundry agent), two small frontend fixes and the push of the Azure
working copy to its own GitHub repository; sections 2-4 and 8-16 are adjusted, and section 16 holds the record.

---

## 1. Status in one paragraph

The project goal in `AdaptiveRAG_Implementation_Plan.md` is **complete**: the notebook-based adaptive RAG was
turned into a FastAPI backend plus a Streamlit frontend, with JWT auth, SQLite persistence, admin roles, file
uploads, a pytest suite, a README, dev run scripts, and a git repository. The first push to
`https://github.com/joydeep-ganguly/AdaptiveRAG.git` (branch `main`) has finished. **Deployment has deliberately not
been started**; the original plan said to stop at a "deployable stage" and to say nothing about local or cloud hosting.
That is the next piece of work.
**Update 2026-10-06:** that work is under way. The move to Azure is incremental, one sub-plan at a time, and whether the
whole project ends up on Azure is decided later. Sub-Plan 1 (code-execution datasets to Azure Blob Storage) and Sub-Plan 2
(web search through a Foundry agent, DuckDuckGo as fallback) are complete; see section 16. The Azure working copy now
has its own git repository, pushed to `https://github.com/joydeep-ganguly/AdaptiveRAGAz.git` (section 11).

---

## 2. What the system does

One LangGraph state machine (`AgentState`) routes each user question to one of four paths:

| Route | Nodes | Backing tech |
|---|---|---|
| `retrieve` | retrieve → grade_documents → generate, with transform_query retry loop and self-RAG hallucination/answer-quality checks (max 3 iterations) | Chroma persisted index (`storage/chroma_financial_db`, collection `financial_docs`), Gemini embeddings |
| `sql_agent` | ReAct agent with schema/generate/validate/execute/fix tools | SQLite employee DB (`employees_db-full-1.0.6.db`) |
| `web_search` | `web_search_node`: one call to the Foundry agent `adaptiverag-websearch`; on failure, the original ReAct agent as fallback | Microsoft Foundry agent with the built-in Web Search tool (answer plus a Sources list); fallback DuckDuckGo via `ddgs` |
| `code_execution` | ReAct agent with `list_code_data_files`, `upload_file` and `run_python_code` | E2B sandbox, datasets from Azure Blob Storage (`code_data/` prefix) |

- Models are in `agent/llm.py`: chat model `gemini-3-flash-preview`, embeddings `gemini-embedding-001`. Three chat
  clients (plain, reasoning with `thinking_level="medium"` and `include_thoughts`, SQL) plus embeddings, each
  `@lru_cache`d.
- Short-term memory is LangGraph's `SqliteSaver` on `checkpoints.db`, keyed by `thread_id`. The app uses the
  conversation UUID as the thread id.
- The financial corpus is 10-K/10-Q/8-K PDFs for Amazon, Apple and Google, stored in `data/rag_data/{company}/`.

---

## 3. Architecture

```
Streamlit (frontend/)  --HTTP + Bearer JWT-->  FastAPI (backend/app)
                                                  |-- app.db            users, conversations, messages (SQLAlchemy)
                                                  |-- LangGraph agent   built once at startup, stored on app.state.graph
                                                  |-- checkpoints.db    LangGraph per-thread memory
                                                  |-- Chroma dir        financial doc vectors
                                                  |-- employees db      SQL agent target
                                                  |-- Azure Blob        code-execution datasets (code_data/ prefix)
                                                  |-- Foundry agent     web search (adaptiverag-websearch), by name, stateless
                                                  `-- E2B / DuckDuckGo (fallback) / Gemini  (external)
```

The frontend never touches the DB, the agent or any external API directly. It only calls FastAPI through
`frontend/api_client.py` (httpx), which turns HTTP and network errors into one `ApiError`.

Layering inside `backend/app`: `api/routers` → `services` → `db/crud` and `auth/security`, with `agent/` as the
ported notebook "brain". Services raise plain Python exceptions (`UserAlreadyExistsError`,
`InvalidCredentialsError`, `ConversationNotFoundError`); routers translate them to HTTP status codes.

---

## 4. Current repo layout (as read from disk)

```
AdaptiveRAG/
├── .git/  .gitignore  .gitattributes (Git LFS for *.db, *.sqlite3)  LICENSE  README.md
├── AdaptiveRAG_Implementation_Plan.md      original plan (kept intact)
├── AdaptiveRAG_Agent_Wiring_Addendum.md    agent/ import-wiring addendum
├── AdaptiveRAG_Phase2_Plan.md              admin roles, data split, uploads
├── PROJECT_SUMMARY.md                      this file
├── AdaptiveRAG_Azure_SubPlan1_CodeData_Blob.md   Azure Sub-Plan 1 (code data to Blob): design, steps, progress log
├── AdaptiveRAG_Azure_SubPlan2_WebSearch_Foundry.md   Azure Sub-Plan 2 (web search via Foundry agent): design, timings, steps, progress log
├── scripts/  seed_admin_user.py  run_backend_dev.{sh,ps1}  run_frontend_dev.{sh,ps1}
├── backend/
│   ├── .env (not committed)  .env.example  pyproject.toml  requirements/{base,backend,dev}.txt
│   ├── app/
│   │   ├── main.py  config.py  deps.py  logging_config.py
│   │   ├── agent/  state.py schemas.py prompts.py llm.py router.py graph.py checkpointer.py debug_utils.py
│   │   │   ├── nodes/      rag_nodes.py sql_agent_node.py web_search_node.py code_execution_node.py
│   │   │   ├── tools/      doc_tools.py sql_tools.py web_tools.py code_tools.py foundry_web_search.py
│   │   │   └── retrieval/  vectorstore.py filters.py ranking.py ingestion.py
│   │   ├── api/routers/    auth.py conversations.py chat.py media.py health.py admin.py
│   │   ├── api/schemas/    auth.py conversation.py chat.py admin.py
│   │   ├── auth/           security.py oauth2.py
│   │   ├── db/             models.py session.py crud.py init_db.py
│   │   ├── services/       auth_service.py chat_service.py
│   │   └── storage/        __init__.py code_data_store.py   (Azure Blob access for the code-execution datasets)
│   ├── data/rag_data/{amazon,apple,google}/   PDFs
│   ├── (data/code_data/ removed in Sub-Plan 1: the 4 datasets now live in Azure Blob, stadaptiverag32/code-execution/code_data/)
│   ├── storage/
│   │   ├── sqlite/         app.db (~32 KB), checkpoints.db (~5.3 MB), employees_db-full-1.0.6.db (~290.7 MB)
│   │   ├── chroma_financial_db/
│   │   ├── media/          generated chart PNGs (runtime output, git-ignored)
│   │   └── debug_logs/     per-conversation debug output (runtime, git-ignored)
│   ├── tests/  conftest.py  unit/  integration/
│   └── test_agent_manual.py  test_auth_security_manual.py  test_services_manual.py  test_code_data_store_manual.py  test_code_agent_manual.py  test_foundry_web_search_manual.py  test_web_search_agent_manual.py   (manual sanity scripts, not collected by pytest)
└── frontend/
    ├── .env (not committed)  .env.example  requirements.txt  config.py  api_client.py  streamlit_app.py
    ├── components/  auth_forms.py chat_window.py sidebar.py
    └── pages/       1_Login.py 2_Chat.py 3_Admin.py
```

`NotebookImplementation/` (the original notebooks, scripts and data) was in the repo root earlier but is **not present
in the latest listing**. The Phase 2 ingestion code was ported from its `Page RAG - Data_Ingestion.ipynb`.

The Azure working copy `D:\CVProjects\AdaptiveRAGAz` has this layout with the Sub-Plan 1 changes marked above. It was copied
without `.git/`; since 2026-10-07 it has its own git repository with a single initial commit (section 11).

---

## 5. How the build went

### Original plan, section 6 (points 1–13), all done
1. Scaffold folders and requirements.
2. `config.py` — `pydantic-settings`, every storage path resolved to an absolute path from `backend/`.
3. Static assets copied into `backend/storage` and `backend/data`.
4. Agent ported, then sanity-checked with `test_agent_manual.py`.
5. DB layer (SQLAlchemy models, session with `PRAGMA foreign_keys=ON`, crud, `init_db`).
6. Auth layer (bcrypt hashing, JWT with `sub` = username, OAuth2 password bearer).
7. Services.
8. FastAPI routers, with the graph built once in `lifespan`.
9. Tests.
10. Streamlit frontend.
11. `scripts/seed_admin_user.py`.
12. README and dev run scripts (`.sh` and `.ps1`).
13. Git init, LFS and push.

### Section 4 multi-user fixes, all closed
1. E2B sandbox is now per conversation: a dict keyed by `conversation_id`, lock-guarded, with idle eviction (20 min).
2. Debug logs go through `debug_utils.write_debug_log`, namespaced per conversation and a no-op unless `DEBUG_MODE`.
3. Every path comes from `get_settings()`.
4. The chat endpoint is a plain `def`, so FastAPI runs the blocking `graph.invoke` in its thread pool.
5. `generate_node` finds the text block via `_extract_text_content` instead of indexing `content[1]`.
6. `load_dotenv` is gone from `agent/`; `config.py` is the only env reader.
7. Chart images are served by `GET /media/{filename}` with a path-traversal guard and a conversation-ownership check
   (filename format `{conversation_id}_{timestamp}_chart_{idx}.png`).

### Chart delivery to the UI (added after point 10)
`chat_service.send_message` snapshots `graph.get_state(config)` before invoking, then scans only this turn's new
`ToolMessage`s for `"Chart saved to ..."`. It returns `(assistant_message, media_filenames)`. `ChatQueryResponse`
carries `media_filenames`, and `chat_window.py` fetches and shows them with `st.image`. It deliberately does not call
`st.rerun()` after a send, or the chart would vanish.

### Phase 2 (all 13 points done)
- `users.type` column (`"admin"` | `"user"`). The first registered user ever becomes admin. After that, public
  `/auth/register` always creates `"user"` and never reads a `type` from the request. Only `POST /admin/users` accepts a type.
- `get_current_admin_user` dependency (token → user → active check → admin check, 403 on failure).
- `data/` split into `rag_data/` (PDFs) and `code_data/` (csv/xlsx); `config.py` has `rag_data_dir` and `code_data_dir`.
  (Sub-Plan 1 later moved `code_data/` to Azure Blob and removed `code_data_dir`; see section 16.)
- Admin-only uploads. Code data: `.csv`/`.xlsx` into `code_data/` (since Sub-Plan 1: into Azure Blob). RAG data: PDF plus form fields (company, doc type,
  fiscal year, optional quarter). The backend builds the filename itself (`{company} {doc_type} [{quarter}] {year}.pdf`),
  saves to `rag_data/{company}/`, then runs `ingest_docs_in_vectordb` on that one file.
- `retrieval/ingestion.py` is ported from the notebook. It uses the shared `vector_store` and recomputes the
  processed-hash set on every call. It returns `{"skipped": bool, "pages_ingested": int}`. Docling is pinned
  (`docling==2.66.0`, `docling-core==2.67.1`, `docling-parse==4.7.3`).
- Frontend: `3_Admin.py` (tabs: create user, upload analysis data, upload RAG document) is gated on
  `st.session_state.user_type == "admin"`.
- The `app.db` reset (delete, `init_db`, `seed_admin_user`) was done once and the first user came out as admin.

---

## 6. API surface

| Method | Path | Auth |
|---|---|---|
| GET | `/health`, `/readiness` | none |
| POST | `/auth/register` | none |
| POST | `/auth/token` | none (OAuth2 password form) |
| GET | `/auth/me` | user |
| POST, GET | `/conversations` | user |
| GET | `/conversations/{id}/messages` | owner |
| DELETE | `/conversations/{id}` | owner |
| POST | `/chat/{conversation_id}/query` → `{conversation_id, role, content, media_filenames}` | owner |
| GET | `/media/{filename}` | owner of the conversation encoded in the filename |
| POST | `/admin/users` | admin |
| POST | `/admin/uploads/code-data` | admin |
| POST | `/admin/uploads/rag-data` | admin |

Ownership failures return the same 404 as "not found", so IDs can't be probed.

Since Sub-Plan 1, `POST /admin/uploads/code-data` stores the file in Azure Blob Storage (a same-name upload silently
overwrites) and returns 400 for an invalid name or extension, 413 above the size limit (50 MB by default) and 502 when
storage fails.

## 7. Data model (`app.db`)
- `users`: id, username (unique), email (unique), hashed_password, `type`, is_active, created_at
- `conversations`: id (UUID string, doubles as the LangGraph `thread_id`), user_id → users, title, created_at, updated_at
- `messages`: id, conversation_id → conversations, role, content, created_at
- Cascade deletes are set on both relationships. `init_db.py` only calls `create_all`, so there is **no migration tool**;
  schema changes so far were handled by deleting `app.db` and recreating it.

## 8. Configuration
Backend `.env` keys: `GOOGLE_API_KEY`, `E2B_API_KEY`, `JWT_SECRET_KEY` (all three required, no defaults, startup fails
without them), `JWT_ALGORITHM=HS256`, `ACCESS_TOKEN_EXPIRE_MINUTES=60`, `APP_DB_PATH`, `CHECKPOINT_DB_PATH`,
`EMPLOYEES_DB_PATH`, `CHROMA_DIR`, `RAG_DATA_DIR`, `DEBUG_MODE`. Paths are relative to `backend/` and
resolved in `config.py`. Frontend `.env`: `API_BASE_URL` (default `http://localhost:8000`).
Azure keys (Sub-Plan 1): `AZURE_STORAGE_ACCOUNT_NAME` (optional at startup, needed for code execution and dataset upload),
`AZURE_STORAGE_CONTAINER` (default `code-execution`), `AZURE_STORAGE_CODE_DATA_PREFIX` (default `code_data/`),
`CODE_DATA_MAX_UPLOAD_MB` (default 50). Auth is `DefaultAzureCredential` (`az login` locally); no keys or connection strings.
Foundry keys (Sub-Plan 2, all optional): `FOUNDRY_PROJECT_ENDPOINT` (blank means DuckDuckGo only), `FOUNDRY_WEB_SEARCH_AGENT_NAME`
(default `adaptiverag-websearch`), `FOUNDRY_REQUEST_TIMEOUT_SECONDS` (default 45; `.env` uses 40),
`WEB_SEARCH_FALLBACK_ENABLED` (default true). The real `.env` holds the endpoint; `.env.example` leaves it blank.

## 9. Tests
`cd backend && pytest`, with `pyproject.toml` setting `pythonpath=["."]` and `testpaths=["tests"]`. The latter keeps
pytest from collecting the three root-level manual scripts, which call live APIs.
- Unit: security, crud, ranking, router (fake LLM), sql_tools validation, admin auth logic, `code_data_store` (fake
  container), code-execution tools/node/prompt (fake store, sandbox and `create_agent`).
- Integration (TestClient): auth, conversations, chat, admin endpoints.
- Fixtures: in-memory SQLite with `StaticPool`; `FakeGraph` (with `get_state`, so the chart-scoping logic works);
  `client` is deliberately **not** used as a context manager, so `lifespan` (the real graph build) never runs;
  `non_admin_auth_headers` exists because the first registered user is always admin; `admin_upload_dirs` monkeypatches
  `app.api.routers.admin.get_settings` to a tmp RAG dir; `fake_code_data_blob` is an in-memory stand-in for the Azure
  container behind `code_data_store` (1 MB limit) for the code-data upload tests.
- Sub-Plan 2 tests: `test_foundry_web_search.py` (response parsing, Sources formatting, error mapping, cached client, the
  wall-clock deadline), `test_web_search_node.py` (Foundry success, failure with and without fallback, not configured, the
  DuckDuckGo helper failing) and `test_web_tools.py` (`ddgs` exceptions, empty results, formatting), all with fakes.
  The suite stood at 144 passed at the end of Sub-Plan 2.
- Never run in tests: live Gemini, E2B, DuckDuckGo, Azure, Foundry, docling ingestion, the real `app.db` or `data/` folders.
- The suite passed at the end of the original plan's point 9; the Phase 2 tests were added afterwards.
- Not covered by automated tests: the `/media` endpoint and the chart-filename extraction in `chat_service`.

## 10. Running it (dev)
- Backend (from `backend/`, venv active): `pip install -r requirements/dev.txt`, copy `.env.example` to `.env` and fill
  it in, `python -m app.db.init_db`, `python ../scripts/seed_admin_user.py`, `uvicorn app.main:app --reload --port 8000`.
- Azure (since Sub-Plan 1): run `az login` once and set `AZURE_STORAGE_ACCOUNT_NAME` in `backend/.env`; the signed-in identity
  needs the Storage Blob Data Contributor role on the storage account.
- Foundry (since Sub-Plan 2, optional): set `FOUNDRY_PROJECT_ENDPOINT` in `backend/.env`; the same `az login` identity needs the
  Foundry User role on the project. Blank endpoint means web questions use DuckDuckGo only.
- Frontend (from `frontend/`): `pip install -r requirements.txt`, copy `.env.example` to `.env`, `streamlit run streamlit_app.py`.
- `scripts/run_*_dev.{sh,ps1}` just `cd` and start the server. `scripts/seed_admin_user.py` fixes its own `sys.path`, so it
  runs from any working directory. It prompts (hidden password) or reads `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`.
- `backend.txt` pins `bcrypt==4.0.1` (added manually; it keeps passlib's bcrypt backend working).

## 11. Git
- Remote: `https://github.com/joydeep-ganguly/AdaptiveRAG.git`, branch `main`.
- `.gitattributes` sends `*.db` and `*.sqlite3` to Git LFS. That covers the 290 MB employee DB, which is over GitHub's
  100 MB limit, and `chroma.sqlite3`. `.gitignore` excludes `.env` (but not `.env.example`), venvs, `__pycache__`,
  `*.db-shm`/`*.db-wal`, `backend/storage/debug_logs/`, `backend/storage/media/`, and IDE folders.
- **Azure working copy (2026-10-07):** its own repository, separate from the one above. Remote
  `https://github.com/joydeep-ganguly/AdaptiveRAGAz.git` (public, was empty), branch `main`, one commit
  `1c940b6` "Initial commit: AdaptiveRAG with Azure Blob datasets and Foundry web search", pushed with `git init -b main`,
  `git add .`, `git commit`, `git remote add origin`, `git push -u origin main`. Confirmed with `git ls-remote origin`
  (`HEAD` and `refs/heads/main` both `1c940b6`).
  - It uses the same `.gitattributes` (LFS), and `git lfs ls-files` lists only `backend/storage/chroma_financial_db/chroma.sqlite3`
    and `backend/storage/sqlite/employees_db-full-1.0.6.db`.
  - `.gitignore` gained `backend/storage/sqlite/app.db` and `backend/storage/sqlite/checkpoints.db`, because they hold test
    accounts (password hashes) and chat history. They are not in this repository; `python -m app.db.init_db` recreates them. (The first
    repository tracks them.)
  - The repository is public and the plan and summary files name the Azure resources (storage account, Foundry project and agent,
    project endpoint); none of this is a credential. `.env` stays ignored and the `.env.example` files are blank.
  - Pushing the large files again uses the GitHub LFS quota a second time.
- Two push problems were hit and fixed: the local branch was `master` (fixed with `git branch -M main`), and the GitHub
  repo already held a LICENSE commit (fixed with `git pull origin main --allow-unrelated-histories`). `LICENSE` is now in the repo.

---

## 12. Design decisions worth preserving
- Config is read in one place (`app/config.py`); no module calls `os.getenv` or `load_dotenv`.
- The compiled graph is passed into `chat_service.send_message` as a parameter and built once in `lifespan`.
- Blocking endpoints (`/chat/.../query`, `/admin/uploads/rag-data`) are `def`, never `async def`.
- Authorization (ownership, admin) is separate from authentication, and both are enforced in dependencies/services, not in the UI.
- Public registration must never honour a caller-supplied `type`.
- Types like `DocType` and `FiscalQuarter` in `agent/schemas.py` are the single source of truth for both ingestion
  metadata and query-time filtering. Keep the upload form values identical to them.
- Tests never call live external services.
- Azure auth is `DefaultAzureCredential` only (no connection strings). `code_data_store` builds its blob client lazily and
  never at import, because `tests/conftest.py` imports `app.main`, which imports the agent graph and `code_tools`.
- All code-data rules (bare `.csv`/`.xlsx` names, size limit, blob prefix) live in `app/storage/code_data_store.py`; the
  admin router and the agent tools only translate its errors.
- All Foundry access sits in `app/agent/tools/foundry_web_search.py`: lazy cached client (never at import), one error type
  (`FoundryWebSearchError`) at the boundary, and a wall-clock deadline (worker thread plus `join`) because the OpenAI client's
  `timeout` only limits each network phase. `web_search_node` decides between Foundry, the DuckDuckGo fallback and the
  "unavailable" message; the fallback is wrapped so it can never crash the graph.
- The Foundry call is stateless (no Foundry conversation) and by agent name only, so edits to the agent in Foundry apply at once.
- A new chat conversation is created when its first message is sent, not when the sidebar button is pressed, so it can be
  titled from that message (no backend rename route exists).

## 13. Known gaps and limitations
- **Chart persistence:** charts shown in a turn are not stored anywhere. Reopening a conversation shows the text but not
  the chart. Fixing it needs a column on `messages` plus a schema change.
- **E2B sandboxes are an in-process dict.** They are not shared between processes and not cleaned up on shutdown.
- **No schema migrations** (see section 7).
- **RAG re-upload:** a corrected version of an already-ingested filing gets a different hash and is added alongside the
  old chunks rather than replacing them. There is no delete or replace flow for ingested documents.
- **First-user race:** two simultaneous registrations on an empty DB could both become admin (accepted for an internal tool).
- **Validation is keyword-based:** `validate_sql_query` rejects non-SELECT and a list of dangerous keywords. It is not a
  read-only connection.
- Retrieval, SQL and web paths still use plain `print()` for much of their diagnostics; only `setup_logging()` basic
  config exists.
- No token-expiry or auto-logout handling was written in the frontend **(observation)**.
- No Dockerfile, CI configuration or process-manager config exists in the repo.
- **Code-data blobs:** the app can list, download and upload (silent overwrite) but not delete; removal is done in the Portal
  or with `az storage blob delete`. Files above the size limit are also refused on download.
- **Web search (Sub-Plan 2):** the agent's instructions live in Foundry, not in the repo, so they can drift from
  `FALLBACK_SYSTEM_PROMPT`. The DuckDuckGo fallback can return outdated news presented as current (accepted).
  Foundry citations are often site landing pages rather than article pages. Web queries leave the Azure compliance boundary
  (Grounding with Bing) and the tool costs extra. A call is capped by `FOUNDRY_REQUEST_TIMEOUT_SECONDS`; timing varies (typically
  20-30 s, one 154 s outlier before the deadline existed).
- **Conversation titles** are the first 40 or so characters of the first message, not a generated summary; existing
  conversations keep their old title, and there is no rename.
- **Code-execution agent** lost `grep_search` in Sub-Plan 1 (blob listing replaces `glob_search`). `get_dataset_info` in
  `code_tools.py` is unused and has a bug (`filepath.endswith == 'csv'` compares a method to a string).
- `/readiness` still checks only `app.db`; it does not check Azure Blob connectivity.

## 14. Deployment-relevant observations (for the deployment work)
These are facts about the current code that a deployment design must account for. None of them are decisions.
- **Heavy startup:** `lifespan` builds the full graph (Chroma open, embeddings client, SQL DB connection,
  checkpointer) once per process. Run more than one worker and each gets its own copy.
- **State that lives in-process or on local disk:** the E2B sandbox registry, `checkpoints.db` (a single sqlite3
  connection with `check_same_thread=False`), `app.db`, the Chroma persist directory, and `storage/media/` chart files. All
  of these assume one writer or one shared filesystem **(observation)**.
- **Large binaries:** the employee DB (~290 MB) and Chroma data travel via Git LFS. A deployment target has to get them
  some other way or pull LFS objects.
- **Secrets:** `GOOGLE_API_KEY`, `E2B_API_KEY` and `JWT_SECRET_KEY` come from `.env` today.
- **Azure access (Sub-Plan 1):** the code-execution datasets are already off local disk. Locally the backend uses the developer's
  `az login`; when hosted in Azure it needs a managed identity with Storage Blob Data Contributor on the storage account.
- **Long requests:** a RAG PDF upload does docling conversion plus per-page embeddings inside one request. The frontend
  allows up to 180 s for it, and 120 s for a chat turn, so any proxy or timeout in front must allow that.
- **Two processes:** the backend and Streamlit are separate processes joined by `API_BASE_URL`. The frontend calls the
  backend server-side via httpx, so browser CORS is not involved **(observation)**.
- **Install weight:** docling makes `pip install` large and slow. `requirements/` is already split into base, backend and dev.
- **Outbound access needed:** Google Gemini API, E2B, DuckDuckGo (fallback only once Foundry is configured), and the Foundry
  project endpoint (`*.services.ai.azure.com`). Foundry's web-search tool is Bing-backed and ignores VPN and private endpoints.
- **Azure access (Sub-Plan 2):** when hosted in Azure, the managed identity also needs the **Foundry User** role on the Foundry
  project. The worst-case chat turn for a web question is about 104 s (router 2.4 s + Foundry limit 40 s + DuckDuckGo fallback
  about 61 s), under the frontend's 120 s limit, so a proxy in front must allow at least that.
- **(open question)** Which hosting target and process model to use, and whether SQLite stays or moves to a server DB.
- **(open question)** Where persistent data (`storage/`, `data/`) lives and how it is backed up.
- **(open question)** Whether a reverse proxy/TLS, health-check wiring (`/health`, `/readiness` exist) and CI are wanted.

## 15. Working conventions
- The work proceeds one numbered plan point at a time.
- Existing planning documents are left intact; new plans go in new `.md` files.
- `AdaptiveRAG_Implementation_Plan.md` deliberately left local/cloud deployment out; deployment is a separate piece of work.
- Development is on Windows (PowerShell, `.venv\Scripts\activate`).
- Deployment work (2026-10-04 onward): the Azure move is incremental. Each sub-plan gets its own `.md` file with a progress checklist
  (`AdaptiveRAG_Azure_SubPlan1_CodeData_Blob.md` is the model). Each change is applied, checked with pytest and manual runs, and then
  ticked off. Azure resources are created in the Portal, and Azure checks use `az` CLI output.
- The git repository for the Azure working copy was added on 2026-10-07 (section 11).

---

## 16. Azure deployment progress (updated 2026-10-07)

The move to Azure is incremental: one sub-plan at a time, each with its own `.md` file and checklist. Whether the whole
project moves to Azure is decided later. Work happens in `D:\CVProjects\AdaptiveRAGAz`.

**Azure resources** (created in the Portal; the subscription already existed): resource group `rg-adaptiverag`,
storage account `stadaptiverag32` (hierarchical namespace off), private container `code-execution`, datasets as blobs under
the virtual folder `code_data/`. For Sub-Plan 2: Foundry resource `fn-adaptiverag`, project `proj-adaptiverag`, agent
`adaptiverag-websearch` (model deployment `gpt-5.4`, built-in Web Search tool, instructions kept in Foundry).

**Still on local disk for now:** `app.db`, `checkpoints.db`, the Chroma index, the employee DB, `data/rag_data/` PDFs and
`storage/media/` chart files.

### Sub-Plan 1: code-execution datasets, local `backend/data/code_data` to Azure Blob (complete, 2026-10-06)

Plan and progress log: `AdaptiveRAG_Azure_SubPlan1_CodeData_Blob.md` (Steps 1-8 all ticked).

| Area | Change |
|---|---|
| `app/config.py`, `.env.example`, `requirements/base.txt` | Added `azure_storage_account_name` (optional), `azure_storage_container`, `azure_storage_code_data_prefix`, `code_data_max_upload_mb` (50) and the packages `azure-storage-blob`, `azure-identity`; removed `code_data_dir`, `resolved_code_data_dir` and its `ensure_storage_dirs()` entry |
| `app/storage/code_data_store.py` (new) | `list_files`, `download`, `upload`, `validate_filename`; lazy cached container client using `DefaultAzureCredential`; errors `InvalidCodeDataFilenameError`, `CodeDataFileTooLargeError`, `CodeDataFileNotFoundError`, `CodeDataStorageError`; bare `.csv`/`.xlsx` names only, size cap on upload and download, silent overwrite, content types set |
| `agent/tools/code_tools.py` | `upload_file(file_name)` downloads from Blob and writes the bytes to the sandbox at `data/<name>` (sandbox path unchanged); new `list_code_data_files(name_filter="")`; failures come back as `"Error : ..."` strings |
| `agent/nodes/code_execution_node.py`, `agent/prompts.py` | Tools are `list_code_data_files`, `upload_file`, `run_python_code`; the `FilesystemFileSearchMiddleware` (`glob_search`/`grep_search`) is gone; prompt rewritten |
| `api/routers/admin.py` | `upload_code_data_file` calls `code_data_store.upload`; 400 / 413 / 502 mapping with a generic 502 message (Azure detail only logged); route, function name and response unchanged |
| Tests | New `tests/unit/test_code_data_store.py` and `tests/unit/test_code_execution_agent.py`; `conftest.py` has a RAG-only `admin_upload_dirs` plus `fake_code_data_blob`; code-data tests in `test_admin_endpoints.py` rewritten; manual scripts `test_code_data_store_manual.py` and `test_code_agent_manual.py` |
| Local folder | Blob copy verified identical to the local files by name, size and SHA-256, then `backend/data/code_data` was deleted |
| `README.md` | Azure prerequisites, setup subsection, admin description, tests note, further-reading entry |
| Frontend | No change (`3_Admin.py` already shows the error detail) |

**Decisions:** auth is `DefaultAzureCredential` only (`az login` locally, managed identity in Azure); a same-name upload
overwrites silently; 50 MB cap; `grep_search` dropped; `get_dataset_info` left as is; `/readiness` unchanged.

**Verification:** the full pytest suite passed after each step; a smoke script against the real container; an end-to-end run
in the UI (list files, analyse `titanic.csv`, upload a new CSV through the Admin page and analyse it). The test blob was
removed afterwards and the container holds only the 4 original files.

**Open items from Sub-Plan 1:** a Blob check in `/readiness` (deferred until hosting on Azure); the unused
`get_dataset_info`; the README architecture diagram has no code fence and no Blob box.

### Sub-Plan 2: web search through the Foundry agent `adaptiverag-websearch` (complete, 2026-10-07)

Plan and progress log: `AdaptiveRAG_Azure_SubPlan2_WebSearch_Foundry.md` (Steps 1-5c ticked; Step 6 is this documentation update).

| Area | Change |
|---|---|
| `app/config.py`, `.env.example`, `requirements/base.txt` | Added `foundry_project_endpoint` (optional), `foundry_web_search_agent_name`, `foundry_request_timeout_seconds` (45), `web_search_fallback_enabled` (true) and `azure-ai-projects>=2.8.0` |
| `agent/tools/foundry_web_search.py` (new) | `search`, `format_answer`, `is_configured`, `FoundryWebSearchError`; lazy cached client; stateless `responses.create` with an `agent_reference` by name; citations parsed from `url_citation` annotations and de-duplicated; numbered **Sources** block; wall-clock deadline through a daemon worker thread (`_run_with_deadline`) |
| `agent/nodes/web_search_node.py` | Foundry first; the original DuckDuckGo ReAct agent moved unchanged into `_duckduckgo_answer` and wrapped by `_safe_duckduckgo_answer`; blank endpoint goes straight to DuckDuckGo; failure falls back or returns "Web search is temporarily unavailable..."; timing printed |
| `agent/tools/web_tools.py` | `DDGSException` is caught and turned into a "no results, do not search again" string (before, it crashed the graph through `ToolNode`) |
| Tests | New `test_foundry_web_search.py`, `test_web_search_node.py`, `test_web_tools.py`; manual scripts `test_foundry_web_search_manual.py` and `test_web_search_agent_manual.py` (per-node timing, Foundry run and fallback run) |
| Foundry (agent instructions) | Instructions edited three times: no closing offers of further help; keep the number of searches down; cite article pages (the last had little visible effect) |
| `conversation.py` (sample script) | Deleted |

**Decisions:** Sources list kept; DuckDuckGo stays as the fallback and its outdated answers are accepted; calls are stateless and by
name only; the agent's tool choice is left to the model (no forced `tool_choice`); Foundry timeout 40 s in `.env`.

**Measured timings:** a typical Foundry answer takes 20-30 s (23.8 s, 29.9 s, 20.5 s and 26.2 s measured); the whole graph took 22.9-28.4 s
(router about 2.4 s). One run took 154.4 s before the deadline existed. The DuckDuckGo route took 43.6-63.7 s.

**Verification:** full pytest suite 144 passed; manual scripts for the Foundry call and for both routes through the graph; an end-to-end UI run
(Foundry answer with Sources, a wrong agent name falling back to DuckDuckGo, a blank endpoint going straight to DuckDuckGo, settings restored).

### Frontend fixes made alongside Sub-Plan 2 (2026-10-07, `frontend/components/`)
- **Conversation titles:** every conversation had been called "New conversation". `sidebar.py` now opens an empty draft; `chat_window.py` creates
  the conversation on the first message with `make_title(prompt)` (whitespace collapsed, 40 characters cut at a word boundary, "..." added),
  reruns so the sidebar lists it, and then sends the message. No backend change.
- **Dollar signs:** `st.markdown` treated text between two `$` as a LaTeX formula, so amounts like "$4 billion ... $14.5 billion" showed as
  italic formulas. `escape_dollars()` in `chat_window.py` escapes `$` outside code spans and code blocks for saved messages, the user's
  message and the live reply.

**Open items from Sub-Plan 2:** none blocking. Possible later changes: put today's date into the fallback prompt, force `tool_choice`, show a note when
the fallback answered, or generate real conversation titles.

### Git repository for the Azure working copy (2026-10-07)
Pushed to `https://github.com/joydeep-ganguly/AdaptiveRAGAz.git`, branch `main`, commit `1c940b6`; details in section 11. Later commits
keep `app.db` and `checkpoints.db` out (they are ignored), and `git status` should be checked for `.env` files before each push.

**Next:** not yet decided. Further sub-plans are not defined yet.
