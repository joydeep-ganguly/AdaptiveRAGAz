# AdaptiveRAG — Azure Sub-Plan 3: replace `backend/data/rag_data` with Azure Blob Storage

Written 2026-11-02. Decisions confirmed 2026-11-02. Status: **complete (2026-11-03); Step 6b is deferred to the end of Sub-Plan 4.**
Part of the incremental Azure move; whether the whole project moves to Azure is decided later.

This sub-plan mirrors Sub-Plan 1 (code-execution datasets to Blob), applied to the RAG PDFs.
It covers **only the blob side**. The Azure AI Search side (data source, index, skillset, indexer) is
**Sub-Plan 4**, planned separately once uploading to blob works. See section 7.

## 0. Azure resources (already created)

| Item | Value |
|---|---|
| Resource group | `rg-adaptiverag` |
| Storage account | `stadaptiverag32` (same account as Sub-Plan 1) |
| Container | `rag` |
| Directory / prefix | `rag_data/` |
| Sub-directories | `amazon/`, `apple/`, `google/` — one PDF in each. The account has no hierarchical namespace, so a "folder" exists only while a blob is under it; these 3 PDFs exist to create the three folders and are the test set for Sub-Plan 4. **Do not delete them.** |
| Local source | `backend/data/rag_data/{amazon (7), apple (6), google (7)}` — 20 PDFs |

Auth is the same as Sub-Plan 1: `DefaultAzureCredential` (`az login` locally, managed identity in Azure).

## 1. Goals

1. The admin RAG upload (`POST /admin/uploads/rag-data`) stores the PDF in blob storage under
   `rag_data/{company}/{constructed filename}` instead of the local `data/rag_data/` folder.
2. The upload **no longer triggers local ingestion** into ChromaDB. Ingestion will be handled by Azure AI Search
   (Sub-Plan 4).
3. Blob holds exactly **3 PDFs (one per company)** at the end of this sub-plan. The other 17 local PDFs and the retirement of the local folder wait until the Azure AI Search pipeline works (Sub-Plan 4), so that the first indexer run processes only 3 files (time and cost).
4. Every other place that used the local `rag_data` folder is updated or retired.

Not in this sub-plan: Azure AI Search (no data source, index, skillset or indexer exists yet, so **nothing flows into AI Search**), replacing ChromaDB, removing docling, changing retrieval. In Sub-Plan 4, creating the indexer will ingest the 3 test PDFs, and the index can then be queried to confirm the data is stored company-wise.

## 2. Where `rag_data` is used today

| # | File | What it does now | Change |
|---|---|---|---|
| 1 | `backend/app/config.py` | `rag_data_dir`, `resolved_rag_data_dir`; `ensure_storage_dirs()` creates the folder | Add RAG blob settings; remove the local-dir setting at the end |
| 2 | `backend/app/api/routers/admin.py` → `upload_rag_data_file` | Builds the filename from the form fields, writes to `rag_data/{company}/`, calls `ingest_docs_in_vectordb`, returns pages/skipped | Upload to blob; no ingestion; new response |
| 3 | `backend/app/api/schemas/admin.py` → `IngestionResponse` | `filename, pages_ingested, skipped` | Retire; reuse `FileUploadResponse` (`filename, message`) |
| 4 | `frontend/pages/3_Admin.py` (RAG tab) | "Upload and ingest", spinner about ingesting, shows pages added or "already ingested" | Relabel to "Upload"; show the upload message and a note that indexing happens later |
| 5 | `frontend/api_client.py` → `upload_rag_data_file` | 180 s timeout "for docling + embeddings" | Shorter timeout (60 s); fix comment |
| 6 | `backend/tests/conftest.py` (`admin_upload_dirs`), `backend/tests/integration/test_admin_endpoints.py` (`test_admin_upload_rag_data_happy_path`) | Assert the file exists on local disk and that ingestion was mocked | In-memory fake blob container, like `fake_code_data_blob` |
| 7 | `backend/.env.example`, `README.md`, `PROJECT_SUMMARY.md` | `RAG_DATA_DIR`, mentions of `data/rag_data/` | Add RAG blob settings; update docs |
| 8 | `backend/app/agent/retrieval/ingestion.py` | Converts a PDF with docling and writes pages to Chroma | **No longer called by anything** after step 4. Left in place until Sub-Plan 4: it is the reference for what the indexer and skillset must reproduce, and it is cheap to keep |

No change needed:
- `vectorstore.py`, `filters.py`, `ranking.py`, `doc_tools.py`: retrieval keeps using the existing Chroma index.
- `frontend/api_client.py` request shape and the form fields: unchanged.
- `router.py`, `graph.py`.

Side effect: `admin.py` stops importing `ingestion.py`, so it no longer pulls in docling at import time.

Search scope: Step 1 includes a repository-wide search to catch any other reference to `rag_data`. The files above were found with a search of `*.py`, `*.md`, `*.example`, `*.ps1`, `*.sh`, `*.txt`.
The older plan files (`AdaptiveRAG_Implementation_Plan.md`, `AdaptiveRAG_Phase2_Plan.md`, `AdaptiveRAG_Agent_Wiring_Addendum.md`) mention the folder and stay intact as history.

## 3. Design

- **New module `backend/app/storage/rag_data_store.py`**, next to `code_data_store.py`, importing only `config`. It has its own lazily built, cached container client (container `rag`). It does not touch `code_data_store.py`.
  - `build_filename(doc_type, fiscal_year, fiscal_quarter, company_name)` → `{company} {doc_type} [{quarter}] {year}.pdf` (same rule as today, so names stay compatible with `extract_metadata_from_filename`).
  - `parse_filename(name)` → the four metadata fields; used by the one-off copy script and the tests.
  - `upload(company_name, doc_type, fiscal_year, fiscal_quarter, data) -> RagDataFile` — validates, builds the name, uploads with `overwrite=True`, content type `application/pdf`.
  - `list_files(company_name=None) -> list[RagDataFile]` (company, name, size), sorted.
  - `download(company_name, name) -> bytes` — used to verify the copy in Step 6 and available for later steps.
  - Exceptions: `RagDataError` base; `InvalidRagDataFieldsError`, `RagDataFileTooLargeError`, `RagDataFileNotFoundError`, `RagDataStorageError`.
- **Blob layout:** container `rag`, blob name `rag_data/{company}/{company} {doc_type} [{quarter}] {year}.pdf`, for example `rag_data/amazon/amazon 10-q q1 2024.pdf`. Works whether directories are real or just name prefixes.
- **Settings** (all optional at startup, validated when the client is first built):
  - `AZURE_STORAGE_ACCOUNT_NAME` (existing, shared)
  - `AZURE_STORAGE_RAG_CONTAINER` (default `rag`)
  - `AZURE_STORAGE_RAG_DATA_PREFIX` (default `rag_data/`)
  - `RAG_DATA_MAX_UPLOAD_MB` (default 20)
  - The client must be created lazily (cached getter, never at import time), because `tests/conftest.py` imports `app.main`.
- **Blob metadata on upload:** `company_name`, `doc_type`, `fiscal_year`, and `fiscal_quarter` (omitted for annual reports), set by the admin route only. Keys use underscores, which both Azure Storage and the indexer accept; values are plain ASCII strings (the year is stored as text). These tags are a convenience: only files uploaded through the admin route carry them, so **the filename convention stays the source of truth** (see section 7 for how the metadata reaches the index).
- **Validation:** moves into the store so the router only maps errors to HTTP codes.
  - Extension must be `.pdf` (today's rule).
  - Company must match `^[a-z0-9\-]+$` after strip/lower (today's rule).
  - Size over the limit is rejected before anything is sent.
  - File must start with `%PDF-` (new, cheap; today only the extension is checked).
- **Admin route:** same URL, form fields, function name and status code (201). It stays a plain `def`. Errors: 400 invalid input, 413 too large (`HTTP_413_CONTENT_TOO_LARGE`), 502 storage failure with a generic message (Azure detail goes to the log only). Response is `FileUploadResponse(filename, message)`.
- **Frontend:** the RAG tab shows the success message and a note that the document will be searchable after the Azure indexing step is in place (until then, it is not).
- **Not doing:** any change to Chroma or retrieval; Azure AI Search resources; a shared blob helper for both stores (a small refactor candidate later, not worth touching working Sub-Plan 1 code now).

## 4. Steps (each leaves the app working and tests passing)

1. **Verify Azure and the code**
   - In the Portal: container `rag` is private; the 3 blobs are under `rag_data/amazon|apple|google/`; blob names follow `{company} {doc_type} [{quarter}] {year}.pdf` (names are case-sensitive and the pattern matters for Sub-Plan 4; ideally the 3 files include both shapes, a 10-K with no quarter and a 10-Q with a quarter, and are small, since page count drives cost); the signed-in account has **Storage Blob Data Contributor** on `stadaptiverag32`.
   - List the blobs from PowerShell:
     ```powershell
     az storage blob list --account-name stadaptiverag32 -c rag --prefix rag_data/ --auth-mode login --query "[].{name:name,size:properties.contentLength}" -o table
     ```
   - Find any other `rag_data` reference (from the project root):
     ```powershell
     git grep -n -i -E "rag_data|rag-data|admin_upload_dirs|ingest_docs_in_vectordb" -- '*.py' '*.md' '*.example' '*.ps1' '*.sh' '*.txt'
     ```
2. **Config (additive, no new packages):** `azure-storage-blob` and `azure-identity` are already in `requirements/base.txt`. Add the RAG settings to `config.py` and `.env.example`; add real values to `backend/.env`.
3. **Blob store module + unit tests** with a mocked client (`tests/unit/test_rag_data_store.py`): filename building and parsing, validation, size limit, `%PDF-` check, blob name and metadata, listing, download, error mapping. Then a smoke script `test_rag_data_store_manual.py` (next to `test_code_data_store_manual.py`) against the real container: list the 3 PDFs, download one, upload and delete a small throwaway PDF. Delete every throwaway blob right after each check: the Sub-Plan 4 indexer will ingest whatever is in the container on its first run.
4. **Admin upload switch:** `upload_rag_data_file` calls `rag_data_store.upload`, drops the ingestion call and the `ingestion` import; retire `IngestionResponse`. Replace `admin_upload_dirs` in `conftest.py` with a `fake_rag_data_blob` fixture and rewrite the RAG tests: happy path (name, blob path, content type, metadata, quarter case), overwrite, bad extension, bad company name, not a PDF, oversize, storage failure, non-admin still 403, and a test that ingestion is not called. (The upload from the Streamlit Admin page moves to Step 5: until the page is updated it would fail on the removed `skipped` field even though the PDF is stored.)
5. **Frontend:** update `3_Admin.py` (heading, button, spinner, result handling) and `api_client.py` (timeout, comment). Then upload a small throwaway PDF from the Streamlit Admin page, confirm it in the Portal (name, content type, metadata), and delete it.
6. **Verify the 3 blob PDFs against the local copies (the remaining 17 are deferred)**
   - A read-only check script (`scripts/verify_rag_data_blob.py`, kept out of the app code) compares each PDF in blob against the local file of the same name by size and SHA-256, using `rag_data_store.list_files` and `download`. It reports local files not yet in blob as "pending", not as errors. Expect 3 identical, 17 pending.
   - **Deferred to the end of Sub-Plan 4** (6b), once the indexer works on the 3 test files: you copy the remaining 17 PDFs to blob yourself (Portal or Storage Explorer), keeping each file's exact name and the `rag_data/{company}/` folder; the script confirms 20 identical; then remove `rag_data_dir`, `resolved_rag_data_dir` and its `ensure_storage_dirs()` entry from `config.py` and `RAG_DATA_DIR` from `.env.example` and `.env` (a repo search for `rag_data_dir`/`RAG_DATA_DIR` must return nothing), and, after your approval, delete `backend/data/rag_data` and the empty `backend/data`. Files copied by hand carry no blob metadata tags (section 7).
   - Until 6b the local folder and the `rag_data_dir` setting stay; nothing in the app reads them any more. `ingestion.py` does not read these settings, so it is unaffected either way.
7. **Docs:** update `README.md` (setup, admin section, new env vars, a note that RAG uploads are stored in Blob and indexing is a later step) and `PROJECT_SUMMARY.md` (a Sub-Plan 3 record). Older plan files stay intact.
8. **End-to-end check:** full pytest run; upload a PDF from the Admin page and see it in the Portal with the right name, content type and metadata; confirm the response and that nothing was written to Chroma (the chat still answers from the existing 20 PDFs); re-upload the same fields and confirm the overwrite. Afterwards delete any test blob you added, so the container holds only the 3 original PDFs.

## 5. Behaviour changes to be aware of

- **Nothing goes into Azure AI Search and nothing into Chroma.** An admin upload is only stored in blob. **Newly uploaded PDFs are not searchable in chat** until Sub-Plan 4 is done; Chroma stays frozen at the 20 PDFs already ingested. The Admin page says so.
- Blob holds only 3 PDFs on purpose (see goal 3), so the first indexer run in Sub-Plan 4 is small and cheap.
- **No more content-hash duplicate check.** An upload with the same company/type/quarter/year overwrites the same blob. The same file under different form values creates a second blob. The indexer handles change detection (decision 9).
- **Uploads are much faster** (no docling conversion, no embeddings); the 180 s timeout is no longer needed.
- Running the app or tests locally now requires an active `az login` with a role on the storage account (already true for the code-data store); unit tests use fakes and need no Azure.
- If Azure is unreachable, the upload endpoint returns 502; app startup does not depend on blob.
- `ingestion.py` becomes dead code until Sub-Plan 4 removes it together with docling.
- `/readiness` stays unchanged, as in Sub-Plan 1.

## 6. Decisions (made on 2026-11-02)

1. **Settings:** separate `AZURE_STORAGE_RAG_CONTAINER` (`rag`) and `AZURE_STORAGE_RAG_DATA_PREFIX` (`rag_data/`), while sharing `AZURE_STORAGE_ACCOUNT_NAME`.
2. **Same-name upload:** silent overwrite, as in Sub-Plan 1.
3. **Max upload size:** 20 MB (`RAG_DATA_MAX_UPLOAD_MB`); the largest local PDF is about 1.4 MB.
4. **Blob metadata tags** (company, type, year, quarter) written on upload: **yes, for now.** Not final: how the metadata reaches the index is settled in Sub-Plan 4 (section 7). The tags cost nothing and are harmless if unused.
5. **`%PDF-` content check** in addition to the extension check: yes.
6. **Response model:** reuse `FileUploadResponse`; retire `IngestionResponse`.
7. **`ingestion.py`:** keep, unused, until Sub-Plan 4.
8. **Remaining 17 local PDFs:** not loaded in this sub-plan. Blob keeps only the 3 test PDFs until the Sub-Plan 4 pipeline is proven; then you copy the 17 yourself (Step 6b) and I provide the verification script.
9. **Hash-based duplicate detection:** not needed — the indexer's change detection does that job.

## 7. What Sub-Plan 4 will build on (preview only, not planned here)

- Azure AI Search data source pointing at container `rag`, prefix `rag_data/`; index, skillset and indexer; the indexer runs on a schedule or on demand and picks up new and changed blobs automatically.
- First run: the indexer ingests the 3 test PDFs (one per company); query the index to confirm the data is stored company-wise. Create the indexer disabled or on-demand so it does not run before the design is ready. Then load the remaining 17 (Step 6b) and run again; change tracking should skip the unchanged 3.
- The index must reproduce today's page-level documents and fields: `company_name`, `doc_type`, `fiscal_year`, `fiscal_quarter`, `page`, `file_hash`, `source_file`, plus the page text and its vector.
- Retrieval (`vectorstore.py`) switches from Chroma to the index; the `$contains` keyword filter needs a replacement (hybrid search or similar).
- The embedding model choice (Gemini today) is decided there.

### Getting company / type / year / quarter into the index (open question for Sub-Plan 4)

Notes from the Microsoft documentation (to be confirmed against the real service in Sub-Plan 4):

- **Blob metadata needs no custom skill.** The blob indexer reads user-specified metadata, but only into index fields that already exist, of type `Edm.String`, with exactly the same name as the metadata key. Anything without a matching field is silently dropped. So `fiscal_year` arrives as a string unless converted.
- **One document per page needs index projections.** Fields reach each page document only through the skillset's `indexProjections` `mappings` (for example `{"name": "company_name", "source": "/document/company_name"}`); indexer `fieldMappings` do not feed them. Every child field must be mapped explicitly.
- **Expected but unverified:** that the blob metadata keys appear in the enrichment tree as `/document/<key>`. Check this in the portal's debug session before relying on it.
- **Weakness of blob tags:** only files uploaded through the admin route get them. The 3 files already in blob and the 17 you copy by hand will have none, and a page without them would break the company/year filters.
- **Alternative that does not depend on tags:** a custom Web API skill (a small Azure Function) that takes `metadata_storage_name` and returns the four fields, parsed with the same convention as today's `extract_metadata_from_filename`. It works for every blob regardless of how it was uploaded; its output is mapped into the pages through the projection `mappings`. This is the likely choice for Sub-Plan 4, with blob tags as an optional shortcut.
- **Not suitable:** indexer `fieldMappings` functions such as `extractTokenAtPosition` on the file name (annual reports have 3 name parts, quarterly ones 4, the last token carries `.pdf`, and the mapping cannot feed projections).
- **Typing:** whether `fiscal_year` is `Edm.String` or `Edm.Int32` in the index is decided there; today's code filters on an int.
- Then `ingestion.py`, docling and Chroma are retired, and the Chroma folder is removed.

## 8. Progress log

- [x] Decisions 1–9 confirmed (2026-11-02)
- [x] Step 1 — verify Azure and code (2026-11-03: signed in as the project account; 3 blobs under `rag_data/` with names matching the convention and sizes identical to the local files (800598 / 101359 / 1371793 bytes); data-plane access works with `az login`; container `rag` private; hierarchical namespace off; repo search found no `rag_data` reference beyond the section 2 table)
- [x] Step 2 — config (2026-11-03: `azure_storage_rag_container` (`rag`), `azure_storage_rag_data_prefix` (`rag_data/`) and `rag_data_max_upload_mb` (20) added to `config.py` and `.env.example`; `.env` updated; settings print `stadaptiverag32 rag rag_data/ 20`; pytest 144 passed; no new packages needed)
- [x] Step 3 — blob store module + tests (2026-11-03: created `app/storage/rag_data_store.py`, `tests/unit/test_rag_data_store.py`, `test_rag_data_store_manual.py`; `upload(original_filename, company_name, doc_type, fiscal_year, fiscal_quarter, data)` validates extension, fields, 1950–2050 year range, size and `%PDF-` header before sending, and writes `rag_data/{company}/{name}` with content type and blob metadata; 71 unit tests passed, full suite 215 passed; smoke script against the real container: listing matched the 3 PDFs, all 3 names parse, download OK, throwaway upload verified (content type and metadata) and deleted, container back to the 3 originals)
- [x] Step 4 — admin upload switch (2026-11-03: `upload_rag_data_file` now calls `rag_data_store.upload` and maps errors to 400 / 413 / 502; no ingestion, no `ingestion` import, no `get_settings`; `IngestionResponse` retired and the route returns `FileUploadResponse`; `admin_upload_dirs` replaced by the `fake_rag_data_blob` fixture; old RAG happy-path test replaced by 12 new tests; admin endpoint tests 23 passed, full suite 226 passed. The Admin-page upload check moved to Step 5)
- [x] Step 5 — frontend (2026-11-03: `3_Admin.py` RAG tab now says "Upload a financial filing", explains that the PDF is only stored in Blob Storage and not yet searchable, has an "Upload" button, spinner "Uploading to Blob Storage…", success message with the constructed filename, and "Upload failed: …" errors; `api_client.upload_rag_data_file` timeout 180 → 60 s with a corrected comment; manual check from the Admin page: throwaway PDF `smoke-test other 2000.pdf` uploaded, confirmed in Azure, deleted; a bad company name showed the expected error and stored nothing; container holds only the 3 original PDFs. Streamlit's own 200 MB picker limit left as is, the server limit is 20 MB)
- [x] Step 6 — verify the 3 blob PDFs against local copies (2026-11-03: added `scripts/verify_rag_data_blob.py` (read-only; statuses IDENTICAL / DIFFERENT / PENDING / BLOB ONLY / BAD NAME; `--require-all` makes PENDING an error); result: 3 identical, 0 different, 17 pending, 0 blob only, 0 bad name, RESULT: OK; all 20 local names follow the naming convention)
- [ ] Step 6b — (deferred to the end of Sub-Plan 4) copy the remaining 17, verify, retire the local folder
- [x] Step 7 — docs (2026-11-03: `README.md` updated (prerequisites, local `data/rag_data` note, Azure Blob section with the RAG container table and the "stored, not indexed" note, admin capabilities, further reading); `PROJECT_SUMMARY.md` updated (status, corpus, architecture, layout, Phase 2 notes, API surface, configuration, tests, design decisions, known gaps, deployment notes, new Sub-Plan 3 record in section 16); older plan files untouched)
- [x] Step 8 — end-to-end check (2026-11-03: Chroma count 1270 before and after; full pytest run from `backend/` 226 passed (a first run from the project root also collected the root-level manual scripts, which call live APIs, and failed in `test_services_manual.py`, a stale manual script that treats the `send_message` tuple as a message; not part of the suite and not changed); Admin-page upload of a throwaway PDF: content type `application/pdf`, size 101359, metadata company/type/year; same-name re-upload overwrote it (size 489645, one blob); a 10-q with quarter q1 added `fiscal_quarter: q1` to the metadata; test blobs deleted, `verify_rag_data_blob.py` shows 3 identical, 17 pending, 0 blob only, RESULT: OK. In-app chat check after the uploads: an Amazon 2023 question was answered with references to the Amazon 10-K 2023 (pages 51, 24 and 68), i.e. still retrieved from the Chroma index. **Sub-Plan 3 complete.** Open: Step 6b (copy the remaining 17 PDFs, verify with `--require-all`, retire the local folder and `rag_data_dir`) at the end of Sub-Plan 4; indexing of uploads is Sub-Plan 4)
