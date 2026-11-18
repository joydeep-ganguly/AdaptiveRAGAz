"""
Step 7 spike (Sub-Plan 4): does Azure Document Intelligence (layout model, markdown output)
give us the same pages as the Chroma ingestion did?

For each of the 3 test PDFs in Blob it
  * sends the PDF to the layout model with markdown output,
  * splits the markdown on the page-break comment and counts the pages,
  * compares the page count with the structured result and with Chroma (93 / 9 / 111),
  * checks page by page whether the first heading of the Chroma page is on the same page here,
  * saves the markdown and a summary for inspection.
Nothing is written to Blob, Chroma or the search index. The layout model is billed per
page analysed (213 pages for all three PDFs).

    python scripts/spike_di_layout.py --only apple      # cheapest first: 9 pages
    python scripts/spike_di_layout.py                   # all three
    python scripts/spike_di_layout.py --no-chroma       # skip the Chroma comparison (backend may run)

Needs `az login` with the role "Cognitive Services User" on the Document Intelligence resource,
"Storage Blob Data Reader" or better on the storage account, and backend/.env. Stop the backend
for the Chroma comparison. Output: backend/storage/debug_logs/di_spike/ (git-ignored).
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_ENDPOINT = "https://di-adaptiverag.cognitiveservices.azure.com/"
PAGE_BREAK = re.compile(r"<!--\s*PageBreak\s*-->")
COMMENT = re.compile(r"<!--\s*([A-Za-z]+)")
HEADING = re.compile(r"^#+\s+(.+)$", re.M)

# the 3 test PDFs and the page counts Chroma holds for them
TEST_FILES = {
    "amazon 10-k 2023.pdf": ("amazon", 93),
    "apple 8-k q4 2023.pdf": ("apple", 9),
    "google 10-k 2023.pdf": ("google", 111),
}


def split_pages(markdown: str) -> list[str]:
    """Split on the page-break comment. A blank piece before the first marker is dropped; blank pages elsewhere are kept so page numbers stay aligned."""
    pieces = [piece.strip() for piece in PAGE_BREAK.split(markdown)]
    if pieces and pieces[0] == "":
        pieces = pieces[1:]
    return pieces


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def first_heading(text: str) -> str | None:
    match = HEADING.search(text)
    return match.group(1).strip() if match else None


def heading_alignment(chroma_pages: dict[int, str], di_pages: list[str]) -> tuple[int, int]:
    """(pages where Chroma's first heading is found on the same page number here, pages compared)."""
    compared = matched = 0
    for number, text in chroma_pages.items():
        heading = first_heading(text)
        if not heading or not normalize(heading):
            continue
        compared += 1
        if number <= len(di_pages) and normalize(heading) in normalize(di_pages[number - 1]):
            matched += 1
    return matched, compared


def analyze(client, data: bytes):
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentContentFormat

    poller = client.begin_analyze_document(
        "prebuilt-layout",
        AnalyzeDocumentRequest(bytes_source=data),
        output_content_format=DocumentContentFormat.MARKDOWN,
    )
    return poller.result()


def read_chroma_pages(source_file: str) -> dict[int, str]:
    from app.agent.retrieval.vectorstore import vector_store

    data = vector_store.get(where={"source_file": source_file}, include=["documents", "metadatas"])
    return {int(meta["page"]): text for meta, text in zip(data["metadatas"], data["documents"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["amazon", "apple", "google"], help="run one company only")
    parser.add_argument("--no-chroma", action="store_true", help="skip the comparison with Chroma")
    parser.add_argument("--endpoint", default=os.environ.get("DI_ENDPOINT", DEFAULT_ENDPOINT))
    args = parser.parse_args()

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.identity import DefaultAzureCredential

    from app.config import get_settings
    from app.storage import rag_data_store

    out_dir = get_settings().resolved_debug_logs_dir / "di_spike"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = DocumentIntelligenceClient(endpoint=args.endpoint.rstrip("/"), credential=DefaultAzureCredential())
    blobs = {f.name: f for f in rag_data_store.list_files()}
    print(f"Endpoint: {args.endpoint}")

    summary, failures = [], 0
    for name, (company, chroma_count) in TEST_FILES.items():
        if args.only and args.only != company:
            continue
        if name not in blobs:
            print(f"\n{name}: NOT IN BLOB")
            failures += 1
            continue
        data = rag_data_store.download(blobs[name].company_name, name)
        started = time.time()
        result = analyze(client, data)
        seconds = time.time() - started

        markdown = result.content or ""
        pages = split_pages(markdown)
        markers = len(PAGE_BREAK.findall(markdown))
        structured_pages = len(result.pages or [])
        comments = Counter(m.group(1) for m in COMMENT.finditer(markdown))
        blank = sum(1 for p in pages if not p)
        lengths = sorted(len(p) for p in pages) or [0]

        row = {
            "file": name, "seconds": round(seconds, 1), "pdf_pages_structured": structured_pages,
            "markers": markers, "starts_with_marker": bool(PAGE_BREAK.match(markdown.lstrip())),
            "split_pages": len(pages), "chroma_pages": chroma_count, "blank_pages": blank,
            "comment_types": dict(comments), "chars_min_median_max": [lengths[0], lengths[len(lengths) // 2], lengths[-1]],
            "api_version": getattr(result, "api_version", None),
        }
        if not args.no_chroma:
            matched, compared = heading_alignment(read_chroma_pages(name), pages)
            row["heading_match"] = f"{matched}/{compared}"
        summary.append(row)

        (out_dir / f"{Path(name).stem}.md").write_text(markdown, encoding="utf-8")
        print(f"\n{name}  ({seconds:.0f}s)")
        for key, value in row.items():
            if key != "file":
                print(f"  {key}: {value}")
        if len(pages) != chroma_count:
            print(f"  NOTE: {len(pages)} pages after the split, Chroma has {chroma_count}")

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSaved to {out_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
