# AdaptiveRAG — Azure Sub-Plan 4: ingest the RAG PDFs with Azure AI Search and retrieve from it

Written 2026-11-11. Decisions confirmed 2026-11-11 (section 7). Status: **Steps 1 to 11, 13 and 15 done (2026-11-18); Step 14 (retire) deferred; Step 12 (paid service) optional and last.** Code and Azure changes are made step by step, when you start each one.
Part of the incremental Azure move. The aim is to run all the sub-agent operations (RAG, SQL, code generation, web search) in the cloud instead of on the local machine; this sub-plan covers RAG, and the SQL and code agents are planned separately afterwards.

Sub-Plan 3 put the RAG PDFs in Blob (`rag` / `rag_data/`). This sub-plan builds the Azure AI Search side: any PDF uploaded to the blob is ingested automatically into a search index, and retrieval reads from that index instead of ChromaDB, **with the smallest possible change to the retrieval logic**. The local ingestion module (docling and Chroma) is retired only after the Azure results are accepted.

## 0. Azure resources

| Item | Value | State |
|---|---|---|
| Resource group | `rg-adaptiverag` | exists |
| Storage account / container / prefix | `stadaptiverag32` / `rag` / `rag_data/` | exists; holds the 3 test PDFs |
| AI Search service (trial) | `as-adaptiverag-free` (Free tier, Central US) | exists; from 2026-11-17 it is the working service: the app reads from it at Step 13 and more PDFs are added to it as needed, within the Free limits below |
| Index | `idx-adaptiverag-trial` | created and checked 2026-11-10 (schema in appendix A) |
| Data source | `azureblob-1794320280049-datasource`: container `rag`, folder `rag_data`, system-assigned managed identity, native blob soft delete tracking on since Step 11; definition in `azure/search/datasource.json` (subscription id is a placeholder there) | exists |
| Skillset, indexer | `ss-adaptiverag-rag`, `ixr-adaptiverag-rag` (indexer disabled until Step 10); definitions in `azure/search/` | created 2026-11-17 |
| Function app for the custom skills | `azure_functions/rag_skills/` in the repo; app `func-adaptiverag-skills` (Flex Consumption, Python, East US 2) with its own storage account `stfuncadaptiverag32` | deployed 2026-11-16 (managed identity roles: Cognitive Services User on `di-adaptiverag`, Storage Blob Data Reader on `stadaptiverag32`; application settings for the endpoint, allowed blob host, Gemini key, model, 20 pages per range, 6 in parallel) |
| Azure Document Intelligence resource | `di-adaptiverag`, East US 2, endpoint `https://di-adaptiverag.cognitiveservices.azure.com/` | created 2026-11-15 (tier S0, confirmed at Step 7 because it read all 94 pages of a PDF; the free tier would read only the first 2 pages of a PDF) |
| AI Search service (full ingestion) | none | optional, created only if the Free service is outgrown (Step 12, moved to the end): a **new** paid-tier service in **East US 2** (same region as the Document Intelligence resource and the Function app), using the same definitions; the Free service (Central US) is then dropped |

Free tier limits that shape the trial (Microsoft limits page): 50 MB storage; 3 indexes, indexers, data sources and skillsets; blob indexer run time 3 minutes (3 to 10 minutes with a skillset); one indexer invocation every 180 seconds; 16 MB maximum source file; 20 built-in-skill documents per indexer per day. The page gives no Free-tier vector quota. Custom Web API skills: timeout 30 s by default, 230 s at most.

## 1. Goals

1. A PDF uploaded to `rag/rag_data/...` is indexed by the indexer with no manual step, one search document per PDF page, with the same fields as today's Chroma records.
2. Chat retrieval reads from the index. `build_search_kwargs` and `search_docs` (Chroma) **stay in place unchanged**, and a setting switches the backend, so going back to Chroma needs no code revert.
3. Everything downstream of the search call stays as it is: `extract_filters`, `generate_ranking_keywords`, the BM25 re-rank, the tool output format.
4. Prove it on 3 PDFs (Free tier) first, cut the app over to it, then add PDFs as needed on the Free tier. The paid full-ingestion service for all 20 PDFs is optional and comes last (decision 2026-11-17).
5. After acceptance: retire `ingestion.py`, docling, Chroma and the local `rag_data` folder (Sub-Plan 3 Step 6b).

Not in this sub-plan: replacing the BM25 re-rank (for example with the semantic ranker), a button that starts the indexer from the Admin page, network isolation (private endpoints), replacing the Gemini embedding model.

## 2. What the Chroma index holds today (the target shape)

Collection `financial_docs`, 1270 page records, 3072-dimension Gemini vectors. Read from the database itself (2026-11-10).

| Chroma field | Type, presence | Index field | How it will be filled |
|---|---|---|---|
| `company_name` | string, all | `company_name` (filterable) | parsed from the blob file name |
| `doc_type` | string, all | `doc_type` (filterable) | parsed from the file name |
| `fiscal_year` | **int**, all | `fiscal_year` Int32 (filterable) | parsed from the file name |
| `fiscal_quarter` | string, **669 of 1270**; absent for annual reports | `fiscal_quarter` (filterable, nullable) | parsed; null when the name has no quarter |
| `page` | int, 1 to 150 | `page` Int32 (retrievable) | page number from the page splitter |
| `source_file` | string, all | `source_file` (retrievable) | blob file name |
| page text (`chroma:document`, about 4.4k characters on average, 21k at most) | string, all | `page_text` (searchable, `en.lucene`, retrievable) | markdown of the page |
| vector | 3072 floats | `vector` (searchable, not stored) | embedding of `page_text` |
| `file_hash` | string, all | **dropped** | the indexer's change tracking replaces it |
| Chroma id (UUID) | | `id` (key, keyword analyzer) | generated by the index projection |
| (new) | | `parent_id` (filterable) | generated by the index projection; links every page to its PDF |

Two facts to carry through: the Chroma data covers 19 distinct PDFs, while 20 are in the local folder (one Google file was never ingested); and `amazon 10-q q3 2024` has 150 pages against about 50 for the other Amazon 10-Qs (probably a page-split problem). Chroma is therefore a good baseline for the 3 trial PDFs (93 + 9 + 111 = 213 pages) but not a perfect one for the full set.

## 3. What the retrieval code does today

`doc_tools.retriever_docs` → `extract_filters(query)` (LLM into `company_name`, `doc_type`, `fiscal_year` int, `fiscal_quarter`) and `generate_ranking_keywords(query)` (5 keywords) → `search_docs(query, filters, keywords, k=10*k)` → `rank_documents_by_keywords(results, keywords, k=k)` (BM25Plus over headings and the following paragraph) → prints every metadata key and value plus the page text.

`search_docs` is the only Chroma-specific step: metadata equality filters (`$and` for several), a `where_document` `$contains` / `$or` keyword filter, and MMR over the vectors with `fetch_k = k*20`.

## 4. Design

### 4.1 Retrieval on Azure (Part A)

- **New module** `backend/app/agent/retrieval/azure_search_store.py`. `vectorstore.py` is not touched. It exposes `search_docs_azure(query, filters, ranking_keywords, k)` with the same signature and the same return type (`list[Document]`) as `search_docs`. It uses the `azure-search-documents` package directly; LangChain's `AzureSearch` class is not used because it expects its own field names.
- **Backend switch:** a setting `RETRIEVAL_BACKEND` (`chroma` default, or `azure`). `retriever_docs` picks `search_docs` or `search_docs_azure` from it. This is the only edit outside the new module, a few lines in `doc_tools.py`. Reverting means setting the variable back to `chroma`.
- **Metadata filters:** the filter dict becomes an OData `$filter`, joined with `and`: `company_name eq 'amazon' and doc_type eq '10-k' and fiscal_year eq 2023`. Strings are single-quoted with embedded quotes doubled, `fiscal_year` is written as a number, and only the four known keys are accepted. Empty filters send no `$filter`. The behaviour for `None` is unchanged: `extract_filters` already drops it.
- **MMR is not available** on Azure AI Search and the vector field is not stored, so there is no client-side MMR either. The query fetches the same number of candidates as today (`k = 10*k`, 50 by default) and the existing BM25 re-rank picks the final 5. The diversity MMR adds is lost; the BM25 step decides the final order anyway.
- **Keywords replace `$contains`.** Chroma's `$contains` was a hard filter: a page without any of the 5 keywords could not be returned. In hybrid search the keywords only add to the score, so a page without them can still arrive through the vector leg. Two modes, selected by `AZURE_SEARCH_KEYWORD_MODE`:
  - `hybrid`: the keywords go into `search_text` (OR between them; a multi-word keyword is sent as a quoted phrase, for example `"net sales" | "operating income"`). The vector and keyword results are fused by the service.
  - `hard`: in addition, the `$filter` gets `search.ismatch('<keywords>', 'page_text', 'simple', 'any')`, which restores the "must contain at least one keyword" rule. The vector search is pre-filtered, like Chroma's.
  - Keywords are cleaned before use: characters that are special in the simple query syntax (`+ | " * ( ) ~ \ -`) are removed or escaped, and a keyword that becomes empty is dropped. Matching is token-based with stemming (`en.lucene`), not a raw substring match, so results will not be identical; the test in Step 4 shows which mode is closer.
  - Default: `hard` (decision D3). `hybrid` stays selectable for comparison.
- **Vector query:** the query vector comes from the same `get_embeddings().embed_query` that Chroma uses, so the query side does not change. `VectorizedQuery(vector, k_nearest_neighbors=k, fields="vector")`. An optional `exhaustive` flag (exact search instead of HNSW) is available for such a small corpus; off by default.
- **Result mapping:** each hit becomes `Document(page_content=page_text, metadata={company_name, doc_type, fiscal_year, fiscal_quarter, page, source_file})`. Fields that are `None` are dropped, as in Chroma. `file_hash` is no longer in the tool output; it was never useful to the prompt.
- **Errors:** a failed search call is logged and re-raised as one clear error; an empty result stays an empty list ("No documents found" as today).
- **Auth and settings:** `RETRIEVAL_BACKEND`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME`, `AZURE_SEARCH_KEYWORD_MODE`, `AZURE_SEARCH_EXHAUSTIVE`; no API key (decision D10). The code uses `DefaultAzureCredential` (needs the role **Search Index Data Reader** on the service, and the service's API access control must allow role-based access). All settings are optional at startup; the client is created lazily and cached, never at import time (`tests/conftest.py` imports `app.main`). New package: `azure-search-documents`.

### 4.2 Test the retrieval before the ingestion exists (Part A, Step 4)

The 3 test PDFs are already in Chroma (213 pages with their Gemini vectors). A read-only script copies exactly those rows (text, metadata, stored vectors) into the trial index, marked `parent_id = "seed"`. The Azure query path can then be compared with Chroma on the same data before any skillset exists, so retrieval problems and ingestion problems are not mixed. The seeded rows are deleted afterwards (`parent_id eq 'seed'`) before the indexer runs.

A fixed set of 8 to 10 questions (2 or 3 per company, with and without year and quarter, one of them with no company filter) is run against both backends. Compared: the candidate list before the BM25 step (overlap of the top 10) and the final 5 pages. Chat answers are spot-checked with page references.

### 4.3 Ingestion pipeline (Part B)

Blob → indexer → skillset → index, one search document per page.

- **Indexer settings:** `allowSkillsetToReadFileData: true`, which gives the skills the PDF as `/document/file_data`; `indexedFileNameExtensions: ".pdf"`; no schedule during the trial (on demand). To avoid the content-extraction limits (256,000 characters on Free) and time spent on text the skillset does not use, `dataToExtract: storageMetadata` is tried first. Created last, once the skills are tested (a new indexer runs once on creation).
- **Two custom Web API skills** (one small Azure Function app, Python), instead of one skill that also returns vectors; this keeps request and response sizes small:
  1. **`pdf-pages` skill**, run once per document. Input: `file_name` (`metadata_storage_name`) and either `file_data` or `url` (`metadata_storage_path`; the Function then reads the blob with its own identity, which avoids sending a large base64 file through the indexer; Step 9 picks one). It sends the PDF to Document Intelligence (a file of more than 20 pages in page ranges of 20, 6 in parallel) and splits the markdown on its page-break markers, drops the page header, footer and page-number comments, and leaves out pages with no text (a warning lists them) while the other pages keep their real page number. Output: `pages` (each item: `page`, `text`) and the document-level fields `company_name`, `doc_type`, `fiscal_year` (int), `fiscal_quarter` (null for 3-part names), `source_file`. The metadata is parsed from the file name with the same convention as `extract_metadata_from_filename` (3 parts = annual, 4 parts = with quarter). Blob metadata tags are **not** used: the file name is the source of truth, so hand-copied files work. A name that does not match the convention returns an error for that record, so a page without metadata never reaches the index. The Function's file-name outputs are stored in the enrichment tree as `parsed_company_name`, `parsed_doc_type`, `parsed_fiscal_year` and `parsed_fiscal_quarter`: the blob indexer exposes the blob metadata that the Admin upload writes under the plain names, and a skill output with the same name fails with "Enrichment target name 'company_name' collides with existing '/document/company_name'".
  2. **`embed` skill**, run per page (context `/document/pages/*`, `batchSize` about 20, `degreeOfParallelism` modest). Input: the page text. Output: `vector` (3072 floats, the same Gemini model and settings as `get_embeddings()` use for documents). The model is called through the Gemini API (it is not offered in Foundry; decision D2), with the same defaults as `get_embeddings()` and the document-side task type; the key is a Function app setting.
- **Page splitting must give markdown pages** (the BM25 re-rank reads the headings). Decision D1: Azure Document Intelligence (layout model, markdown output), split on its page-break markers. The Function calls it with its managed identity (role **Cognitive Services User** on the resource); Step 7 checks the markers on real output.
- **Skillset `indexProjections`:** one selector, `targetIndexName` the index, `parentKeyFieldName` `parent_id`, `sourceContext` `/document/pages/*`, `projectionMode` `skipIndexingParentDocuments` (otherwise the index also gets one empty parent document per PDF). Every child field is mapped explicitly: `page_text` ← `/document/pages/*/text`, `page` ← `/document/pages/*/page`, `vector` ← `/document/pages/*/vector`, and `company_name`, `doc_type`, `fiscal_year`, `fiscal_quarter`, `source_file` ← the matching `/document/...` values. **No mapping for `id` or `parent_id`**: Microsoft documents that mapping the parent key breaks change tracking.
- **Updates and deletes (documented behaviour):** a changed PDF replaces its pages on the next run (the projected keys carry a hash that changes with the parent); a deleted PDF removes its pages **only** with a `dataDeletionDetectionPolicy` on the data source (decision D5). Both confirmed at Step 11: a re-uploaded PDF replaced its 9 pages (count unchanged), and deleting a throwaway PDF removed its 9 pages at the next run.
- **Auth to the Function:** function key for the trial; managed identity (`authResourceId`) later.
- **Skills are deployed as code in the repo** (`azure_functions/rag_skills/`, own `requirements.txt`, unit tests for file-name parsing and page splitting). A test asserts that the Function's file-name parser and `extract_metadata_from_filename` return the same result for the same list of names.

### 4.4 Definitions live in the repo

Index, data source, skillset and indexer are kept as JSON files in `azure/search/`, so the full-ingestion service is created by applying the same files (portal "Add ... (JSON)" or REST) rather than by clicking again. Appendix A is the verified index definition.

## 5. Steps (each leaves the app working; the default backend stays `chroma` until Step 13)

**Part A: retrieval on Azure**

1. [x] **Verify the Azure side** (done 2026-11-11). In the portal: index JSON shows `"stored": false`, cosine and 3072 for `vector`; the search service's identity has **Storage Blob Data Reader** on `stadaptiverag32`; the service's API access control (Keys blade) allows roles, or an API key is available; your account has **Search Index Data Contributor** (for seeding) and **Search Index Data Reader**. Save the index JSON into `azure/search/index.json`.
2. [x] **Config and package** (done 2026-11-12; `azure-search-documents` 12.1.0, 226 tests pass, an invalid `RETRIEVAL_BACKEND` fails at startup): add the settings of 4.1 to `config.py` and `.env.example` (real values in `backend/.env`); add `azure-search-documents` to `requirements/base.txt`.
3. [x] **Retrieval module and unit tests** (done 2026-11-14; 25 new tests, 251 pass in total): `azure_search_store.py` plus tests with a fake search client: filter building (quote escaping, int year, empty), keyword cleaning (phrases, special characters, empty), `hybrid` and `hard` request shapes, result mapping (None dropped), backend switch. The existing Chroma tests stay untouched.
4. [x] **Seed and compare** (done 2026-11-14; 213 pages seeded; Chroma's final pages were always among Azure's candidates and `hard` matched Chroma at least as well as `hybrid` in every case, so D3 `hard` stands): `scripts/seed_search_from_chroma.py` copies the 213 rows of the 3 test PDFs into the trial index; `scripts/compare_retrieval_backends.py` (read-only) runs 8 questions, each pinned to one of the 3 PDFs, on both backends; `hard` is the default (D3) and `hybrid` is run for comparison.
5. [x] **Switch and chat check** (done 2026-11-14; 3 questions answered from Azure with page references; `RETRIEVAL_BACKEND=chroma` brought the Chroma answer back; two existing bugs found and fixed on the way, see the progress log): wire `RETRIEVAL_BACKEND` in `retriever_docs`; ask the same questions in the chat with `azure`, then switch back to `chroma` to prove the revert works.
6. [x] **Remove the seeded rows** (done 2026-11-14; 213 deleted, `$count` of the index is 0; `RETRIEVAL_BACKEND` stays `chroma` while the index is empty) (`parent_id eq 'seed'`); the index is empty again.

**Part B: ingestion**

7. [x] **Document Intelligence spike (decision D1)** (done 2026-11-15; go criteria met, see the progress log): create the Document Intelligence resource (S0), run the layout model with markdown output on the 3 PDFs and compare with the Chroma pages. Go criteria: one page-break marker per page (check whether one also appears before page 1), page count within 2 of Chroma (93, 9, 111), headings present as markdown, under about 150 s for the largest file. If the markers are missing, split with the page spans of the structured result instead.
8. [x] **Function app** (done 2026-11-16; deployed as `func-adaptiverag-skills`, Python 3.11, Flex Consumption, East US 2; all calls answered, see the progress log): `pdf-pages` and `embed` skills with unit tests (`pdf-pages` drops pages that have no text once the comment tags are removed but keeps the physical page number of the others; it splits a long file into page ranges, analysed in parallel, so that one skill call stays well under the 230 s limit); deploy; call each with a sample request (curl) and check the response shape (`recordId`, `data`, `errors`, `warnings`).
9. [x] **Skillset** (done 2026-11-17; debug sessions on Apple and Amazon, see the progress log): `azure/search/skillset.json` (skills `pdf-pages` and `embed-pages`, the index projection) and `azure/search/indexer.json`; the files hold the placeholder `<FUNCTION_KEY>` and the real key is pasted only in the portal, never committed. The skills get the blob URL (`metadata_storage_path`) and the Function reads the blob with its own identity, so `allowSkillsetToReadFileData` is not used. A portal debug session needs an existing indexer, so the indexer is created now but **disabled** (an enabled one would run on creation). Test one blob in the debug session (Apple for the values, Amazon for the null quarter and the skipped page) and confirm the enrichment tree values, the mappings, and that the index stays empty.
10. [x] **Indexer on the 3 PDFs** (done 2026-11-17; 213 page documents, all checks pass, see the progress log): enable the indexer created at Step 9, run it on demand; check the document count per PDF against Chroma, metadata and nulls, a vector query returns pages, and run the Step 4 question set against the real index; compare with the Step 4 results.
11. [x] **Change tracking** (done 2026-11-17; see the progress log): run again with no change (nothing processed); re-upload one PDF from the Admin page (only its pages are replaced, count unchanged); delete a throwaway blob and confirm what happens to its pages under the deletion policy chosen in D5.

**Part C: cutover, more PDFs, cleanup** (Step 12 was moved to the end on 2026-11-17, so the order is 13, 14, 15, then 12)

13. [x] **Cutover on the Free-tier index** (done 2026-11-18; see the progress log; Chroma stays untouched as the fallback; the code default of `retrieval_backend` stays `chroma` so tests and a fresh clone need no Azure, and `azure` is set in `backend/.env`): (a) in `backend/.env` set `AZURE_SEARCH_ENDPOINT` (the Free service) and `RETRIEVAL_BACKEND=azure`, and change the example in `backend/.env.example`; restart the backend; (b) acceptance in the chat: questions on each of the 3 PDFs show `[BACKEND] azure` in the backend log and answers with page references, and a question about a company not in the index gets the fixed "couldn't find" reply; the full test suite still passes; (c) revert check: `RETRIEVAL_BACKEND=chroma` and a restart bring the Chroma answers back; (d) schedule the indexer (tested with `PT15M`, left at `PT24H` in the portal's indexer JSON and in `azure/search/indexer.json`), upload one more PDF from the Admin page (a real filing from the local folder) and check that it becomes searchable with no manual run, and that the index stays within the Free limits; (e) update the Admin upload docstring: the route only stores the file, and the PDF is searchable after the next indexer run.
14. [ ] **Retire, after your approval (deferred 2026-11-17: Chroma stays for now, and this waits until the Azure index holds all the PDFs you need):** `ingestion.py`, `docling`, `docling-core`, `docling-parse`, `langchain-chroma` and the Chroma settings and folder (`backend/storage/chroma_financial_db`, and keep it out of the repo), the Chroma `search_docs` and `build_search_kwargs` if you no longer want the fallback; `rag_data_dir` and the local `data/rag_data` (Sub-Plan 3 Step 6b); the "not yet searchable" note on the Admin page changes to "searchable after the next indexer run".
15. [x] **Docs** (done 2026-11-18): `README.md` (architecture note, prerequisites, the Azure AI Search section with the settings, Admin upload text, further reading) and `PROJECT_SUMMARY.md` (sections 1-4, 8, 9, 13, 14 and 16); older plan files stay intact.
12. [ ] **Full-ingestion service (moved to the end 2026-11-17; optional, only if the Free service is outgrown, see D8):** create the new AI Search service (tier by decision D8); apply the JSON definitions; give it the role on the storage account; you copy the remaining 17 PDFs to blob (Sub-Plan 3 Step 6b), and `scripts/verify_rag_data_blob.py --require-all` confirms 20 identical; run the indexer; compare page counts with Chroma (known differences: the one Google PDF missing from Chroma, and the 150-page Amazon 10-Q).

## 6. Behaviour changes to be aware of

- Retrieval results will not be identical to Chroma's: no MMR, token matching instead of substring matching for keywords, different scores. The comparison in Steps 4 and 10 measures how far they differ.
- `file_hash` disappears from the tool output.
- After cutover an uploaded PDF becomes searchable only after an indexer run. The indexer runs on a schedule (`PT24H`; the Free tier minimum interval is 5 minutes), so an upload is searchable at the next daily run at the latest; run the indexer from the portal for an immediate result.
- While the index holds only the PDFs loaded so far, a question about any other company, year or quarter finds nothing in `azure` mode (Chroma holds 19 files). `RETRIEVAL_BACKEND=chroma` brings them back.
- Open, decide later: duplicate-content protection. Chroma skipped a file whose SHA-256 (`file_hash`) matched an earlier one; the indexer cannot do that (it keys on the blob path and detects change by last-modified time). Options: an upload check in the Admin page using the blob MD5, an index field `file_hash` mapped from `metadata_storage_content_md5` with a report script, or both.
- Page text comes from Document Intelligence, not docling, so page boundaries and headings can differ slightly from the Chroma pages. Known case: `amazon 10-k 2023.pdf` has 94 pages; the local ingestion dropped the empty page 79, so Chroma's page numbers from 80 onwards are one lower than the PDF's real page numbers. The Azure index uses the real page numbers.
- A question for which no documents are retrieved now gets a fixed "couldn't find relevant passages" reply instead of an answer the LLM writes from memory (both backends), and the rewrite loop stops after 3 rewrites (4 searches in total). Since Step 13 the relevance grading is skipped when nothing was retrieved.
- Known, deferred: a question that matches nothing in the index (for example a filing that has not been uploaded) still runs the whole rewrite loop and takes about 3.5 minutes (measured 2026-11-18: 10 searches and 23 model calls; every `retriever_docs` call makes 2 model calls for the filters and the ranking keywords, the rewrite step 1; the Azure search itself takes about 0.4 s). Ideas for later: extract the filters once per question instead of once per rewritten query, stop at once when the filtered company, document type and year have no file in the index, run the queries of one pass in parallel.
- The app has no delete function. A PDF is removed by deleting its blob (portal or `az storage blob delete`); its pages leave the index at the next indexer run, which has to happen within the soft delete retention (7 days). Without that run the pages stay searchable.
- A PDF uploaded through the Admin page carries blob metadata (`company_name`, `doc_type`, `fiscal_year`, `fiscal_quarter`); the pipeline ignores it and takes everything from the file name.
- Running the app locally needs an active `az login` with a role on the search service (or an API key).

## 7. Decisions (confirmed 2026-11-11)

| # | Decision | Outcome |
|---|---|---|
| D1 | How PDFs become page-level markdown | **Azure Document Intelligence**, layout model, markdown output, split on its `<!-- PageBreak -->` markers. Needs a paid (S0) resource. The marker is documented only in a support answer, so Step 7 verifies it on real output. docling, the built-in Document Layout skill and a lightweight PDF library are not pursued |
| D2 | Embedding model | **Keep Gemini `gemini-embedding-001` (3072), called through the Gemini API** from the `embed` skill, because the model is not offered in Foundry. Same defaults as `get_embeddings()` (document-side task type for pages); query side unchanged, so Chroma stays a valid fallback. Key as a Function app setting (Key Vault later) |
| D3 | Keyword mode | **`hard`** (`search.ismatch` in the filter). `hybrid` remains a setting value for comparison |
| D4 | Skill hosting and auth | Azure Functions (Python, **Flex Consumption**: the Linux Consumption plan is retired); function key for the trial, managed identity later |
| D5 | Deletion tracking | Native blob soft delete tracking, switched on in the trial at Step 11 (the storage account has soft delete for 7 days and versioning off) and used again on the full service; confirmed that it removes the projected pages |
| D6 | Indexer schedule | On demand through Step 12. Changed 2026-11-17: a schedule on the Free service at Step 13: tested every 15 minutes, left at every 24 hours (`PT24H`; a run with no changes processes 0 documents), so an uploaded PDF becomes searchable at the next daily run at the latest, or at once when the indexer is run from the portal |
| D7 | `fiscal_year` type | Settled: Int32 in the index |
| D8 | Tier of the full-ingestion service | Deferred 2026-11-17 (Step 12 moved to the end). Stay on Free and add PDFs as needed. Measured: 213 pages = 3.31 MB storage and 8.16 MB vector quota, one run about 1 minute. The 20 PDFs (about 1,270 pages) would be about 20 MB storage against the 50 MB Free limit; the docs give no Free vector quota and a 3-minute indexer limit may need several runs. Re-decide when the Free service holds what it can or a limit is hit |
| D9 | Blob metadata tags (Sub-Plan 3 decision 4) | Not used for the index; the file name is the source of truth. The admin route may keep writing them |
| D10 | Search auth from the app | Role-based, `DefaultAzureCredential`: **Search Index Data Reader** for the app, **Search Index Data Contributor** for you while seeding. No API key |

## 8. Things to verify in the real service (all confirmed on 2026-11-17)

- `file_data` from the indexer (`allowSkillsetToReadFileData`) is not used: the skills receive the blob URL and the Function reads the blob with its own identity (the Function also accepts a base64 file, tested). The custom-skill page states no payload limits, so the URL form avoids that question.
- `dataToExtract: storageMetadata` still provides `metadata_storage_name` and `metadata_storage_path` to the skills (Steps 9 and 10).
- The integer values from the skill (`fiscal_year`, `page`) are accepted by the `Edm.Int32` index fields (Step 10: no type error, `verify_search_index.py` reads them back as the expected integers).
- A null `fiscal_quarter` flows through the projection into the index without an error (the Amazon 10-K pages carry no quarter).
- The Free tier run limits: one run over the three PDFs (213 pages, skillset with Document Intelligence and Gemini calls) took about 1 minute.
- Role-based access works on the Free service (the app's search calls and `verify_search_index.py` use `az login`).

## 9. Progress log

- 2026-11-18: Step 15 complete. `README.md` and `PROJECT_SUMMARY.md` updated for Sub-Plan 4 (see the Step 15 line). Open: Step 14 (after your approval), Step 12 (optional), duplicate-content protection, the slow no-result question.
- 2026-11-18: Step 13 complete. (a) `backend/.env` set to `RETRIEVAL_BACKEND=azure` with the Free service endpoint; `.env.example` updated; the code default in `config.py` stays `chroma`. (b) Three chat questions, one on each PDF, were answered from Azure with page references (log shows `[BACKEND] azure`). The fourth question (Apple 2024 10-K, a filing that is not in the index) first timed out in the UI: every pass through the loop graded the "No ducuments found" text with the model, so 7 of the 23 model calls were wasted and the total went past the 120 s chat timeout. Fixed: `NO_DOCUMENTS_PREFIX` in `doc_tools.py`, `retrieve_node` returns an empty result when no query found anything, `grade_documents_node` skips the model call for an empty result, and the chat timeout in `frontend/api_client.py` is 300 s. One slip on the way (the final `return retrieved_text` of `retriever_docs` was replaced by mistake) was caught by `test_output_is_identical_for_both_backends` and corrected. After the fix: 292 tests pass; the same question gave the fixed not-found reply after 3 rewrites, in about 3.6 minutes, with no grading calls. (c) `RETRIEVAL_BACKEND=chroma` gave Chroma answers again; set back to `azure`. (d) Indexer schedule tested at `PT15M`: `apple 10-k 2023.pdf` uploaded through the Admin page was indexed by a scheduled run without a manual run (80 pages, numbered 1 to 80, equal to Chroma's 80; index 293 documents; no verify problems except the unexpected-file flag until `EXPECTED_PAGES` got the file); the schedule was then set to `PT24H` in the portal and in `azure/search/indexer.json`. (e) Admin upload docstring updated.
- 2026-11-17: plan reordered after the Step 11 results. The Free-tier index (3 PDFs, 213 pages) is accepted as the working Azure RAG, so Step 12 (new paid service, all 20 PDFs) moves to the end as optional and the cutover (Step 13) comes next; Chroma stays in place and Step 14 (retire) waits. More PDFs are added to the Free service as needed (Free limits: 50 MB storage, 3 indexers; no vector quota stated in the docs). D6 and D8 changed (section 7). Duplicate-content protection is open (section 6). The repo `azure/search/indexer.json` is enabled (`"disabled": false`).
- 2026-11-11: decisions D1 to D10 confirmed (section 7); index JSON re-checked from the portal (`stored: false`, cosine, 3072, all attributes as intended). Step 1 started.
- 2026-11-17: Step 11 complete. (1) Storage account: blob soft delete 7 days, versioning off. (2) Run with no change: Success, 0 documents processed. (3) Re-upload of `apple 8-k q4 2023.pdf` through the Admin page: the run failed with "Enrichment target name 'company_name' collides with existing '/document/company_name'", because the Admin upload writes blob metadata with the same names as the skill outputs (the 3 hand-copied PDFs had none, so Step 10 had not shown it). Fixed in `skillset.json` by naming the skill outputs `parsed_company_name`, `parsed_doc_type`, `parsed_fiscal_year`, `parsed_fiscal_quarter` and pointing the projection mappings at them; skillset updated in the portal (key pasted there). One run then failed once with an instance-metadata token error and the next run succeeded without any change, so it looks temporary. After the fix the re-upload run processed 1 document and the index stayed at 213 (Apple 9 pages). (4) Data source: native blob soft delete tracking switched on; definition saved as `azure/search/datasource.json`. (5) Throwaway `apple 8-k q1 2050.pdf` (9 pages) uploaded through the Admin page: 1 document processed, 222 documents; blob deleted; next run: 1 document processed, back to 213. Part B is finished.
- 2026-11-17: Step 10 complete. The indexer `ixr-adaptiverag-rag` was enabled and run once: Success, 3 of 3 documents, 0 errors, about 1 minute. `scripts/verify_search_index.py`: 213 documents (Amazon 93 pages 1 to 94 with the empty page 79 missing, Apple 9, Google 111), metadata equal to the file names (null quarter for the 10-Ks), unique ids, a parent_id on every page, no empty text, no parent-only documents; three sample questions through `search_docs_azure` returned sensible pages (Amazon net sales by product and results of operations, Apple cash flow statement, Google "How We Make Money"). `scripts/compare_retrieval_backends.py` against the real index, `hard` mode, final 5 pages shared with Chroma: 3/5, 4/5, 4/5, 3/5, 2/5, 2/5, 3/3, 0/0 (Step 4 with Chroma's own pages: 5/5, 4/5, 5/5, 5/5, 4/5, 2/5, 3/3, 0/0). Lower overlap is expected because the page text now comes from Document Intelligence and Chroma's Amazon pages after 79 are numbered one lower; Chroma's final pages were inside Azure's candidates in every case (5/5, 3/3). `hybrid` was equal or one page apart in either direction, so D3 `hard` stands. 292 tests pass.
- 2026-11-17: Step 9 complete. Skillset `ss-adaptiverag-rag` and the disabled indexer `ixr-adaptiverag-rag` created in the portal from `azure/search/skillset.json` and `indexer.json` (function key pasted in the portal only). A disabled indexer works as the debug-session template. The session state is kept in `stfuncadaptiverag32` (search service identity with Storage Blob Data Contributor). Debug session on `apple 8-k q4 2023.pdf`: company `apple`, type `8-k`, year 2023, quarter `q4`, 9 pages; `embed-pages` ran 9 iterations with no errors or warnings, and page 1 holds `page` 1, the markdown text and a 3072-number `vector`; the graph connects the projection mappings to the search index. Debug session on `amazon 10-k 2023.pdf`: quarter `null` (no error), 93 pages, one warning "Pages with no text were skipped: 79", 0 errors. The index was not written to. Still unchecked until Step 10: that the integer fields (`fiscal_year`, `page`) are accepted by the `Edm.Int32` index fields.
- 2026-11-16: Step 8 complete. Deployed with `func azure functionapp publish` (remote build, Python 3.11; the local Python is 3.13, which only produces a warning). Calls to the deployed app with a function key: `embed` returned two 3072-float vectors and an error for the empty text; `pdf-pages` from blob URLs returned Apple 9 pages in 12 s, Amazon 93 pages (page 79 skipped, warning) in 21 s, Google 111 pages in 14 s; `pdf-pages` with the file as base64 (`file_data` form) returned Apple 9 pages. A first `embed` call failed with an invalid Gemini key because the placeholder had been stored; fixed by setting the real key. The default host key was shown in the console during testing and is to be regenerated before the skillset uses it (Step 9). Note: `--code` values that start with `-` must be passed as `--code=<key>`.
- 2026-11-15: Step 8 code written and unit-tested (50 tests in `azure_functions/rag_skills/tests`, plus 25 parity cases in `backend/tests/unit/test_rag_skills_filename_parity.py` against `parse_filename`). Hosting changed from "Consumption" to Flex Consumption because the Linux Consumption plan is retired. Local runs from PowerShell against the real Document Intelligence resource: Amazon 10-K 93 pages in 28 to 41 s (page 79 skipped), Google 10-K 111 pages in 26 s, Apple 8-K 9 pages, `embed` 3072-float vectors; page text identical to the spike apart from heading depth (ranges number heading levels relative to the range; the re-rank regex `^#+\s+` ignores depth). Earlier slow timings (100 to 216 s) came from running the same commands in Git Bash and are not representative. Deployment and live tests follow.
- 2026-11-15: Step 7 complete (`scripts/spike_di_layout.py` on the 3 PDFs, layout model, markdown output, api-version 2024-11-30). A `<!-- PageBreak -->` marker sits between pages and none before page 1, so the split gives one piece per page (markers = pages - 1). Pages: Apple 9 (Chroma 9), Google 111 (111), Amazon 94 (Chroma 93). First headings on the same page as Chroma: Apple 8/8, Google 96/96, Amazon 69/78; the 9 differences are all after page 79, which is an empty page in the PDF (only a page-number tag) that the local ingestion dropped, shifting Chroma's later pages by one. Time: Apple 10 to 19 s, Amazon 121 s, Google 125 s (about 1.1 s per page, so a file of about 200 pages would pass the 230 s skill limit; Step 8 splits long files into page ranges). Page records with no text must be skipped before embedding. Next: Step 8.
- 2026-11-15: Document Intelligence resource `di-adaptiverag` created in East US 2, chosen so that the paid AI Search service and the Function app can sit in the same region. The Free search service stays in Central US for the trial; calls between regions work. Step 7 spike script prepared.
- 2026-11-14: Step 6 complete: the 213 seeded documents were removed; the trial index is empty and `RETRIEVAL_BACKEND=chroma`. Part A is finished. Next: create the Document Intelligence resource (S0), then Step 7.
- 2026-11-14: Step 5 complete: `retriever_docs` picks the backend from `RETRIEVAL_BACKEND` (5 tests). In the chat with `azure`, the 3 test questions were answered with page references; with `chroma` the Amazon Q2 2024 question was answered from Chroma. Side fixes, both existing bugs that show up on any question with no documents, whatever the backend: (1) `rewritting_count` was missing from `AgentState`, so the retry limit never counted and a query with no documents rewrote forever; fixed by declaring the field and resetting `rewritting_count` and `rewritten_queries` at the start of every question in `chat_service.py` (plus `recursion_limit` 50 as a safety net); (2) with no documents `generate_node` let the LLM answer from memory and cite invented pages; it now returns a fixed not-found reply without calling the LLM. 262 tests pass.
- 2026-11-14: Step 4 complete: 213 pages (93 + 9 + 111) seeded into the trial index with `parent_id = seed`; 8-question comparison run. Final 5 pages shared with Chroma: 5/5, 4/5, 5/5, 5/5, 4/5, 2/5, 3/3, 0/0 in `hard` mode (hybrid equal or lower). Azure returns more keyword candidates than Chroma (token matching instead of case-sensitive substring), and returned pages for a question where Chroma found none. The seeded rows stay until Step 6.
- 2026-11-14: Step 3 complete: `azure_search_store.py` and `tests/unit/test_azure_search_store.py` added; 25 new tests, 251 pass in total.
- 2026-11-12: Step 2 complete: settings added to `config.py`, `.env.example` and `.env`; `azure-search-documents` 12.1.0 installed and added to `requirements/base.txt`.
- 2026-11-11: Step 1 complete. Search service on the Free tier allows Entra ID or API key (`aadOrApiKey`); its identity has Storage Blob Data Reader on `stadaptiverag32`; the signed-in account has Search Index Data Contributor; a role-based `$count` on the index returned 0. D2 confirmed: Gemini embeddings through the Gemini API.
- 2026-11-10: Chroma fields listed and confirmed from the database. Trial index `idx-adaptiverag-trial` re-created with explicit attributes (an earlier version had every attribute on by default) and checked against the schema.

## Appendix A. Index definition (verified 2026-11-10)

Every attribute is explicit: omitted attributes default to true on simple fields, which would make every field searchable, filterable, sortable and facetable.

```json
{
  "name": "idx-adaptiverag-trial",
  "fields": [
    {"name": "id", "type": "Edm.String", "key": true, "retrievable": true,
     "searchable": true, "analyzer": "keyword",
     "filterable": true, "sortable": false, "facetable": false},
    {"name": "parent_id", "type": "Edm.String", "retrievable": true,
     "searchable": false, "filterable": true, "sortable": false, "facetable": false},
    {"name": "company_name", "type": "Edm.String", "retrievable": true,
     "searchable": false, "filterable": true, "sortable": false, "facetable": false},
    {"name": "doc_type", "type": "Edm.String", "retrievable": true,
     "searchable": false, "filterable": true, "sortable": false, "facetable": false},
    {"name": "fiscal_year", "type": "Edm.Int32", "retrievable": true,
     "filterable": true, "sortable": false, "facetable": false},
    {"name": "fiscal_quarter", "type": "Edm.String", "retrievable": true,
     "searchable": false, "filterable": true, "sortable": false, "facetable": false},
    {"name": "page", "type": "Edm.Int32", "retrievable": true,
     "filterable": false, "sortable": false, "facetable": false},
    {"name": "source_file", "type": "Edm.String", "retrievable": true,
     "searchable": false, "filterable": false, "sortable": false, "facetable": false},
    {"name": "page_text", "type": "Edm.String", "retrievable": true,
     "searchable": true, "analyzer": "en.lucene",
     "filterable": false, "sortable": false, "facetable": false},
    {"name": "vector", "type": "Collection(Edm.Single)", "searchable": true,
     "retrievable": false, "stored": false, "dimensions": 3072,
     "vectorSearchProfile": "vector-profile"}
  ],
  "vectorSearch": {
    "algorithms": [
      {"name": "vector-config-default", "kind": "hnsw",
       "hnswParameters": {"m": 4, "efConstruction": 400, "efSearch": 500, "metric": "cosine"}}
    ],
    "profiles": [{"name": "vector-profile", "algorithm": "vector-config-default"}]
  }
}
```

Notes: attributes of an existing field cannot be changed, only fields added, so a change means deleting and re-creating the index. `page_text` is not filterable or sortable on purpose: pages reach 21k characters and filterable or sortable terms have a size limit of about 32 KB.
