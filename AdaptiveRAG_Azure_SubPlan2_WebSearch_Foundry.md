# AdaptiveRAG — Azure Sub-Plan 2: web search through a Microsoft Foundry agent

Written 2026-10-06, updated 2026-10-07. Status: **complete (Steps 1-6 done)** (see section 8).
Part of the incremental Azure move (see `thread_summary.md`, section 16). Sub-Plan 1 (code data to Blob) is complete.

## 0. Azure resources and decisions already made

| Item | Value |
|---|---|
| Resource group | `rg-adaptiverag` (same as Sub-Plan 1) |
| Foundry resource | `fn-adaptiverag` |
| Foundry project | `proj-adaptiverag` |
| Project endpoint | `https://fn-adaptiverag.services.ai.azure.com/api/projects/proj-adaptiverag` |
| Agent | `adaptiverag-websearch`, built-in Web Search tool enabled (search type "Search the web with Bing Search": general web, no setup, no Bing Custom Search connection), instructions started identical to the system prompt in `web_search_node.py`; the developer has since added lines to Foundry (see section 6) |
| Model deployment | `gpt-5.4` |
| Agent reference | By **name only**, no version. A name-only reference uses the latest version: it was version 2 at the first test and became version 3 after the instruction "Do not end with offers of further help" to the instructions |
| Auth | `DefaultAzureCredential` (`az login` locally, managed identity in Azure) |

Decisions made:
- Answers include a **Sources** list built from the citations.
- **DuckDuckGo stays as a fallback.**
- The call is **stateless**: no Foundry conversation is created or used.
- `conversation.py` (root-level sample script) is deleted only after this plan is implemented.

## 1. Goal

The `web_search` route of the LangGraph agent asks the Foundry agent `adaptiverag-websearch` (which owns its own
web-search tool) instead of running a local LangChain agent around DuckDuckGo. If Foundry is not configured or fails,
the existing DuckDuckGo agent answers instead. The router, graph, chat service and frontend do not change.

## 2. What the Foundry call returns (verified 2026-10-06 with the stateless call)

- The call works **without a conversation**:
  `openai.responses.create(extra_body={"agent_reference": {"name": ..., "type": "agent_reference"}}, input=...)`.
- `response.output` is a list holding reasoning items, several `web_search_call` items (this sample made 4 calls with
  14 search queries in total) and a final `message` item. The message's `output_text` part carries the answer text plus an
  `annotations` list of `url_citation` entries (`title`, `url`, `start_index`, `end_index`).
- The answer text **already contains inline markdown links** like `([msn.com](https://...))`. The sample had 6 citation
  annotations but only 5 distinct URLs, so duplicates must be removed.
- `web_search_call` items have no source list of their own (`sources=None`), so sources come only from the annotations.
- The first test answer ended with an **offer of follow-up options** ("If you want, I can also give you..."). Because each web
  search call is stateless, that offer could not be honoured on the next turn. The instruction "Do not end with offers of
  further help" to the agent's instructions (agent version 3); the portal answer afterwards no longer contained the offer.
- Because one question can trigger many searches, latency can be noticeable and varies a lot. Measurements (wall clock):

  | Date | What | Time |
  |---|---|---|
  | 2026-10-06 | Stateless sample call, `time.perf_counter()` | 23.8 s |
  | 2026-10-06 | Step 4 manual run, whole graph, Foundry route (outlier) | 154.4 s |
  | 2026-10-06 | Same run, whole graph, DuckDuckGo fallback route | 63.7 s |
  | 2026-10-07 | `foundry_web_search.search()` alone (5 sources, 2148 characters) | 29.9 s |
  | 2026-10-07 | After the deadline edits: Foundry node 20.5 s, whole graph 22.9 s (router about 2.4 s) | 22.9 s |
  | 2026-10-07 | After Step 4b, timeout 40 s: Foundry node 26.2 s (2 sources), whole graph 28.4 s | 28.4 s |
  | 2026-10-07 | Same run, DuckDuckGo fallback route, whole graph | 43.6 s |

  Typical Foundry time is therefore 20-30 s; the 154.4 s run was an outlier, which is why a hard deadline is needed.

## 3. Where web search is used today

| # | File | What it does now | Change |
|---|---|---|---|
| 1 | `backend/app/agent/nodes/web_search_node.py` | LangChain ReAct agent with the DuckDuckGo tool; returns the whole message list | Foundry first, DuckDuckGo agent as fallback (moved into a helper, unchanged) |
| 2 | `backend/app/agent/tools/web_tools.py` (`web_search`, `ddgs`) | DuckDuckGo search tool | None; kept for the fallback |
| 3 | `backend/app/config.py`, `backend/.env.example`, `backend/requirements/base.txt` | No Foundry settings or package | Add settings and `azure-ai-projects` (`openai` comes with it) |
| 4 | `backend/tests/` | Nothing covers the web node | New unit tests |
| 5 | `README.md`, `thread_summary.md`, this file | Describe DuckDuckGo web search | Update |
| 6 | `conversation.py` (project root) | Sample script that calls the agent | Deleted at the end |

No change needed:
- `graph.py`, `router.py`, `state.py`: the `web_search` route and node name stay as they are.
- `chat_service.py`: it reads the last message's text, so a single `AIMessage` works.
- `frontend/components/chat_window.py`: it renders answers with `st.markdown`, so links and the Sources list display correctly. The Sources list is part of the saved answer text, so it also shows when a conversation is reopened.

## 4. Design

### 4.1 New module `backend/app/agent/tools/foundry_web_search.py`
- `search(query) -> WebSearchResult(text, sources)`, where `WebSearchSource(title, url)`.
- `format_answer(result) -> str`: the answer text, followed by a block `**Sources**` with a numbered list of markdown
  links (`1. [Title](url)`). Sources are de-duplicated by URL in order of first appearance. Titles lose newlines and
  square brackets; if a title is missing the host name is used. No sources: the text alone.
- Client: built **lazily and cached** (never at import, because `tests/conftest.py` imports `app.main`):
  `AIProjectClient(endpoint=..., credential=DefaultAzureCredential()).get_openai_client()`.
- Call: the client with a per-request timeout and **no automatic retries**
  (`with_options(timeout=..., max_retries=0)`, to be confirmed against the installed SDK), then `responses.create` with the
  `agent_reference` shown in section 2 and no `conversation`.
- Parsing: walk `response.output`; for each `message` item, collect its `output_text` parts and their `url_citation`
  annotations, reading attributes defensively. An empty answer is an error.
- **Wall-clock deadline (added 2026-10-07).** The OpenAI client's `timeout` applies per network phase, so it did not cap the whole
  call. `search()` therefore runs the request in a daemon worker thread (`_run_with_deadline(func, seconds)`: thread plus
  `join(timeout)`); when the deadline passes the caller gets `FoundryWebSearchError("... timed out after N s")` and the abandoned
  request is left to finish or fail on its own. The per-request `timeout` and `max_retries=0` stay as a second guard. Any
  exception raised inside the worker is re-raised to the caller and mapped as below.
- One error type, `FoundryWebSearchError`, with a short message. Any exception raised by the Foundry/OpenAI/Azure call is
  converted at this single boundary (detail and traceback go to the log) so the fallback always gets a chance.

### 4.2 Settings (all optional at startup; none needed by tests)
| `.env` setting | Default | Meaning |
|---|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | *(blank)* | Project endpoint; blank means "use DuckDuckGo only" |
| `FOUNDRY_WEB_SEARCH_AGENT_NAME` | `adaptiverag-websearch` | Agent referenced by name |
| `FOUNDRY_REQUEST_TIMEOUT_SECONDS` | `45` | **Wall-clock deadline** for the whole Foundry call, also used as the client's per-request timeout (typical call 20-30 s). The code default and `.env.example` stay 45; the developer's `.env` sets 40 (open decision 6) |
| `WEB_SEARCH_FALLBACK_ENABLED` | `true` | Fall back to DuckDuckGo when Foundry fails |

`FOUNDRY_PROJECT_ENDPOINT` stays blank in `.env.example`, as with the storage account name.

### 4.3 Node flow (`web_search_node`)
1. Take the latest user query, as today.
2. Endpoint blank: go straight to the DuckDuckGo helper.
3. Otherwise ask Foundry; on success return `{'messages': [AIMessage(format_answer(result))]}`.
4. On `FoundryWebSearchError`: log it, print a `[WEB]` line like the other nodes, and if the fallback is enabled use the
   DuckDuckGo helper. If it is disabled, return an `AIMessage` saying "Web search is temporarily unavailable. Please try
   again later."
5. The DuckDuckGo helper is the current code moved unchanged (it keeps returning the full message list).
6. **Hardening (Step 4b, found 2026-10-07).** Every node call to the helper goes through `_safe_duckduckgo_answer(query)`: any
   exception (for example `ddgs` raising `DDGSException: "No results found."`, which `ToolNode` re-raised and which crashed the
   graph, and would have given the chat endpoint a 500) is logged and answered with the same unavailable `AIMessage`. In
   `web_tools.web_search` a `DDGSException` is caught and turned into a "no results; do not search again" string, so the
   fallback agent can still answer from its own knowledge instead of looping on the tool.
- The Foundry path returns only the answer, so it no longer re-adds the user's question to the saved thread history (the
  current node does).
- Time budget: the frontend allows about 120 s per chat turn. Worst case today: router about 2.4 s + Foundry deadline 45 s +
  DuckDuckGo fallback about 61 s = about 109 s, which fits but with little margin; with the developer's 40 s deadline it is about 104 s
  (the latest fallback run took 43.6 s in total).

### 4.4 Tests (no Azure or network)
- `tests/unit/test_foundry_web_search.py`: parsing of the sample response shape (answer text, annotation extraction,
  de-duplication, order, missing title, no annotations), `format_answer`, error mapping (timeout, auth failure, API error,
  empty answer), missing endpoint, lazily built and cached client with the right endpoint and credential.
- `tests/unit/test_web_search_node.py`: Foundry success returns one `AIMessage` with the Sources block; Foundry failure with
  fallback goes to the DuckDuckGo helper; failure with fallback disabled returns the unavailable message; blank endpoint
  never calls Foundry.
- Added 2026-10-07: three deadline tests in `test_foundry_web_search.py` (wall-clock deadline enforced, `_run_with_deadline`
  returns the value, re-raises the worker's exception); Step 4b adds `tests/unit/test_web_tools.py` (`DDGSException`, empty
  results, normal formatting) and two node tests (DuckDuckGo helper raising, with Foundry not configured and with Foundry
  failing).
- `backend/test_foundry_web_search_manual.py` (same convention as the other `*_manual.py` files): a real call that prints
  the answer, the extracted sources and the elapsed time, then the same question through the graph.

## 5. Steps (each leaves the app working and tests passing)

1. **Verify.**
   - `pip show azure-ai-projects` in the backend venv: **done**, version 2.8.0 (it requires `openai`, `azure-identity` and
     `azure-storage-blob`, all already installed).
   - Time the stateless sample call: **done**, 23.8 s, so the 45 s timeout default stays.
   - Check the Web Search tool's terms for displaying citations: **done** (see section 6). The app shows citations inline and in
     a Sources list.
   - Baseline `pytest -q`: **done**, 102 passed, 1 warning (a pre-existing `langchain-community` deprecation notice in
     `sql_tools.py`, unrelated to this plan).
2. **Dependencies and config (additive):** `azure-ai-projects` in `base.txt` (`azure-ai-projects>=2.8.0`, the version tested in Step 1), the four settings in
   `config.py` and `.env.example`, real values in the developer's `.env`.
3. **Foundry module, its unit tests and the manual smoke script.**
4. **Node switch with fallback, and the node unit tests.**
4b. **DuckDuckGo fallback hardening** (section 4.3 item 6): `web_tools.py`, `_safe_duckduckgo_answer` in the node, new tests. **Done.**
5. **End-to-end** (latency issue settled, section 7 item 5; Step 4b done):
   - Ask a clearly web question in the UI (for example the day's tech news); check the answer and the Sources list.
   - Test the fallback by temporarily blanking `FOUNDRY_PROJECT_ENDPOINT` or using a wrong agent name; the question must
     still be answered via DuckDuckGo; restore the settings afterwards.
   - Full `pytest -q`.
5b. **Conversation titles (frontend, side task found during Step 5, outside the web-search scope).** Every conversation was listed as
   "New conversation" because the sidebar button created it with that fixed title and the backend has no rename route. Change, frontend only:
   the button no longer creates a conversation; it opens a draft (`st.session_state.draft_conversation`). When the first message is sent,
   `chat_window.py` creates the conversation with `title=make_title(prompt)`, stores the prompt in `st.session_state.pending_prompt` and reruns, so the
   sidebar already lists the new conversation and the message is then sent as usual. `make_title` collapses whitespace and keeps the first 40
   characters, cutting at a word boundary and adding "…". Files: `frontend/components/sidebar.py`, `frontend/components/chat_window.py`. Side effect:
   clicking "New conversation" no longer leaves an empty row in the list. Existing conversations keep their old titles (delete them to test).
6. **Docs and cleanup:** `README.md` (setup, settings, the Azure sign-in and role), `thread_summary.md`, this checklist; `conversation.py` is
   deleted.

## 6. Behaviour changes and things to watch

- Web answers now come from `gpt-5.4` in Foundry, not Gemini, so tone and length can differ. The agent's instructions live
  in Foundry; the copy in `web_search_node.py` remains only for the fallback, so the two can drift apart.
- Referencing the agent by name means the **latest version** is used; edits in Foundry take effect immediately.
- Agent instructions in Foundry changed after the plan was first written: (1) "Do not end with offers of further help" (version 3);
  (2) on 2026-10-07, lines to keep the number of searches per question down and to cite article pages rather than site landing
  pages. The first has the intended effect; the second did not visibly change the citations in one run (Sources still mostly
  landing pages such as `techcrunch.com/latest/`), which looks like a limit of the Bing results, so it is accepted for now.
- Follow-up offers: the agent's answers used to end with offers that this stateless setup cannot honour. Fixed on the
  Foundry side ("Do not end with offers of further help" added to the instructions, agent version 3). Optionally add the same line
  to the DuckDuckGo fallback prompt in `web_search_node.py` for consistency.
- The Web Search tool costs money beyond normal model usage. It uses Grounding with Bing, so queries leave the Azure compliance and
  geographic boundary, the Microsoft Data Protection Addendum does not cover them, and the tool ignores VPNs and private endpoints
  (Foundry docs). An administrator can disable web search for a whole subscription. The Foundry pages state no specific
  citation-display rule; the binding terms are the Grounding with Bing terms of use, and the app shows citations inline and in a
  Sources list anyway. Web results are untrusted input; here they only reach the user as markdown text.
- The router sends questions that mention Amazon, Apple or Google to the RAG path, so web tests need a clearly web question.
- The fallback adds latency when Foundry fails (up to the Foundry timeout plus the DuckDuckGo agent's time).
- Quota or rate limits on the model deployment surface as errors, and the fallback then answers.
- When the backend is hosted in Azure, its managed identity needs the **Foundry User** role on the Foundry project (formerly
  named Azure AI User; the role IDs are unchanged). Locally a developer's own login is enough.
- Sources in the list come from the citations Foundry returns; their quality depends on the search results (the sample
  cited a Verge archive page).

## 7. Decisions still open (defaults in brackets)

1. Resolved 2026-10-06: Foundry timeout 45 s (the timed sample took 23.8 s); revisit after Step 5 if real questions run close to it.
2. Whether to show the user a short note when the DuckDuckGo fallback answered (no).
3. Resolved 2026-10-06: the developer added "Do not end with offers of further help" to the agent's instructions (version 3).
4. Whether the call should force the tool (`tool_choice="required"`, as the Foundry samples do) so every answer is grounded and
   cited (no, the model decides; revisit after Step 5 if answers come back without sources).
5. **Resolved 2026-10-07** (found in the Step 4 manual run): the whole graph took 154.4 s on the Foundry route and 63.7 s on the
   DuckDuckGo route, against the frontend's 120 s limit. The 45 s client timeout is per network phase, not a total deadline.
   Done: (a) Foundry alone timed at 29.9 s; (b) wall-clock deadline in `foundry_web_search.search()` (worker thread plus
   `join(timeout)`) with timing in the node and the manual script; (c) agent instructions in Foundry to limit searches. Re-run after
   the edits: Foundry node 20.5 s, whole graph 22.9 s, so 154.4 s was an outlier. Keep watching the variance during Step 5.
6. **Resolved 2026-10-07:** the developer set `FOUNDRY_REQUEST_TIMEOUT_SECONDS=40` in `.env` (worst case about 104 s instead of 109 s against the 120 s frontend limit). The default in `config.py` and `.env.example` stays 45; revisit after Step 5.
7. **Decided 2026-10-07: accept.** (Seen in the Step 4b manual run) the DuckDuckGo fallback can return **stale news presented as today's** (it answered with
   May 2024 items when asked for today's headlines). This is the original behaviour of the fallback agent, which has no date and whose
   search tool has no recency filter. Options: tell the fallback prompt today's date; or accept it, since the fallback only runs when Foundry fails
   The developer accepts the outdated fallback answers for now.
8. **Resolved 2026-10-07 (Step 5c):** (seen in the UI) a dollar amount in an answer can be rendered as maths. In the screenshot "$4 billion ... $14.5 billion" showed as
   italic formula text, because Streamlit's `st.markdown` treats text between two `$` signs as LaTeX. Not specific to Foundry. Fix when wanted: escape `$` as `\$`
   where answers are rendered in `chat_window.py` (frontend only, applies to old and new answers). Done as `escape_dollars()`: it escapes every `$`
   outside code spans and fenced code blocks (code is left alone because a backslash would show literally there), and is used for saved messages, the
   user's own message and the live reply.

## 8. Progress log

- [x] Step 1 — verify (done 2026-10-06: stateless call and response structure verified, `openai` confirmed installed
  in the backend venv as a dependency of `azure-ai-projects` 2.8.0; baseline pytest 102 passed; agent instructions updated
  (version 3); citation terms reviewed; call timed at 23.8 s; the agent's Web Search tool is the general Bing type)
- [x] Step 2 — dependencies and config (2026-10-06: `azure-ai-projects>=2.8.0` added to `base.txt`; `foundry_project_endpoint` (optional), `foundry_web_search_agent_name`, `foundry_request_timeout_seconds` (45) and `web_search_fallback_enabled` (true) added to `config.py`; matching block added to `.env.example` with the endpoint blank; `.env` updated; contents reviewed; settings print correctly and pytest passes unchanged. Note: a blank `FOUNDRY_PROJECT_ENDPOINT=` is read as an empty string, so code must treat empty or whitespace-only as "not configured")
- [x] Step 3 — Foundry module + tests + smoke script (2026-10-06: created `app/agent/tools/foundry_web_search.py` (`search`, `format_answer`, `is_configured`, `FoundryWebSearchError`, lazy cached client, defensive parsing, `with_options(timeout, max_retries=0)`), `tests/unit/test_foundry_web_search.py` and `test_foundry_web_search_manual.py`; contents reviewed against the proposal; reported: 26 new tests passed, full suite 128 passed, live smoke script printed "Foundry web search OK.")
- [x] Step 4 — node switch with fallback + tests (2026-10-06: `web_search_node.py` rewritten (Foundry first; DuckDuckGo helper `_duckduckgo_answer` and `FALLBACK_SYSTEM_PROMPT` unchanged; blank endpoint goes straight to DuckDuckGo; failure falls back or returns the unavailable message), `tests/unit/test_web_search_node.py` and `test_web_search_agent_manual.py` created; contents reviewed against the proposal; reported: 8 new tests passed, full suite 136 passed. Manual run: the router chose `web_search`; the Foundry route answered with a numbered Sources list (4 sources, citing mostly site landing pages); the fallback route answered via DuckDuckGo but returned stale news presented as today's. Latency issue found here was then handled in the follow-up below. The raw list-of-blocks printed for the fallback answer is only how the script prints Gemini content; `chat_service._extract_text` handles it in the app)
- [x] Step 4a — latency fix (2026-10-07: wall-clock deadline in `foundry_web_search.py` (`_run_with_deadline`, `_DeadlineExceeded`), timing in `web_search_node.py` and per-node timing in `test_web_search_agent_manual.py`, agent instructions updated in Foundry; contents reviewed; reported: `test_foundry_web_search.py` 29 passed, full suite 139 passed. Manual run: Foundry node 20.5 s, whole graph 22.9 s (router about 2.4 s); open decision 5 resolved. The fallback half of the manual run crashed with `DDGSException: No results found.`, handled in Step 4b)
- [x] Step 4b — DuckDuckGo fallback hardening (2026-10-07: `DDGSException` caught in `web_tools.web_search` with a "do not search again" message; `_safe_duckduckgo_answer` wrapper in `web_search_node.py` used by both fallback branches; new `tests/unit/test_web_tools.py` (3 tests) and 2 node tests; contents reviewed against the proposal; reported: 13 passed for the two files, full suite 144 passed. `FOUNDRY_REQUEST_TIMEOUT_SECONDS=40` set in the developer's `.env`. Manual run: Foundry route 28.4 s total (node 26.2 s, 2 sources), fallback route 43.6 s and no crash. Noted: the fallback answer was stale 2024 news presented as today's, see open decision 7)
- [x] Step 5 — end-to-end (2026-10-07: full UI test run: Foundry route with Sources list, fallback with a wrong agent name, fallback with a blank endpoint, settings restored; all behaved as expected; `pytest -q` 144 passed)
- [x] Step 5b — conversation titles (2026-10-07: `sidebar.py` opens a draft conversation instead of creating one; `chat_window.py` adds `make_title`, creates the conversation with that title on the first message, then reruns and sends the message via `pending_prompt`; frontend only, no backend change; both files syntax-checked and `make_title` tried on sample inputs here; to be confirmed in the UI after deleting the old conversations)
- [x] Step 5c — dollar signs shown as maths (2026-10-07: `escape_dollars()` added to `frontend/components/chat_window.py` and applied to history, the user's message and the live reply; tried on sample text including code spans and an already-escaped `$`; to be confirmed in the UI)
- [x] Step 6 — docs and cleanup (2026-10-07: `README.md` updated (intro, prerequisites, new "Foundry web-search agent" setup section with settings table and fallback behaviour, "Database and first admin" heading, usage notes on Sources and conversation naming, tests note, further reading); `thread_summary.md` updated (header, sections 1-4 and 8-16, new Sub-Plan 2 and frontend-fix records); `conversation.py` deleted. Afterwards, outside this plan: the Azure working copy was pushed to its own GitHub repository, joydeep-ganguly/AdaptiveRAGAz)
