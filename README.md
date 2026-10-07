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

## Prerequisites

- Python 3.10+
- A Google AI Studio API key (`GOOGLE_API_KEY`)
- An E2B API key (`E2B_API_KEY`) — powers the code-execution sandbox
- An Azure Storage account with a private blob container that holds the code-execution
  datasets, the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
  (`az login`), and the **Storage Blob Data Contributor** role on that account
- Optional, for web search through Microsoft Foundry: a Foundry project with an agent named
  `adaptiverag-websearch` that has the built-in Web Search tool, and the **Foundry User**
  role on that project. Without it, web questions are answered through DuckDuckGo only.

The backend's `storage/` (Chroma index, employee database) and `data/` (sample
filings) folders are expected to already be present in the repo — some of these
are large binary files handled via Git LFS or shared separately; ask a teammate
if they're missing after cloning. The code-execution datasets are not in the repo:
they live in Azure Blob Storage (see "Azure Blob Storage" below).

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
`AZURE_STORAGE_ACCOUNT_NAME` for the code-execution datasets (next section). Web search
through Foundry is optional and has its own settings (the section after that).

#### Azure Blob Storage (code-execution datasets)

The code-execution agent reads its `.csv`/`.xlsx` datasets from Azure Blob Storage,
and the Admin page uploads new ones there. Sign in once with the Azure CLI:

```bash
az login
```

The app authenticates with `DefaultAzureCredential` — your `az login` session locally,
a managed identity when hosted in Azure — so no keys or connection strings are stored.
Your identity needs the **Storage Blob Data Contributor** role on the storage account.

| `.env` setting | Default | Meaning |
|---|---|---|
| `AZURE_STORAGE_ACCOUNT_NAME` | *(none — required)* | Storage account name |
| `AZURE_STORAGE_CONTAINER` | `code-execution` | Container holding the datasets |
| `AZURE_STORAGE_CODE_DATA_PREFIX` | `code_data/` | Folder (blob prefix) inside the container |
| `CODE_DATA_MAX_UPLOAD_MB` | `50` | Largest dataset the Admin page accepts |

Only `.csv`/`.xlsx` files directly under the prefix are used. The backend starts
without Azure configured; only the code-execution agent and the Admin dataset
upload report an error until it is.

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
- Upload a financial filing PDF — this stores it and immediately ingests it into
  the RAG index. You'll provide the company name, document type, fiscal year,
  and (for quarterly filings) the fiscal quarter, so it's tagged correctly for
  retrieval.

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
  moved to Azure Blob Storage — see the next entry.)
- `AdaptiveRAG_Azure_SubPlan1_CodeData_Blob.md` — moving the code-execution
  datasets from the local `data/code_data` folder to Azure Blob Storage: design,
  steps and progress log.
- `AdaptiveRAG_Azure_SubPlan2_WebSearch_Foundry.md` — replacing the LangGraph
  web-search agent with the Microsoft Foundry agent `adaptiverag-websearch`
  (DuckDuckGo kept as the fallback): design, timings, steps and progress log.