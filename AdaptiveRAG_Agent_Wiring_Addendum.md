# Addendum — Wiring the `agent/` subfolder's missing dependencies

Supplements `AdaptiveRAG_Implementation_Plan.md`, specifically section 3 (file-by-file
migration map). That table said "logic unchanged, just import paths" for each ported
file — in practice, every file under `backend/app/agent/` was copy-pasted verbatim from
the notebook / `scripts/`, so each one references names (`AgentState`, `my_tools`,
`mysql_tools`, a bare `llm`, `get_latest_user_query`, `prompts`, `utils`) that don't exist
in its new location. This addendum documents the fix.

## Root cause

Splitting one notebook's shared execution namespace into separate files doesn't
automatically carry imports with it — every name a file used to get "for free" from
being in the same notebook/module now needs an explicit `import`.

## Design decisions made while fixing this

1. **New file: `agent/llm.py`.** The notebook/scripts instantiated `ChatGoogleGenerativeAI`
   in four separate places with three different configurations (plain / reasoning /
   sql). `llm.py` centralizes all three plus the embeddings client behind `get_llm()`,
   `get_reasoning_llm()`, `get_sql_llm()`, `get_embeddings()` (each `@lru_cache`d, so
   still a single client instance per process, matching the original behavior). Every
   node/tool file now imports the specific variant it needs from here instead of building
   its own client.

2. **Path-fixing rule applied during this pass:** paths that are *functionally required*
   to find real data — Chroma directory, employees DB, checkpoints DB, the `data/`
   folder, the E2B key — are now wired through `app.config.get_settings()`, since the
   config layer already exists for exactly this and several of these files (`checkpointer.py`,
   `sql_tools.py`) were non-functional without it regardless of path style. Paths that
   are purely diagnostic (`debug_logs/*.md`) were **left as bare relative strings** —
   those still fall under the concurrency-safety fixes already tracked in section 4 of
   the main plan (per-conversation namespacing), not under "restore the missing imports."

3. **Two dead imports removed from `tools/sql_tools.py`**: `from langchain_ollama import
   ChatOllama` and `from langchain_openai import ChatOpenAI` were both unused and neither
   package is in `requirements/base.txt` — left in, they'd raise `ModuleNotFoundError` on
   import, not just an unused-import warning.

4. **One dead client removed from `retrieval/vectorstore.py`**: the original file
   instantiated its own `llm = ChatGoogleGenerativeAI(...)`, but nothing in that file
   ever calls it (it was only used by `extract_filters`/`generate_ranking_keywords`,
   which now live in `retrieval/filters.py`). Removed rather than ported.

5. **`agent/tools/web_tools.py`, `agent/retrieval/ranking.py`, `agent/state.py`,
   `agent/schemas.py`, `agent/prompts.py`** needed no changes — they were already
   self-contained.

## Package scaffolding still required

None of `backend/app/`, `backend/app/agent/`, `backend/app/agent/nodes/`,
`backend/app/agent/tools/`, `backend/app/agent/retrieval/` currently have an
`__init__.py`. Add an empty one to each so `from app.agent.state import AgentState`
resolves reliably (Python's implicit namespace packages can work without them, but
explicit `__init__.py` is the safer default for an application and avoids pytest
import-resolution surprises later).

## Updated per-file dependency table (agent/ only)

| File | Needs from |
|---|---|
| `state.py` | *(nothing — self-contained)* |
| `schemas.py` | *(nothing — self-contained)* |
| `prompts.py` | *(nothing — self-contained)* |
| `llm.py` *(new)* | `app.config` |
| `checkpointer.py` | `app.config` |
| `graph.py` | `app.agent.state`, `app.agent.router`, `app.agent.checkpointer`, `app.agent.nodes.*` |
| `router.py` | `app.agent.state`, `app.agent.nodes.rag_nodes` (for `get_latest_user_query`), `app.agent.llm` |
| `nodes/rag_nodes.py` | `app.agent.tools.doc_tools`, `app.agent.llm` |
| `nodes/sql_agent_node.py` | `app.agent.state`, `app.agent.nodes.rag_nodes`, `app.agent.llm`, `app.agent.tools.sql_tools` |
| `nodes/web_search_node.py` | `app.agent.state`, `app.agent.nodes.rag_nodes`, `app.agent.llm`, `app.agent.tools.web_tools` |
| `nodes/code_execution_node.py` | `app.config`, `app.agent.state`, `app.agent.nodes.rag_nodes`, `app.agent.llm`, `app.agent.tools.code_tools`, `app.agent.prompts` |
| `tools/doc_tools.py` | `app.agent.retrieval.filters`, `app.agent.retrieval.vectorstore`, `app.agent.retrieval.ranking` |
| `tools/sql_tools.py` | `app.agent.llm`, `app.config` |
| `tools/code_tools.py` | `app.config` |
| `tools/web_tools.py` | *(nothing — self-contained)* |
| `retrieval/filters.py` | `app.agent.schemas`, `app.agent.llm` |
| `retrieval/vectorstore.py` | `app.config`, `app.agent.llm` |
| `retrieval/ranking.py` | *(nothing — self-contained)* |

No circular imports result from this: `nodes/rag_nodes.py` is a leaf that several other
node/router files depend on for `get_latest_user_query`, but it never imports back from
them.

## Not fixed in this pass (still tracked in section 4 of the main plan)

- The E2B sandbox singleton (`_sbx` in `tools/code_tools.py`) is still one sandbox per
  process, shared across all users/conversations.
- Debug log files (`debug_logs/*.md`) are still fixed filenames, not namespaced per
  conversation.

Both require threading a `conversation_id`/`thread_id` through the node call chain
(via LangGraph's `config` parameter), which touches `chat_service.py` — out of scope
for an agent-subfolder-only pass.
