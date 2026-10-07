"""
Ports the ingestion pipeline from
NotebookImplementation/Page RAG - Data_Ingestion.ipynb into the backend, so
admin-uploaded PDFs (point #10) get embedded into the same Chroma index the
RAG retrieval path already queries.

Two deliberate deviations from the notebook:
  - Reuses the shared `vector_store` from agent/retrieval/vectorstore.py instead
    of building a second, separate Chroma client — there should only ever be
    one connection to the same persisted Chroma directory in a running process.
  - The "already processed" file-hash set is recomputed fresh on every call to
    ingest_docs_in_vectordb, not built once as a module-level global — the
    notebook's version would go stale after the very first ingestion in a
    long-running process (or under concurrent admin uploads).
"""

import hashlib
from pathlib import Path

from docling.document_converter import DocumentConverter
from langchain_core.documents import Document

from app.agent.retrieval.vectorstore import vector_store

PAGE_BREAK_PLACEHOLDER = "<!-- page break -->"


def extract_metadata_from_filename(filename: str) -> dict:
    """
    Extract metadata from filename.

    Expected format: {company} {doc_type} {quarter} {year}.pdf
    Examples:
    - amazon 10-k 2024.pdf
    - amazon 10-q q1 2024.pdf

    Returns:
        dict with company_name, doc_type, fiscal_year, fiscal_quarter
    """
    name = filename.replace('.pdf', '')
    parts = name.split()

    metadata = {}
    if len(parts) == 4:
        metadata['fiscal_quarter'] = parts[2]
        metadata['fiscal_year'] = int(parts[3])
    else:
        metadata['fiscal_quarter'] = None
        metadata['fiscal_year'] = int(parts[2])

    metadata['company_name'] = parts[0]
    metadata['doc_type'] = parts[1]

    return metadata


def extract_pdf_pages(pdf_path) -> list[str]:
    """Converts a PDF to markdown and splits it into one string per page."""
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown_text = result.document.export_to_markdown(page_break_placeholder=PAGE_BREAK_PLACEHOLDER)
    return markdown_text.split(PAGE_BREAK_PLACEHOLDER)


def compute_file_hash(file_path) -> str:
    """Computes the SHA-256 hash of a file's content, used to detect already-ingested PDFs."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def _get_processed_hashes() -> set[str]:
    """
    Queries the CURRENT state of the Chroma index for every file_hash already
    ingested. Computed fresh on every call — see the module docstring for why
    this can't be a cached module-level global like the notebook's version.
    """
    existing_docs = vector_store.get(where={"file_hash": {"$ne": ""}}, include=["metadatas"])
    return {m.get("file_hash") for m in existing_docs["metadatas"] if m.get("file_hash")}


def ingest_docs_in_vectordb(pdf_path) -> dict:
    """
    Ingests a single PDF into the shared Chroma vector store, one Document per
    page, tagged with metadata parsed from the filename. Safe to call on a
    file that's already been ingested — it's skipped based on content hash,
    not filename, so even a renamed duplicate is caught.

    Returns a small summary dict: {"skipped": bool, "pages_ingested": int}.
    """
    pdf_path = Path(pdf_path)
    print(f"Processing: {pdf_path.name}")

    file_hash = compute_file_hash(pdf_path)
    if file_hash in _get_processed_hashes():
        print(f"[SKIP] already processed: {pdf_path}")
        return {"skipped": True, "pages_ingested": 0}

    pages = extract_pdf_pages(pdf_path)
    file_metadata = extract_metadata_from_filename(pdf_path.name)

    processed_pages = []
    for page_num, page_text in enumerate(pages, start=1):
        metadata_dict = file_metadata.copy()
        metadata_dict["page"] = page_num
        metadata_dict["file_hash"] = file_hash
        metadata_dict["source_file"] = pdf_path.name

        processed_pages.append(Document(page_content=page_text, metadata=metadata_dict))

    vector_store.add_documents(documents=processed_pages)

    return {"skipped": False, "pages_ingested": len(processed_pages)}