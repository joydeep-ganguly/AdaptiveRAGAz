# AdaptiveRAG

A multi-agent RAG assistant: financial-document Q&A (10-K/10-Q/8-K filings), an
employee-database SQL agent, general web search (a Microsoft Foundry agent with a
built-in web-search tool, with DuckDuckGo as a fallback), and a sandboxed code-execution
agent for data analysis — behind a FastAPI backend with JWT auth and admin roles,
and a Streamlit frontend.

## Architecture
┌────────────┐ HTTP (Bearer JWT) ┌──────────────────┐
│ Streamlit │ ───────────────────────────────► │ FastAPI │
│ frontend │ ◄─────────────────────────────── │ backend │
└────────────┘ └────────┬─────────┘
│
┌──────────────────────────────────────┼──────────────────────────┐
│ │ │
┌───────▼────────┐ ┌─────────▼─────────┐ ┌─────────▼─────────┐
│ app.db │ │ LangGraph agent │ │ checkpoints.db │
│ (users, │ │ (adaptive RAG / │ │ (per-conversation │
│ conversations) │ │ sql / web / code)│ │ memory) │
└────────────────┘ └─────────┬──────────┘ └────────────────────┘
┌────────────────────────┼─────────────────────────┐
┌──────▼──────┐ ┌─────────▼────────┐ ┌──────────▼─────────┐
│ Chroma │ │ employees_db │ │ E2B sandbox / │
│ (financial │ │ (.db, SQL agent) │ │ DuckDuckGo (ddgs) │
│ docs) │ └───────────────────┘ └─────────────────────┘
└─────────────┘


The frontend only ever talks to the FastAPI backend over HTTP with a Bearer token —
never directly to the database, the agent, or any external API.

Web questions go to the Microsoft Foundry agent `adaptiverag-websearch` (the diagram's
DuckDuckGo box is now the fallback, used when Foundry is not configured or fails).

Filing questions are answered from an Azure AI Search index when `RETRIEVAL_BACKEND=azure` (the diagram's
Chroma box is the local fallback, selected with `RETRIEVAL_BACKEND=chroma`). PDFs in Blob Storage are indexed
by an Azure AI Search indexer through two custom skills in an Azure Function (see "Azure AI Search" below).

## Prerequisites

- Python 3.10+
- A Google AI Studio API key (`GOOGLE_API_KEY`)
- An E2B API key (`E2B_API_KEY`) — powers the code-execution sandbox
- An Azure Storage account with private blob containers that hold the code-execution
  datasets and the RAG filing PDFs, the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
  (`az login`), and the **Storage Blob Data Contributor** role on that account
- For filing retrieval from Azure: an Azure AI Search service with the index, data source, skillset and
  indexer from `azure/search/`, the Function app from `azure_functions/rag_skills/` and a Document
  Intelligence resource (see "Azure AI Search" below), and the **Search Index Data Reader** role on the
  search service. Without them set `RETRIEVAL_BACKEND=chroma`.
- Optional, for web search through Microsoft Foundry: a Foundry project with an agent named
  `adaptiverag-websearch` that has the built-in Web Search tool, and the **Foundry User**
  role on that project. Without it, web questions are answered through DuckDuckGo only.

The backend's `storage/` folder (Chroma index, employee database) is expected to
already be present in the repo — some of these are large binary files handled via
Git LFS or shared separately; ask a teammate if they're missing after cloning. The
code-execution datasets and the uploaded RAG filings are not read from disk: they
live in Azure Blob Storage (see "Azure Blob Storage" below). `data/rag_data/` still
holds local copies of the sample filings, but the app no longer reads it.

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements/dev.txt   # heads up: docling is a large install, budget extra time
copy .env.example .env                # Windows; `cp .env.example .env` on macOS/Linux
```

Fill in `.env`: at minimum `GOOGLE_API_KEY`, `E2B_API_KEY`, and `JWT_SECRET_KEY`
(any random string signs login tokens — e.g. generate one with
`python -c "import secrets; print(secrets.token_hex(32))"`), plus
`AZURE_STORAGE_ACCOUNT_NAME` for the Blob Storage datasets and filings (next section). Web search
through Foundry is optional and has its own settings (the section after that).

#### Azure Blob Storage (code-execution datasets and RAG filings)

The code-execution agent reads its `.csv`/`.xlsx` datasets from Azure Blob Storage,
and the Admin page uploads new ones there. The Admin page also uploads the RAG filing
PDFs to Blob Storage (second table below). Sign in once with the Azure CLI:

```bash
az login
```

The app authenticates with `DefaultAzureCredential` — your `az login` session locally,
a managed identity when hosted in Azure — so no keys or connection strings are stored.
Your identity needs the **Storage Blob Data Contributor** role on the storage account.

| `.env` setting | Default | Meaning |
|---|---|---|
| `AZURE_STORAGE_ACCOUNT_NAME` | *(none — required)* | Storage account name (shared by both containers) |
| `AZURE_STORAGE_CONTAINER` | `code-execution` | Container holding the datasets |
| `AZURE_STORAGE_CODE_DATA_PREFIX` | `code_data/` | Folder (blob prefix) inside the container |
| `CODE_DATA_MAX_UPLOAD_MB` | `50` | Largest dataset the Admin page accepts |

Only `.csv`/`.xlsx` files directly under the prefix are used.

The RAG filing PDFs go to a second container of the same account, one folder per
company, named from the upload form fields:
`rag/rag_data/{company}/{company} {doc_type} [{quarter}] {year}.pdf`
(for example `rag_data/amazon/amazon 10-q q1 2024.pdf`).

| `.env` setting | Default | Meaning |
|---|---|---|
| `AZURE_STORAGE_RAG_CONTAINER` | `rag` | Container holding the filings |
| `AZURE_STORAGE_RAG_DATA_PREFIX` | `rag_data/` | Folder (blob prefix) inside the container |
| `RAG_DATA_MAX_UPLOAD_MB` | `20` | Largest PDF the Admin page accepts |

The Admin upload only **stores** the PDF (its company, type, year and quarter are also
written as blob metadata). Azure AI Search indexes it at its next indexer run (see the next
section), so a new filing is searchable in chat after that run.

The backend starts without Azure configured; only the code-execution agent and the
Admin uploads report an error until it is.

#### Azure AI Search (RAG filings)

Chat answers about filings are retrieved from an Azure AI Search index: one search document per PDF
page, holding the page text, a Gemini embedding and the company, document type, year, quarter and page
number. `RETRIEVAL_BACKEND` chooses the source: `azure`, or `chroma` (the old local index, kept as a
fallback; it is also the default when the setting is absent). Sign in with `az login`; your identity
needs the **Search Index Data Reader** role on the search service.

| `.env` setting | Default | Meaning |
|---|---|---|
| `RETRIEVAL_BACKEND` | `chroma` | `azure` reads from Azure AI Search, `chroma` from the local index |
| `AZURE_SEARCH_ENDPOINT` | blank | `https://<service>.search.windows.net` |
| `AZURE_SEARCH_INDEX_NAME` | `idx-adaptiverag-trial` | Index to query |
| `AZURE_SEARCH_KEYWORD_MODE` | `hard` | `hard`: a page must contain at least one ranking keyword; `hybrid`: keywords only add to the score |
| `AZURE_SEARCH_EXHAUSTIVE` | `false` | Exhaustive vector search instead of the approximate one |

How a PDF becomes searchable: the PDF is in Blob Storage; an Azure AI Search indexer
(`ixr-adaptiverag-rag`, scheduled every 24 hours, or run on demand in the portal) calls the `pdf-pages`
skill of the Azure Function app in `azure_functions/rag_skills/` (Document Intelligence turns the PDF into
one markdown text per page; company, type, year and quarter come from the file name) and its `embed` skill
(Gemini `gemini-embedding-001`), then writes the pages to the index. Changing a PDF re-indexes it, and
deleting its blob removes its pages at the next run (blob soft delete tracking). The definitions of the
index, data source, skillset and indexer are in `azure/search/` (the function key and the subscription id
are placeholders there). `scripts/verify_search_index.py` checks the index against the expected page
counts and runs sample questions. While the index holds only some of the filings, questions about the
others find nothing in `azure` mode; `RETRIEVAL_BACKEND=chroma` brings them back.

#### Foundry web-search agent (web search)

Web questions are answered by the Microsoft Foundry agent `adaptiverag-websearch`, which
runs its own web-search tool. The agent, its model and its instructions live in Foundry,
not in this repo. The answer ends with a numbered **Sources** list built from the
citations the agent returns. The backend calls the agent by name and statelessly (no
Foundry conversation is created), so the latest version of the agent is always used.

It uses the same `az login` / managed-identity sign-in as above; your identity needs the
**Foundry User** role on the Foundry project.

| `.env` setting | Default | Meaning |
|---|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | *(blank)* | Project endpoint; blank means "use DuckDuckGo only" |
| `FOUNDRY_WEB_SEARCH_AGENT_NAME` | `adaptiverag-websearch` | Agent called, by name |
| `FOUNDRY_REQUEST_TIMEOUT_SECONDS` | `45` | Hard limit on the whole Foundry call (a typical call takes 20-30 s) |
| `WEB_SEARCH_FALLBACK_ENABLED` | `true` | Answer with the DuckDuckGo agent when Foundry fails or times out |

If Foundry is not configured, fails or is too slow, the DuckDuckGo agent answers instead
(its answers have no Sources list and its search results can be outdated). If the
fallback is switched off, or DuckDuckGo fails too, the chat shows "Web search is
temporarily unavailable. Please try again later." Note that web queries leave the
Azure compliance boundary (the tool is backed by Bing), and the web-search tool costs
extra on top of normal model usage.

#### Database and first admin

```bash
python -m app.db.init_db
python ../scripts/seed_admin_user.py
```

The very first user ever created — whether through this script or the frontend's
registration form — automatically becomes an **admin**. Everyone after that is a
regular user unless an admin explicitly creates them as one.

Start the backend:
```bash
uvicorn app.main:app --reload --port 8000
```
or, once `.env` is set up, run `..\scripts\run_backend_dev.ps1` (Windows) /
`../scripts/run_backend_dev.sh` (macOS/Linux) from inside `backend/`.

Confirm it's running at http://localhost:8000/docs.

### 2. Frontend

In a separate terminal:
```bash
cd frontend
pip install -r requirements.txt
copy .env.example .env
streamlit run streamlit_app.py
```
or `..\scripts\run_frontend_dev.ps1` / `../scripts/run_frontend_dev.sh`.

## Using it

- Register or log in.
- Ask questions: financial filings ("What was Amazon's revenue in 2023?"),
  employee data ("Who is the CFO?"), general knowledge, or data analysis
  ("chart average ratings from IMDB-Movie-Data.csv").
- Web questions ("What are today's top technology headlines?") return an answer with a
  numbered **Sources** list. Answers take roughly 20-35 s.
- Conversations persist and are listed in the sidebar. "New conversation" opens an empty
  chat; the conversation is created when you send the first message and is named after it
  (the first 40 characters or so).

## Admin capabilities

If you're an admin, an **Admin** page appears in the sidebar:
- Create new users, choosing their type (`user` or `admin`).
- Upload a `.csv`/`.xlsx` (up to 50 MB by default) for the code-execution agent to
  analyze. It is stored in Azure Blob Storage, and uploading a file with an
  existing name overwrites it.
- Upload a financial filing PDF (up to 20 MB by default) to Azure Blob Storage. You'll
  provide the company name, document type, fiscal year, and (for quarterly filings) the
  fiscal quarter; the file is stored as `{company}/{company} {doc_type} [{quarter}] {year}.pdf`,
  and uploading the same company, type and period again overwrites it. The upload only
  stores the filing; it becomes searchable in chat after the next Azure AI Search indexer run (see the
  Azure AI Search section).

## Running tests

```bash
cd backend
pytest
```
Nothing in the suite touches live Gemini/E2B/DuckDuckGo/Azure/Foundry APIs or your real
database/data folders — safe to run anytime.

## Further reading

- `AdaptiveRAG_Implementation_Plan.md` — the original notebook-to-service
  migration plan: architecture rationale, the concurrency bugs fixed for
  multi-user use, and the initial build order.
- `AdaptiveRAG_Phase2_Plan.md` — admin roles, the `rag_data`/`code_data` folder
  split, and the PDF upload/ingestion pipeline. (The `code_data` folder has since
  moved to Azure Blob Storage, and PDF uploads now go to Blob Storage too, with
  indexing left to Azure — see the next entries.)
- `AdaptiveRAG_Azure_SubPlan1_CodeData_Blob.md` — moving the code-execution
  datasets from the local `data/code_data` folder to Azure Blob Storage: design,
  steps and progress log.
- `AdaptiveRAG_Azure_SubPlan2_WebSearch_Foundry.md` — replacing the LangGraph
  web-search agent with the Microsoft Foundry agent `adaptiverag-websearch`
  (DuckDuckGo kept as the fallback): design, timings, steps and progress log.
- `AdaptiveRAG_Azure_SubPlan3_RAG_Blob.md` — moving the RAG filing PDFs from the local
  `data/rag_data` folder to Azure Blob Storage (the upload only stores the file; indexing
  is a later step): design, steps and progress log.
- `AdaptiveRAG_Azure_SubPlan4_RAG_AISearch.md` — indexing the RAG filing PDFs with Azure AI Search
  (Document Intelligence and Gemini embeddings through an Azure Function) and reading chat retrieval from
  the index, with Chroma kept as a switchable fallback: design, decisions, steps and progress log.
