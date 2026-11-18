# rag_skills

Two custom skills for the Azure AI Search indexer (Python Azure Functions app, Flex Consumption plan).

| Route | Input per record | Output per record |
|---|---|---|
| `POST /api/pdf-pages` | `file_name` (bare name, e.g. `amazon 10-k 2023.pdf`) and either `file_data` (the file, base64) or `url` (blob URL) | `company_name`, `doc_type`, `fiscal_year`, `fiscal_quarter` (null for annual reports), `source_file`, `pages` = `[{page, text}]` |
| `POST /api/embed` | `text` | `vector` (3072 floats, `gemini-embedding-001`, task type RETRIEVAL_DOCUMENT) |
| `GET /api/health` | none (anonymous) | `{"status": "ok"}` |

`pdf-pages` runs the Document Intelligence layout model (markdown output), splits the markdown on its
`<!-- PageBreak -->` markers, removes the page header, footer and page-number comments, and drops pages with
no text. The other pages keep their real page number. A file longer than `DI_CHUNK_PAGES` pages is analysed in
page ranges in parallel, so one call stays well under the 230 s limit of a custom skill.

The file-name rules in `skills/filename.py` are a copy of `parse_filename` in
`backend/app/storage/rag_data_store.py`; `backend/tests/unit/test_rag_skills_filename_parity.py` keeps them equal.

## Application settings

| Setting | Meaning |
|---|---|
| `DOCUMENT_INTELLIGENCE_ENDPOINT` | `https://di-adaptiverag.cognitiveservices.azure.com` |
| `ALLOWED_BLOB_HOST` | host of the storage account; `url` inputs on other hosts are refused |
| `GEMINI_API_KEY` | Gemini API key for the embeddings |
| `EMBEDDING_MODEL` | default `gemini-embedding-001` |
| `DI_CHUNK_PAGES` | default 20 |
| `DI_PARALLELISM` | default 6 |

Access to Document Intelligence and to the blobs uses the app's managed identity locally (`az login`) and in
Azure: role **Cognitive Services User** on the Document Intelligence resource, **Storage Blob Data Reader** on the
storage account (only needed for `url` inputs).

## Local use

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements-dev.txt
    python -m pytest -q
    copy local.settings.json.example local.settings.json     # then fill in GEMINI_API_KEY
    func start
    python tools/call_skills.py embed
    python tools/call_skills.py pages --blob "apple/apple 8-k q4 2023.pdf"
