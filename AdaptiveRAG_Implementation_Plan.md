# Adaptive RAG — Implementation Plan (Notebook → Deployable Service)

Scope note: this plan **only** covers code structure, migration steps, auth/DB design,
testing, git setup, and running dev servers so peers can use the app. It intentionally
does not cover where/how you host it (local server, cloud, containers, etc.) — that's
a separate decision for later.

---

## 1. Target Architecture

```
┌────────────┐        HTTP (Bearer JWT)        ┌──────────────────┐
│  Streamlit │ ───────────────────────────────► │     FastAPI      │
│  frontend  │ ◄─────────────────────────────── │     backend      │
└────────────┘                                   └────────┬─────────┘
                                                            │
                     ┌──────────────────────────────────────┼──────────────────────────┐
                     │                                       │                          │
             ┌───────▼────────┐                    ┌─────────▼─────────┐      ┌─────────▼─────────┐
             │  app.db         │                    │  LangGraph agent   │      │  checkpoints.db    │
             │ (users,         │                    │  (ported from      │      │ (LangGraph short-  │
             │  conversations, │                    │  NotebookImpl.)    │      │  term memory)       │
             │  messages)      │                    └─────────┬──────────┘      └────────────────────┘
             └────────────────┘                                │
                                       ┌────────────────────────┼─────────────────────────┐
                                ┌──────▼──────┐        ┌─────────▼────────┐      ┌──────────▼─────────┐
                                │  Chroma     │        │ employees_db      │      │  E2B sandbox /      │
                                │  (financial │        │  (.db, SQL agent) │      │  DuckDuckGo (ddgs)  │
                                │  docs)      │        └───────────────────┘      └─────────────────────┘
                                └─────────────┘
```

Key idea: **the frontend never talks to the agent, database, Chroma, or E2B directly.**
It only calls FastAPI endpoints with a Bearer token. FastAPI owns all business logic,
persistence, and orchestration of the LangGraph agent — exactly mirroring the notebook's
graph, just wrapped in a service layer.

---

## 2. Proposed Folder Structure

```
AdaptiveRAG/
├── NotebookImplementation/            # unchanged — keep as the research/reference copy
│
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI() app factory, router registration, startup/shutdown
│   │   ├── config.py                  # pydantic-settings: all env vars, resolved absolute paths
│   │   ├── logging_config.py          # replaces scattered print() calls with real logging
│   │   ├── deps.py                    # shared Depends(): get_db, get_current_user, get_agent_graph
│   │   │
│   │   ├── api/
│   │   │   ├── routers/
│   │   │   │   ├── auth.py            # /auth/register, /auth/token, /auth/me
│   │   │   │   ├── conversations.py   # /conversations CRUD (list, create, delete, get messages)
│   │   │   │   ├── chat.py            # /chat/{conversation_id}/query — the main "ask the agent" endpoint
│   │   │   │   ├── media.py           # authenticated file serving for generated chart PNGs
│   │   │   │   └── health.py          # /health, /readiness (checks DB + vectorstore + API keys reachable)
│   │   │   └── schemas/               # Pydantic request/response models (API contracts)
│   │   │       ├── auth.py
│   │   │       ├── conversation.py
│   │   │       └── chat.py
│   │   │
│   │   ├── auth/
│   │   │   ├── security.py            # password hashing (passlib), JWT encode/decode (python-jose)
│   │   │   └── oauth2.py              # OAuth2PasswordBearer scheme + get_current_user resolver
│   │   │
│   │   ├── db/                        # app-level persistence (users/conversations), separate from the agent's own DBs
│   │   │   ├── session.py             # SQLAlchemy engine + session factory for app.db
│   │   │   ├── models.py              # User, Conversation, Message ORM models
│   │   │   ├── crud.py                # create_user, get_user, create_conversation, append_message, ...
│   │   │   └── init_db.py             # run once to create tables in a fresh app.db
│   │   │
│   │   ├── services/
│   │   │   ├── auth_service.py        # register/login business logic (wraps crud + security)
│   │   │   └── chat_service.py        # orchestrates: load/verify conversation ownership → invoke agent
│   │   │                              #   graph with thread_id=conversation_id → persist messages → return result
│   │   │
│   │   └── agent/                     # <-- the ported "brain", 1:1 with NotebookImplementation/scripts
│   │       ├── state.py               # AgentState TypedDict, RouterQuery / Grade* / SearchQueries schemas
│   │       ├── router.py              # route_question()  (from notebook cell)
│   │       ├── graph.py               # create_adaptive_rag() — builds & compiles the StateGraph
│   │       ├── checkpointer.py        # SqliteSaver setup, absolute path, connection handling
│   │       ├── prompts.py             # all system prompts in one place (was scattered across nodes + prompts.py)
│   │       ├── schemas.py             # ChunkMetadata, DocType, FiscalQuarter, RankingKeywords
│   │       │
│   │       ├── nodes/
│   │       │   ├── rag_nodes.py       # retrieve/grade/generate/transform_query/router-checks (from scripts/rag_nodes.py)
│   │       │   ├── sql_agent_node.py  # employee-DB ReAct agent
│   │       │   ├── web_search_node.py # web-search ReAct agent
│   │       │   └── code_execution_node.py  # data-analysis / sandbox ReAct agent
│   │       │
│   │       ├── tools/
│   │       │   ├── doc_tools.py       # retriever_docs (from my_tools.py)
│   │       │   ├── web_tools.py       # web_search (from my_tools.py)
│   │       │   ├── code_tools.py      # sandbox mgmt, upload_file, run_python_code (from my_tools.py)
│   │       │   └── sql_tools.py       # renamed from mysql_tools.py — it's SQLite, not MySQL
│   │       │
│   │       └── retrieval/
│   │           ├── vectorstore.py     # Chroma + embeddings init (from utils.py)
│   │           ├── filters.py         # extract_filters, generate_ranking_keywords (from utils.py)
│   │           └── ranking.py         # extract_headings_with_context, rank_documents_by_keywords, search_docs
│   │
│   ├── data/                          # copy of NotebookImplementation/data (csv/xlsx for code-exec agent)
│   ├── storage/
│   │   ├── chroma_financial_db/       # copy of the already-built Chroma index
│   │   ├── sqlite/
│   │   │   ├── app.db                 # NEW — users, conversations, messages
│   │   │   ├── checkpoints.db         # LangGraph short-term memory
│   │   │   └── employees_db-full-1.0.6.db
│   │   ├── media/                     # generated chart PNGs, namespaced per conversation_id
│   │   └── debug_logs/                # retrieval/generation debug output, namespaced per conversation_id
│   │
│   ├── tests/
│   │   ├── conftest.py                # fixtures: temp app.db, TestClient, fake/mocked agent graph
│   │   ├── unit/
│   │   │   ├── test_security.py       # password hashing + JWT
│   │   │   ├── test_router.py         # route_question with a mocked LLM
│   │   │   ├── test_sql_tools.py      # validate_sql_query — injection/dangerous-keyword cases
│   │   │   ├── test_ranking.py        # pure functions: BM25 ranking, heading extraction
│   │   │   └── test_crud.py           # db/crud.py against an in-memory sqlite
│   │   └── integration/
│   │       ├── test_auth_endpoints.py
│   │       ├── test_conversations_endpoint.py
│   │       └── test_chat_endpoint.py  # agent graph mocked out — never calls live Gemini/E2B in tests
│   │
│   ├── requirements/
│   │   ├── base.txt                   # shared agent/LLM deps
│   │   ├── backend.txt                # base.txt + FastAPI/auth/db deps
│   │   └── dev.txt                    # backend.txt + pytest tooling
│   └── .env.example
│
├── frontend/
│   ├── streamlit_app.py               # entrypoint, session_state bootstrap, page config
│   ├── pages/
│   │   ├── 1_Login.py                 # login/register form, stores JWT in st.session_state
│   │   └── 2_Chat.py                  # conversation sidebar + chat window
│   ├── components/
│   │   ├── chat_window.py             # renders messages, markdown, citations, chart images
│   │   ├── sidebar.py                 # conversation list / new chat / logout
│   │   └── auth_forms.py
│   ├── api_client.py                  # thin httpx wrapper: attaches Bearer token, calls backend endpoints
│   ├── config.py                      # API_BASE_URL from env
│   ├── requirements.txt               # streamlit, httpx, pandas — deliberately does NOT need langchain/E2B/etc.
│   └── .env.example
│
├── scripts/
│   ├── seed_admin_user.py             # creates the first login so peers can get in
│   ├── run_backend_dev.sh / .ps1
│   └── run_frontend_dev.sh / .ps1
│
├── .gitignore
├── .gitattributes                     # Git LFS tracking rules for the large .db files
└── README.md                          # setup / run / test instructions for the team
```

---

## 3. File-by-file migration map

| Old location | New location | Notes |
|---|---|---|
| Notebook cell: `RouterQuery` | `agent/state.py` | |
| Notebook cell: `AgentState` | `agent/state.py` | Add the missing `rewritting_count: int` field explicitly — nodes already read/write it via `state.get()`, but it's not declared in the `TypedDict` |
| Notebook cell: `route_question` | `agent/router.py` | |
| Notebook cell: `sql_agent_node` | `agent/nodes/sql_agent_node.py` | |
| Notebook cell: `web_search_node` | `agent/nodes/web_search_node.py` | |
| Notebook cell: `code_execution_node` | `agent/nodes/code_execution_node.py` | Needs the sandbox-scoping fix below |
| Notebook cells: checkpointer + `create_adaptive_rag()` | `agent/checkpointer.py` + `agent/graph.py` | Path must become absolute, not `"db/checkpoints.db"` |
| `scripts/rag_nodes.py` | `agent/nodes/rag_nodes.py` | Logic unchanged, just import paths |
| `scripts/my_tools.py` | split into `agent/tools/doc_tools.py`, `web_tools.py`, `code_tools.py` | |
| `scripts/mysql_tools.py` | `agent/tools/sql_tools.py` | Renamed — it's SQLite, the name is a leftover from an earlier version |
| `scripts/utils.py` | split into `agent/retrieval/vectorstore.py`, `filters.py`, `ranking.py` | |
| `scripts/prompts.py` | `agent/prompts.py` | Consolidate the other inline system prompts (currently duplicated inline in `rag_nodes.py`, `sql_agent_node`, `web_search_node`) here too, so there's one place to tune prompts |
| `scripts/schemas.py` | `agent/schemas.py` | |
| `scripts/test.py` | delete | It's a scratch duplicate of `rag_nodes.py`, not a real test |
| `data/` | `backend/data/` | |
| `chroma_financial_db/` | `backend/storage/chroma_financial_db/` | Copy the already-built index — no need to re-embed documents |
| `db/checkpoints.db` | `backend/storage/sqlite/checkpoints.db` | |
| `db/employees_db-full-1.0.6.db` | `backend/storage/sqlite/employees_db-full-1.0.6.db` | |
| `debug_logs/`, `images/` | `backend/storage/debug_logs/`, `backend/storage/media/` | Must become per-conversation, see below |

---

## 4. Bugs/assumptions in the notebook code that must be fixed for multi-user use

These aren't structural nitpicks — without fixing them, concurrent users **will** corrupt
each other's sessions:

1. **E2B sandbox is a single module-level global** (`_sbx` in `my_tools.py`). Every user
   currently shares one sandbox process. Fix: key sandboxes by `conversation_id` (e.g. a
   `dict[str, Sandbox]` with an idle-timeout eviction), so User A's uploaded files and code
   state can never leak into User B's session.

2. **Debug log files use fixed names** (`debug_logs/self_rag.md`, `retrieved_reranked_docs.md`,
   `self_rag_answer.md`). Two simultaneous requests will overwrite each other's files. Fix:
   namespace by `conversation_id`/request id, and gate writing behind a `DEBUG_MODE` setting
   so production doesn't write these at all.

3. **Hard-coded relative paths** (`"./data"`, `"./chroma_financial_db"`, `"db/checkpoints.db"`)
   depend on the process's current working directory. Fix: resolve everything to absolute
   paths from `config.py` (`BASE_DIR / "storage" / ...`) so it works regardless of where
   `uvicorn`/`streamlit` is launched from.

4. **All LLM/tool calls are synchronous** (`llm.invoke(...)`, `agent.invoke(...)`, sandbox
   calls). If the FastAPI chat endpoint is declared `async def` and calls these directly, it
   blocks the whole event loop for every other user during that request. Fix: either declare
   the endpoint as a plain `def` (FastAPI runs sync endpoints in a thread pool automatically)
   or explicitly wrap the graph invocation in `starlette.concurrency.run_in_threadpool`.

5. **`generate_node` indexes `response.content[1]['text']`** — this assumes Gemini always
   returns a "thinking" block at index 0 and text at index 1. Fix: parse the content blocks
   defensively (find the block with `type == "text"`) instead of trusting a fixed index.

6. **`load_dotenv()` is called independently in five different files.** Fix: call it once,
   centrally, in `config.py`, and have every other module read settings from there instead
   of re-touching the environment.

7. **Generated chart images have no way to reach the Streamlit UI** — `run_python_code`
   currently just writes a PNG to local disk and returns a path string. The new `media.py`
   router needs to serve these files (auth-checked, scoped by conversation) so the frontend
   can actually display them, e.g. as `st.image(url, ...)`.

---

## 5. Auth & Data model

**Auth flow chosen:** standard FastAPI **OAuth2 Password Bearer + JWT**, since you want
users stored in SQLite (a third-party OAuth provider like Google/GitHub login wouldn't
need a password table at all). If you actually meant "let users sign in with their Google
account," flag that — it's a different, smaller flow (via `authlib`) that skips password
storage entirely.

**Tables (`app.db`):**

- `users` — `id, username, email, hashed_password, is_active, created_at`
- `conversations` — `id (= LangGraph thread_id, uuid), user_id (FK), title, created_at, updated_at`
- `messages` — `id, conversation_id (FK), role (user/assistant), content, created_at`

`conversations.id` is deliberately reused as the LangGraph `thread_id`, so the same
identifier ties together: app-level ownership, the checkpoint DB's state, and any
generated media files. `messages` duplicates what LangGraph's checkpointer already stores,
but makes listing conversation previews fast and simple without reconstructing graph state.

**Endpoints:**

- `POST /auth/register` — create user
- `POST /auth/token` — OAuth2 password grant → `{access_token, token_type}`
- `GET /auth/me` — current user info
- `POST /conversations` — create a new conversation, returns its id
- `GET /conversations` — list current user's conversations
- `GET /conversations/{id}/messages` — history
- `DELETE /conversations/{id}`
- `POST /chat/{conversation_id}/query` — body `{message}`; verifies the conversation belongs
  to the caller, invokes the agent graph with `config={"configurable": {"thread_id": conversation_id}}`,
  persists both messages, returns the assistant's reply (+ any media URLs)
- `GET /media/{conversation_id}/{filename}` — auth-checked static file serving for chart PNGs
- `GET /health`

Every conversation/chat/media endpoint must check `conversation.user_id == current_user.id`
before touching it — this is the authorization check, separate from authentication.

---

## 6. Step-by-step build order

1. **Scaffold the repo** — create the folder tree above, `requirements/*.txt`, `.env.example`, `.gitignore`. Nothing functional yet.
2. **Config layer** — `config.py` with `pydantic-settings`, resolving all paths/keys. Confirm it loads `GOOGLE_API_KEY`, `E2B_API_KEY`, `JWT_SECRET_KEY`.
3. **Copy static assets** — `data/`, `chroma_financial_db/`, `employees_db-full-1.0.6.db` into `backend/storage/` and `backend/data/` unchanged (no re-embedding needed).
4. **Port the agent package** (`agent/`) file-by-file per the migration map, applying fixes #1–#6 above as you go. Sanity-check it by writing a throwaway script that calls `create_adaptive_rag()` and runs a couple of the same queries as the notebook's last cells — confirms parity before building anything on top.
5. **`db/` layer** — SQLAlchemy models, `init_db.py`, `crud.py`. Run `init_db.py` to produce a fresh `app.db`.
6. **`auth/` layer** — password hashing, JWT issue/verify, `get_current_user` dependency.
7. **`services/`** — `auth_service.py`, then `chat_service.py` (this is where the ownership check + agent invocation + message persistence live).
8. **`api/` routers** — wire everything into FastAPI, register routers in `main.py`.
9. **Tests** — write unit tests as each piece lands (don't leave them all to the end); integration tests once the routers exist, with the agent graph mocked out.
10. **Frontend** — `api_client.py` first (test it against the running backend with `curl`/Postman-equivalent), then the login page, then the chat page, then chart-image rendering via `/media`.
11. **`scripts/seed_admin_user.py`** — so the team has a login on day one.
12. **README + dev run scripts** — so peers can clone and go.
13. **Git init + first push.**

---

## 7. Testing strategy

- **Unit** — pure functions and isolated logic, no network calls: `validate_sql_query` (injection cases), `rank_documents_by_keywords`/`extract_headings_with_context`, JWT encode/verify, password hashing, `crud.py` against an in-memory/temp SQLite.
- **Integration** — FastAPI `TestClient` against real routers: register → token → `/auth/me`; conversation CRUD; the `/chat` endpoint with `graph.invoke` monkeypatched to return a canned message (never call live Gemini/E2B/DuckDuckGo in the test suite — costs money and is flaky).
- **Optional live smoke test** — one test marked e.g. `@pytest.mark.live`, skipped by default, that really calls the compiled graph end-to-end for a manual pre-release check.
- Run with: `pytest backend/tests -v`

---

## 8. Dependencies

**`backend/requirements/base.txt`** (shared agent/LLM stack — matches what the notebook actually imports):
```
python-dotenv
pydantic>=2
langchain
langchain-core
langchain-google-genai
langchain-community
langchain-chroma
langgraph
langgraph-checkpoint-sqlite
e2b-code-interpreter
ddgs
rank_bm25
pandas
openpyxl
```

**`backend/requirements/backend.txt`**:
```
-r base.txt
fastapi
uvicorn[standard]
sqlalchemy
python-jose[cryptography]
passlib[bcrypt]
bcrypt==4.0.1
python-multipart
pydantic-settings
email-validator
```

**`backend/requirements/dev.txt`**:
```
-r backend.txt
pytest
pytest-mock
pytest-cov
```

**`frontend/requirements.txt`** (deliberately minimal — the frontend never imports the agent stack):
```
streamlit
httpx
pandas
python-dotenv
```

---

## 9. `.env.example`

**`backend/.env.example`**:
```
# LLM / Embeddings
GOOGLE_API_KEY=

# Code execution sandbox
E2B_API_KEY=

# Auth
JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Storage (defaults are usually fine — resolved to absolute paths in config.py)
APP_DB_PATH=storage/sqlite/app.db
CHECKPOINT_DB_PATH=storage/sqlite/checkpoints.db
EMPLOYEES_DB_PATH=storage/sqlite/employees_db-full-1.0.6.db
CHROMA_DIR=storage/chroma_financial_db
DATA_DIR=data
DEBUG_MODE=false
```

**`frontend/.env.example`**:
```
API_BASE_URL=http://localhost:8000
```

---

## 10. Git repository setup

1. `git init` at the repo root (decide whether `NotebookImplementation/` stays in the same
   repo for provenance, or lives separately — either works, same-repo is simpler for peers).
2. Write `.gitignore` **before** the first `git add .`:
   ```
   __pycache__/
   *.pyc
   .venv/
   venv/
   .env
   *.db-shm
   *.db-wal
   backend/storage/debug_logs/
   backend/storage/media/
   ```
3. **Large binary files** — `employees_db-full-1.0.6.db` is ~290 MB and `chroma.sqlite3` is
   ~44 MB. GitHub hard-blocks files over 100 MB, so the employees DB can't be committed as a
   plain blob. Two options:
   - **Git LFS** (recommended if these files need to travel with the repo): `git lfs install`,
     `git lfs track "*.db" "*.sqlite3"`, commit the resulting `.gitattributes` before adding
     the DB files.
   - **Keep them out of git entirely** and note in the README that teammates copy
     `storage/chroma_financial_db/` and `storage/sqlite/employees_db-full-1.0.6.db` directly
     from wherever they currently sit (no need to regenerate — the Chroma index and employee
     DB are already built).
4. Commit in logical layers (scaffolding → agent port → db/auth → api → frontend → tests) or
   as one clean initial commit — either is fine for a fresh app; just make sure `.gitignore`
   is in place first so secrets and large binaries never enter history.
5. `git remote add origin <your team's git host URL>` then `git push -u origin main`.

---

## 11. Running in a dev environment (for peers)

Once cloned:

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate      # or .venv\Scripts\activate on Windows
pip install -r requirements/dev.txt
cp .env.example .env        # fill in GOOGLE_API_KEY, E2B_API_KEY, JWT_SECRET_KEY
python -m app.db.init_db    # creates app.db with the users/conversations/messages tables
python ../scripts/seed_admin_user.py   # creates a first login for the team
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
pip install -r requirements.txt
cp .env.example .env        # API_BASE_URL=http://localhost:8000
streamlit run streamlit_app.py
```

Run the test suite to validate a new setup: `pytest backend/tests -v`

If teammates need to hit *your* running dev instance from their own machines on the same
network (rather than each running their own copy), start uvicorn/Streamlit with
`--host 0.0.0.0` and share your machine's LAN IP — that's the extent of the networking
detail relevant to "dev access," anything beyond that is deployment territory.
