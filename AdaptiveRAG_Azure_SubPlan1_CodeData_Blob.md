# AdaptiveRAG — Azure Sub-Plan 1: replace `backend/data/code_data` with Azure Blob Storage

Written 2026-10-04. Status: **plan only, nothing implemented yet.**
Part of the incremental Azure move; whether the whole project moves to Azure is decided later.

## 0. Azure resources (already created)

| Item | Value |
|---|---|
| Resource group | `rg-adaptiverag` |
| Storage account | `stadaptiverag32` |
| Container | `code-execution` |
| Directory / prefix | `code_data/` |
| Content | Local `backend/data/code_data` content already copied: `apple_2024.xlsx`, `google_2024.xlsx`, `IMDB-Movie-Data.csv`, `titanic.csv` |

## 1. Goals

1. The code-execution agent fetches datasets from blob storage (not the local folder) and uploads them to the E2B sandbox.
2. The admin upload function `upload_code_data_file` stores files in blob storage.
3. Every other place that used the local `code_data` folder is updated or retired.

## 2. Where `code_data` is used today

| # | File | What it does now | Change |
|---|---|---|---|
| 1 | `backend/app/config.py` | `code_data_dir`, `resolved_code_data_dir`; `ensure_storage_dirs()` creates the folder | Add blob settings; remove the local-dir setting at the end |
| 2 | `backend/app/agent/tools/code_tools.py` → `upload_file` | Reads the file from disk, writes it into the E2B sandbox | Download from blob, then write to the sandbox |
| 3 | `backend/app/agent/nodes/code_execution_node.py` | `FilesystemFileSearchMiddleware(root_path=code_data_dir)` gives the agent `glob_search` and `grep_search` over the local folder | Replace with a blob-backed `list_code_data_files` tool |
| 4 | `backend/app/agent/prompts.py` → `CODE_EXECUTION_PROMPT` | Tells the agent to use `glob_search` on the "LOCAL filesystem (./data)" | Rewrite for the new tool names and blob source |
| 5 | `backend/app/api/routers/admin.py` → `upload_code_data_file` | Writes the file into local `code_data/` | Upload to blob |
| 6 | `backend/tests/conftest.py` (`admin_upload_dirs`), `backend/tests/integration/test_admin_endpoints.py` (`test_admin_upload_code_data_happy_path`) | Assert the file exists on local disk | Use an in-memory fake blob store |
| 7 | `backend/.env.example`, `README.md`, `backend/requirements/base.txt` | Mention the local folder; no Azure settings or packages | Add Azure settings and packages; update docs |

No change needed:
- `frontend/pages/3_Admin.py`, `frontend/api_client.py`: they only call `POST /admin/uploads/code-data`, whose request/response stay the same.
- `router.py`, `graph.py`: only refer to the `code_execution` route.
- `rag_data` stays local.

Unrelated finding: `get_dataset_info` in `code_tools.py` is not registered as a tool, takes a local path, and has a bug (`filepath.endswith == 'csv'` compares a method to a string, so it always reads Excel). Cleanup candidate, not blob work.

Files read when preparing this plan: config, code tools, code node, prompts, admin router and schemas, `main.py`, `deps.py`, `health.py`, `router.py`, `graph.py`, ingestion, frontend admin page and API client, `conftest.py`, admin tests, README, requirements, `.env.example`, `.gitignore`, scripts. Not read: other tests, SQL/web nodes, other frontend files, the older plan docs, the `.env` files. The connector has no text search, so Step 1 includes a search to catch any other reference.

## 3. Design

- **New module `backend/app/storage/code_data_store.py`**, importing only `config`, so both the agent and the admin router can use it. Functions: `list_files()`, `download(name) -> bytes`, `upload(name, stream)`. Exceptions: `CodeDataFileNotFoundError`, `CodeDataStorageError`.
- **Blob layout:** container `code-execution`, prefix `code_data/`, account `stadaptiverag32`. Works whether directories are real (hierarchical namespace) or just name prefixes.
- **Settings:** `AZURE_STORAGE_ACCOUNT_NAME` (optional at startup, validated when the blob client is first built), `AZURE_STORAGE_CONTAINER` (default `code-execution`), `AZURE_STORAGE_CODE_DATA_PREFIX` (default `code_data/`), `CODE_DATA_MAX_UPLOAD_MB` (default 50). Auth is `DefaultAzureCredential` only; no connection string (decision 1). The blob client must be created lazily (cached getter, never at import time), because `tests/conftest.py` imports `app.main`, which imports the agent graph and `code_tools`.
- **Client:** one cached synchronous `BlobServiceClient` (thread-safe). Endpoints stay plain `def`, per the project rule for blocking work.
- **`upload_file` tool:** parameter renamed `local_file_name` → `file_name`. Validates the name (bare filename, `.csv`/`.xlsx` only), downloads the blob, writes to the sandbox at `data/{name}` (sandbox path `/home/user/data/…` unchanged). Errors are returned as a string to the agent (like today's "file not found") rather than raised.
- **`list_code_data_files` tool:** no arguments; returns file names and sizes.
- **Admin upload:** same route, function name and response. Adds a size cap, streams to blob with the right content type, and maps storage failures to HTTP 502.
- **Not doing:** letting the sandbox pull from a SAS URL itself. It is more efficient for very large files but adds a security surface; the files here are small.

## 4. Steps (each leaves the app working and tests passing)

1. **Verify Azure and the code**
   - In the Portal: container access level is private; the 4 blobs are under `code_data/` (names are case-sensitive); the signed-in account has **Storage Blob Data Contributor** if Entra ID is used.
   - PowerShell, from the project root, to find any other `code_data` reference:
     ```powershell
     Get-ChildItem -Recurse -Include *.py,*.ps1,*.sh,*.md,*.txt,*.toml | Where-Object { $_.FullName -notmatch '\\.venv\\' } | Select-String code_data | Select-Object Path,LineNumber
     ```
2. **Dependencies and config (additive):** add `azure-storage-blob` and `azure-identity` to `requirements/base.txt`; add the new settings to `config.py` and `.env.example`; add real values to `backend/.env`.
3. **Blob store module + unit tests** with a mocked client. Then a smoke script run against the real container (list, download one file).
4. **Agent switch:** update `upload_file`, add `list_code_data_files`, remove the middleware from the node, rewrite the prompt. Add unit tests with a fake store and fake sandbox. Run the code-execution case from `test_agent_manual.py` (IMDB line chart) against real blob.
5. **Admin upload:** switch `upload_code_data_file` to blob; update `conftest.py` and admin tests; add tests for bad extension, bad filename, oversized file, storage error. Then upload a file from the Streamlit Admin page and confirm it appears in the Portal.
6. **Verify, then retire the local folder:** compare local files against blob (names, sizes). Remove `code_data_dir`, `resolved_code_data_dir` and the `ensure_storage_dirs` entry. After approval, delete `backend/data/code_data` and the `CODE_DATA_DIR` line in `.env`.
7. **Docs:** update `README.md` (setup, admin section, new env vars). Older plan files stay intact as history.
8. **End-to-end check:** full pytest run plus one chat flow: an analysis question on an existing file, then on a newly uploaded file.

## 5. Behaviour changes to be aware of

- The agent loses `grep_search` (content search); blob listing is enough for a handful of CSV/XLSX files.
- Each `upload_file` call now includes a blob download before the sandbox write (small files, so negligible).
- Running the app or `test_agent_manual.py` locally now requires an active `az login` with a role on the storage account.
- First call with `DefaultAzureCredential` can be slow while it probes credential sources.
- Azure unreachable: the agent tool reports an error to the user; app startup does not depend on blob.
- Blob names are case-sensitive (`IMDB-Movie-Data.csv`).

## 6. Decisions (made on 2026-10-05)

1. **Auth:** `DefaultAzureCredential` — `az login` locally, managed identity in Azure, no secret in `.env`. No connection-string fallback.
2. **Same-name upload:** keep today's silent overwrite.
3. **Max upload size:** 50 MB (setting `CODE_DATA_MAX_UPLOAD_MB`).
4. **Agent search:** drop `grep_search`; blob listing replaces `glob_search`.
5. **Unused `get_dataset_info`:** leave for now.
6. **Readiness check:** leave `/readiness` unchanged for now; revisit when hosting on Azure.

## 7. Progress log

- [x] Decisions 1–6 made (2026-10-05)
- [x] Step 1 — verify Azure and code (2026-10-05: 4 blobs match local sizes; container private; Storage Blob Data Contributor held; hierarchical namespace off; no other `code_data` references found beyond the table in section 2)
- [x] Step 2 — dependencies and config (2026-10-05: `azure-storage-blob` and `azure-identity` in `base.txt`; settings added to `config.py` and `.env.example`; `.env` updated; settings print correctly; pytest passes unchanged)
- [x] Step 3 — blob store module + tests (2026-10-05: created `app/storage/__init__.py`, `app/storage/code_data_store.py`, `tests/unit/test_code_data_store.py`, `test_code_data_store_manual.py`; contents reviewed against the proposal; unit tests, full pytest suite and manual smoke script all passed)
- [x] Step 4 — agent switch (2026-10-05: `code_tools.py` has `list_code_data_files` (optional `name_filter`) and blob-backed `upload_file(file_name)`; `code_execution_node.py` uses the three tools and no filesystem middleware; `CODE_EXECUTION_PROMPT` rewritten; added `tests/unit/test_code_execution_agent.py` and `test_code_agent_manual.py`; contents reviewed; unit tests, full pytest suite and manual end-to-end run all passed)
- [x] Step 5 — admin upload (2026-10-05: `admin.py` `upload_code_data_file` now calls `code_data_store.upload` (bytes, silent overwrite) and maps errors to 400 / 413 (`HTTP_413_CONTENT_TOO_LARGE`) / 502 with a generic message; `conftest.py` has a RAG-only `admin_upload_dirs` plus new `fake_code_data_blob` fixture; `test_admin_endpoints.py` code-data tests rewritten (happy path, xlsx content type, overwrite, bad extension, oversize, storage failure); file contents reviewed; both pytest runs reported with only a Starlette deprecation warning, since fixed; manual Admin-page check passed: new CSV uploaded, then re-uploaded with added data)
- [x] Step 6 — verify and retire local folder (2026-10-05: blob copy verified IDENTICAL to local by name, size and SHA-256 (4 files); `code_data_dir`, `resolved_code_data_dir` and its `ensure_storage_dirs()` entry removed from `config.py`; `CODE_DATA_DIR` removed from `.env.example` and the developer's `.env`; repo search for `code_data_dir`/`CODE_DATA_DIR` returns nothing; `backend/data/code_data` deleted; full pytest passes and the folder is not recreated at startup)
- [x] Step 7 — docs (2026-10-05: `README.md` updated: Azure prerequisites bullet, `data/` no longer lists datasets, new "Azure Blob Storage (code-execution datasets)" setup subsection with `az login`, `DefaultAzureCredential` and the four settings, admin-upload description (50 MB default, stored in Blob, overwrite), tests note mentions Azure, further-reading entry for this plan; older plan files left as history)
- [x] Step 8 — end-to-end check (2026-10-06: full pytest passed; in Chat the agent listed the 4 files and analysed `titanic.csv` from Blob; a newly uploaded test CSV via the Admin page was listed and analysed correctly; optional missing-file check not run; test blob deleted and the container again holds only the 4 original files. **Sub-Plan 1 complete.** Open items outside this sub-plan: decision 6 (blob check in `/readiness`) deferred until hosting on Azure; unused `get_dataset_info` in `code_tools.py` left as is (decision 5); README architecture diagram has no code fence and no Blob box)
