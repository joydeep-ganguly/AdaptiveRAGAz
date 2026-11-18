"""
Check what the indexer put into the Azure AI Search index (Sub-Plan 4, Step 10).

Read-only. For every PDF: the number of page documents against the expected count, the page
numbers (gaps are listed, not an error: a page without text is skipped), the metadata
(company, type, year, quarter) against the file name, unique ids, a parent_id, and page text
that is not empty. Documents without a source_file (parent-only documents) are reported.
Then three sample questions go through the same search function the app uses.

    python scripts/verify_search_index.py
    python scripts/verify_search_index.py --no-search     # skip the sample questions (no Gemini call)

Needs `az login` (role: Search Index Data Reader) and backend/.env.
Exit code 0 = ok, 1 = a check failed.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# source_file -> expected number of page documents (pages with no text are not indexed)
EXPECTED_PAGES = {
    "amazon 10-k 2023.pdf": 93,   # 94 PDF pages; page 79 is empty
    "apple 8-k q4 2023.pdf": 9,
    "google 10-k 2023.pdf": 111,
    "apple 10-k 2023.pdf": 80,
}

SELECT = ["id", "parent_id", "company_name", "doc_type", "fiscal_year", "fiscal_quarter", "page", "source_file", "page_text"]

SAMPLE_QUESTIONS = [
    ("What was Amazon's total net sales in 2023?", {"company_name": "amazon", "doc_type": "10-k", "fiscal_year": 2023}),
    ("Apple's cash flow from operating activities", {"company_name": "apple", "doc_type": "8-k", "fiscal_year": 2023}),
    ("How does Alphabet make money from advertising?", {"company_name": "google", "doc_type": "10-k", "fiscal_year": 2023}),
]


def metadata_from_name(name: str) -> dict:
    """Same rules as the file-name convention (rag_data_store.parse_filename), without importing Azure."""
    parts = name[: -len(".pdf")].split(" ") if name.endswith(".pdf") else []
    if len(parts) not in (3, 4):
        return {}
    return {
        "company_name": parts[0],
        "doc_type": parts[1],
        "fiscal_year": int(parts[-1]) if parts[-1].isdigit() else None,
        "fiscal_quarter": parts[2] if len(parts) == 4 else None,
    }


def analyse(docs: list[dict], expected: dict) -> tuple[list[dict], list[str]]:
    """Returns (one summary row per PDF, list of problems)."""
    problems: list[str] = []
    by_file: dict = defaultdict(list)
    orphans = 0
    for doc in docs:
        if not doc.get("source_file"):
            orphans += 1
        else:
            by_file[doc["source_file"]].append(doc)
    if orphans:
        problems.append(f"{orphans} document(s) without a source_file (parent-only documents)")

    ids = [d.get("id") for d in docs]
    if len(ids) != len(set(ids)):
        problems.append("duplicate document ids")

    rows = []
    for name in sorted(set(by_file) | set(expected)):
        pages_docs = by_file.get(name, [])
        numbers = sorted(d["page"] for d in pages_docs if isinstance(d.get("page"), int))
        row = {"file": name, "count": len(pages_docs), "expected": expected.get(name)}
        row["first"], row["last"] = (numbers[0], numbers[-1]) if numbers else (None, None)
        row["gaps"] = [n for n in range(1, numbers[-1] + 1) if n not in set(numbers)] if numbers else []
        rows.append(row)

        if name not in expected:
            problems.append(f"{name}: not an expected file")
        elif len(pages_docs) != expected[name]:
            problems.append(f"{name}: {len(pages_docs)} page documents, expected {expected[name]}")
        if len(numbers) != len(set(numbers)):
            problems.append(f"{name}: a page number appears more than once")
        if len(numbers) != len(pages_docs):
            problems.append(f"{name}: some documents have no integer page")

        meta_seen = {(d.get("company_name"), d.get("doc_type"), d.get("fiscal_year"), d.get("fiscal_quarter")) for d in pages_docs}
        wanted = metadata_from_name(name)
        want_tuple = (wanted.get("company_name"), wanted.get("doc_type"), wanted.get("fiscal_year"), wanted.get("fiscal_quarter"))
        if pages_docs and meta_seen != {want_tuple}:
            problems.append(f"{name}: metadata {sorted(map(str, meta_seen))} does not match the file name {want_tuple}")
        if pages_docs and len({d.get("parent_id") for d in pages_docs}) != 1:
            problems.append(f"{name}: pages have different or missing parent_id values")
        if any(not d.get("parent_id") for d in pages_docs):
            problems.append(f"{name}: a page has no parent_id")
        empty = [d.get("page") for d in pages_docs if not (d.get("page_text") or "").strip()]
        if empty:
            problems.append(f"{name}: empty page_text on pages {empty[:10]}")
    return rows, problems


def first_heading(text: str) -> str:
    match = re.search(r"^#{1,6}\s+(.+)$", text or "", re.MULTILINE)
    return (match.group(1) if match else (text or "")[:60]).strip()[:70]


def fetch_all(client) -> list[dict]:
    return [dict(hit) for hit in client.search(search_text="*", select=SELECT, top=1000)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-search", action="store_true", help="skip the sample questions")
    args = parser.parse_args()

    from app.agent.retrieval.azure_search_store import get_search_client, search_docs_azure

    client = get_search_client()
    docs = fetch_all(client)
    rows, problems = analyse(docs, EXPECTED_PAGES)

    print(f"{len(docs)} documents in the index\n")
    print(f"{'file':<26}{'docs':>6}{'expected':>10}{'pages':>10}  gaps")
    for r in rows:
        span = f"{r['first']}-{r['last']}" if r["first"] else "-"
        print(f"{r['file']:<26}{r['count']:>6}{str(r['expected']):>10}{span:>10}  {r['gaps']}")

    if not args.no_search:
        print("\nSample questions through search_docs_azure (k=3):")
        for question, filters in SAMPLE_QUESTIONS:
            try:
                hits = search_docs_azure(question, filters=filters, k=3)
            except Exception as exc:  # noqa: BLE001 - report and carry on
                problems.append(f"search failed for {question!r}: {exc}")
                continue
            print(f"  {question}")
            if not hits:
                problems.append(f"no result for {question!r}")
            for h in hits:
                print(f"    {h.metadata.get('source_file')} p{h.metadata.get('page')}: {first_heading(h.page_content)}")

    print()
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("OK: counts, metadata and ids are as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
