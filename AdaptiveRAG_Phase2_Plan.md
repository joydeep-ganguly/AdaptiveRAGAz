# AdaptiveRAG — Phase 2 Plan: Admin Roles, Data Separation & Uploads

This is a companion document to `AdaptiveRAG_Implementation_Plan.md`, which is left
completely untouched. It picks up after section 6, point #11, and plans four new
requirements. Same spirit as the original: folder structure first, then a numbered
build order, then the gotchas worth knowing before you start.

---

## 0. Findings from the current code

- `backend/data/` currently mixes two unrelated purposes: `amazon/`, `apple/`, `google/`
  (PDF filings, e.g. `amazon/amazon 10-k 2024.pdf`) sit
  alongside `IMDB-Movie-Data.csv`, `titanic.csv`, `apple_2024.xlsx`, `google_2024.xlsx`
  (the code-execution agent's analysis datasets).
- The RAG side has **no ingestion code in the backend at all yet** — `chroma_financial_db/`
  was copied over pre-built. `Page RAG - Data_Ingestion.ipynb` (in `NotebookImplementation/`)
  has the only existing ingestion logic, and it isn't referenced anywhere under `backend/`.
- `agent/schemas.py`'s `DocType` (`10-k`, `10-q`, `8-k`, `other`) and `FiscalQuarter`
  (`q1`–`q4`) enum values already match exactly what the notebook's
  `extract_metadata_from_filename` produces from a filename like `amazon 10-q q1 2024.pdf`
  — so the two pieces are compatible, just not yet connected.
- `User` currently has no role/type concept at all — just `is_active`.
- `config.py`'s `data_dir` / `resolved_data_dir` is used in exactly two places:
  `agent/tools/code_tools.py` (`upload_file`'s save location) and
  `agent/nodes/code_execution_node.py` (`FilesystemFileSearchMiddleware`'s `root_path`).
  Nothing in the RAG path (`doc_tools.py`, `retrieval/vectorstore.py`) touches it — it
  only ever queries the already-built Chroma index.

---

## 1. Folder structure changes

```
backend/
├── data/
│   ├── rag_data/                  # NEW home for PDFs — admin uploads land here
│   │   ├── amazon/                # moved as-is from data/amazon/
│   │   ├── apple/                 # moved as-is from data/apple/
│   │   └── google/                # moved as-is from data/google/
│   └── code_data/                 # NEW home for the code-execution agent's datasets
│       ├── IMDB-Movie-Data.csv    # moved from data/
│       ├── titanic.csv            # moved from data/
│       ├── apple_2024.xlsx        # moved from data/
│       └── google_2024.xlsx       # moved from data/
│
├── app/
│   ├── agent/
│   │   └── retrieval/
│   │       └── ingestion.py       # NEW — ports Page RAG - Data_Ingestion.ipynb's
│   │                              #   extract_pdf_pages / compute_file_hash /
│   │                              #   ingest_docs_in_vectordb, reusing the shared
│   │                              #   vector_store from vectorstore.py instead of
│   │                              #   building a second Chroma client
│   │
│   ├── api/
│   │   ├── routers/
│   │   │   └── admin.py           # NEW — admin-only: create user (with type),
│   │   │                          #   upload code-data file, upload+ingest rag-data PDF
│   │   └── schemas/
│   │       └── admin.py           # NEW — AdminCreateUserRequest, upload response models
│   │
│   └── db/
│       └── models.py              # CHANGED — User gets a `type` column
│
└── requirements/
    └── base.txt                   # CHANGED — adds docling, docling-core, docling-parse

frontend/
├── pages/
│   └── 3_Admin.py                 # NEW — visible only when logged-in user is admin:
│                                  #   create-user form (with type dropdown) + two
│                                  #   file-uploader widgets
└── components/
    └── auth_forms.py              # CHANGED — login now also stores the user's type
                                   #   in session state (needed to gate the Admin page)
```

The public self-registration form (`render_register_form` in `auth_forms.py`) is
**unchanged** — it still has no type field, matching "activated only when an admin
creates a user."

---

## 2. Requirement 1 — Admin user type

**Design:** two separate creation paths, not one form with a conditionally-hidden field
on the backend side (the frontend can still visually hide/show it):

- `POST /auth/register` (existing, public, unauthenticated) — creates `type="user"`
  always, **except** when it's the very first user ever, which becomes `type="admin"`
  automatically. It never accepts a `type` field from the caller — letting an anonymous
  request choose "admin" would be a privilege-escalation hole.
- `POST /admin/users` (new, admin-only) — the only place a `type` field is ever
  accepted from a request body, and only reachable by someone already authenticated
  as an admin.

**`db/models.py`:** add to `User`:
```python
type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
```
(Plain `String`, not a DB-level enum — matches how `Message.role` is already handled:
validation lives in the Pydantic schema layer, not the database.)

**`db/crud.py`:** add
```python
def count_users(db: Session) -> int:
    return db.query(User).count()
```
and thread a `user_type: str = "user"` parameter through `create_user`.

**`services/auth_service.py`:**
- `register_user` calls `crud.count_users(db)` first; if `0`, force `user_type="admin"`,
  else `"user"`. Signature unchanged otherwise.
- New `create_user_by_admin(db, username, email, password, user_type)` — same
  uniqueness checks as `register_user`, but takes `user_type` directly from the caller
  (the router will have already confirmed the caller is an admin).

**`auth/oauth2.py`:** add
```python
def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    if current_user.type != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
```
Re-export it from `deps.py` alongside the existing dependencies.

**`api/schemas/auth.py`:** `UserResponse` gains `type: str` (it already reads via
`from_attributes`, so this is a one-line addition).

**Migration note — decide before writing code:** `init_db.py`'s `Base.metadata.create_all()`
only creates *new* tables; it won't add a column to the `users` table you already have
from all the testing so far. Two options:
1. **Simplest:** delete `storage/sqlite/app.db` and re-run `python -m app.db.init_db`,
   then `python scripts/seed_admin_user.py` — the reseeded first user becomes admin
   automatically via the logic above. Loses existing test conversations/messages (fine,
   since it's all demo data so far).
2. **Preserves existing data:** manually run
   `ALTER TABLE users ADD COLUMN type VARCHAR(16) NOT NULL DEFAULT 'user'` against the
   existing `app.db`, then manually promote one existing user to `admin` with an
   `UPDATE` statement, since the "first user" auto-admin logic only fires on *new*
   registrations, not retroactively.

---

## 3. Requirement 2 — Data folder separation

**`config.py`:** replace `data_dir` / `resolved_data_dir` with:
```python
rag_data_dir: str = "data/rag_data"
code_data_dir: str = "data/code_data"
```
and matching `resolved_rag_data_dir` / `resolved_code_data_dir` properties. Update
`ensure_storage_dirs()` to create both instead of the old single directory.

**Physical move** (as laid out in section 1): `amazon/`, `apple/`, `google/` →
`data/rag_data/`; the four loose files → `data/code_data/`.

**Code changes, both confirmed as the *only* two consumers:**
- `agent/tools/code_tools.py` — `upload_file`'s `local_file_path` now joins against
  `settings.resolved_code_data_dir`.
- `agent/nodes/code_execution_node.py` — `FilesystemFileSearchMiddleware(root_path=...)`
  now points at `settings.resolved_code_data_dir`.

**`.env.example` and your real `.env`:** replace `DATA_DIR=data` with
```
RAG_DATA_DIR=data/rag_data
CODE_DATA_DIR=data/code_data
```
Note this has to be updated in your actual `.env` too — `.env.example` changes don't
propagate there automatically.

---

## 4. Requirement 3 — Code-data upload (admin-only)

New endpoint in `api/routers/admin.py`:
```
POST /admin/uploads/code-data
```
- Behind `get_current_admin_user`.
- Accepts a `.csv` or `.xlsx` `UploadFile`; reject anything else by extension.
- Sanitize the filename the same way `media.py` already does (`os.path.basename`,
  reject if it changes the input) before writing into `settings.resolved_code_data_dir`.
- Returns a simple `{filename, message}` response.

---

## 5. Requirement 4 — RAG PDF upload + ingestion

### Step 1 — upload

```
POST /admin/uploads/rag-data
```
- Behind `get_current_admin_user`.
- Rather than trusting an admin to type a perfectly-formatted filename (the notebook's
  `extract_metadata_from_filename` parses `"{company} {doc_type} [{quarter}] {year}.pdf"`
  by splitting on spaces — fragile if typed by hand), the request takes **structured
  fields alongside the file**: `company_name`, `doc_type` (constrained to the `DocType`
  enum values), `fiscal_year`, optional `fiscal_quarter` (constrained to `FiscalQuarter`).
  The endpoint constructs the canonical filename itself, e.g.
  `f"{company_name} {doc_type} {fiscal_quarter + ' ' if fiscal_quarter else ''}{fiscal_year}.pdf"`,
  and saves under that name in `settings.resolved_rag_data_dir`. This means
  `extract_metadata_from_filename` can be ported **unmodified** and will always succeed,
  since the backend — not a human — is the one producing the filename it parses.

### Step 2 — ingestion, triggered by the same request

New module `agent/retrieval/ingestion.py`, porting from the notebook:
- `extract_pdf_pages(pdf_path)` — unchanged (docling → per-page markdown split).
- `extract_metadata_from_filename(filename)` — unchanged.
- `compute_file_hash(pdf_path)` — unchanged.
- `ingest_docs_in_vectordb(pdf_path)` — **adapted**, not copied verbatim:
  - Imports the **shared** `vector_store` from `agent/retrieval/vectorstore.py`
    instead of building a second, separate `Chroma(...)` client.
  - Computes the "already processed" hash set **fresh, inside the function call**
    (`vector_store.get(where={"file_hash": {"$ne": ""}}, ...)`) rather than once at
    module import time — the notebook's version builds that set as a one-time global,
    which would go stale the moment a second file is ingested in the same running
    process, or a concurrent upload happens.

The upload endpoint calls `ingest_docs_in_vectordb(saved_path)` on **just the one
newly-saved file** right after saving it — matching "the above step will trigger a
data ingestion pipeline," without rescanning the whole `rag_data/` folder on every
upload (the notebook's original `for pdf_path in pdf_files: ...` loop-over-everything
is what you'd reach for separately if you ever want a "reprocess everything" admin
action, but a single-file call is what a single upload should trigger).

**This endpoint must be `def`, not `async def`** — same rule as `chat.py` (section 4,
bug #4 of the original plan). PDF conversion via docling plus embedding every page is
genuinely slow for a multi-page filing, and this keeps that blocking work in FastAPI's
thread pool instead of freezing the event loop for every other user.

**New dependency:** the notebook pins specific versions in a comment —
```
pip install "docling==2.66.0" "docling-core==2.67.1" "docling-parse==4.7.3"
```
Add these three, pinned, to `requirements/base.txt`. This is a noticeably heavier
install than anything added so far — worth flagging to the team before they next
`pip install`.

**Response:** something like `{filename, pages_ingested, skipped: bool}` so the admin
UI can show "Ingested 42 pages" or "Already processed, skipped."

---

## 6. Frontend changes

- **`api_client.py`** — three new functions: `create_user_as_admin(token, username,
  email, password, user_type)`, `upload_code_data_file(token, file_bytes, filename)`,
  `upload_rag_data_file(token, file_bytes, filename, company_name, doc_type,
  fiscal_year, fiscal_quarter)`. The last two use `httpx`'s multipart `files=` support.
- **`auth_forms.py`** — `render_login_form` additionally stores
  `st.session_state.user_type = user["type"]` after a successful login (needs
  `UserResponse.type` from section 2).
- **`pages/3_Admin.py`** — new page, `st.stop()`s immediately with a message if
  `st.session_state.get("user_type") != "admin"`. Otherwise renders three sections:
  a create-user form (username/email/password + a `type` selectbox — this is the one
  and only place in the UI that field appears), a `st.file_uploader` for code-data
  (csv/xlsx), and a `st.file_uploader` plus the four metadata fields for rag-data PDFs.

---

## 7. Build order (continuing from point #11)

1. Decide the `app.db` migration approach (section 2) before touching any code.
2. `db/models.py` — add `User.type`. `db/crud.py` — add `count_users`, thread
   `user_type` through `create_user`.
3. `services/auth_service.py` — first-user-is-admin logic in `register_user`; add
   `create_user_by_admin`.
4. `auth/oauth2.py` + `deps.py` — add `get_current_admin_user`.
5. `api/schemas/auth.py` — add `type` to `UserResponse`. New `api/schemas/admin.py`.
6. `config.py` — split `data_dir` into `rag_data_dir` / `code_data_dir`; update
   `ensure_storage_dirs`. Update `.env.example` and your real `.env`.
7. Physically move the files/folders per section 1.
8. `agent/tools/code_tools.py` + `agent/nodes/code_execution_node.py` — switch to
   `resolved_code_data_dir`.
9. Add `docling`/`docling-core`/`docling-parse` (pinned) to `requirements/base.txt`.
   New `agent/retrieval/ingestion.py`.
10. New `api/routers/admin.py` (all three endpoints) — register in `main.py`.
11. Frontend: `api_client.py` additions, `auth_forms.py` session-state addition,
    new `pages/3_Admin.py`.
12. Re-run `python -m app.db.init_db` (if you chose the drop-and-recreate migration)
    and `python scripts/seed_admin_user.py` to get your first real admin login.
13. Tests: unit tests for the first-user-admin logic and `get_current_admin_user`;
    integration tests confirming all three `/admin/*` endpoints 403 for a non-admin,
    and a happy-path upload test with `ingest_docs_in_vectordb` mocked out (same
    "never run the real heavy pipeline in tests" rule already applied to the LLM/E2B
    calls elsewhere in the test suite).

---

## 8. Things worth double-checking as you implement

- The public `/auth/register` endpoint must never read or honor a `type` field from
  its request body under any circumstance other than the automatic first-user case —
  that's the actual security boundary this whole feature depends on.
- `DocType`/`FiscalQuarter` enum values are already lowercase-hyphenated (`10-k`, `q1`,
  etc.) — keep the admin upload form's dropdown values consistent with those exact
  strings, or metadata written at ingestion time won't match what `retrieval/filters.py`
  extracts at query time, and retrieval-time filtering will silently miss documents.
- `ingest_docs_in_vectordb`'s dedup check is only as good as the file hash — uploading
  the *same* filing twice under two different constructed filenames (e.g. a typo in
  fiscal_year) would not be caught as a duplicate by hash, only by filename collision
  on disk (which would just overwrite). Not a blocker, just something to be aware of.
