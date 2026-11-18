"""
Read-only comparison of the two retrieval backends (Sub-Plan 4, Steps 4 and 10).

For each question the same filters and the same 5 ranking keywords are sent to
  * Chroma             (vectorstore.search_docs, MMR, `$contains` keyword filter), and
  * Azure AI Search    (azure_search_store.search_docs_azure), in each keyword mode asked for,
then the same BM25 re-rank (rank_documents_by_keywords) picks the final pages. Nothing is
written to Chroma or to the index.

The questions are limited to the 3 test PDFs and each one pins a single PDF with explicit
filters, so both backends search the same pages. Run after the seeding script:

    python scripts/compare_retrieval_backends.py
    python scripts/compare_retrieval_backends.py --modes hard

Needs `az login` (role: Search Index Data Reader or Contributor) and backend/.env.
Stop the backend first (Chroma). The report with the final pages per backend is written to
backend/storage/debug_logs/retrieval_compare.md (git-ignored).
"""

import argparse
import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

AMAZON = {"company_name": "amazon", "doc_type": "10-k", "fiscal_year": 2023}
GOOGLE = {"company_name": "google", "doc_type": "10-k", "fiscal_year": 2023}
APPLE = {"company_name": "apple", "doc_type": "8-k", "fiscal_year": 2023, "fiscal_quarter": "q4"}

QUESTIONS = [
    ("Amazon 10-K 2023", AMAZON, "What were Amazon's total net sales and operating income in 2023?"),
    ("Amazon 10-K 2023", AMAZON, "How did Amazon's cash flows from operating activities change in 2023?"),
    ("Amazon 10-K 2023", AMAZON, "What risks does Amazon describe about competition?"),
    ("Google 10-K 2023", GOOGLE, "What was Alphabet's revenue by segment in 2023?"),
    ("Google 10-K 2023", GOOGLE, "How much did Alphabet spend on research and development in 2023?"),
    ("Google 10-K 2023", GOOGLE, "What does Alphabet say about legal and regulatory matters?"),
    ("Apple 8-K Q4 2023", APPLE, "What were Apple's net sales and net income in the fourth quarter?"),
    ("Apple 8-K Q4 2023", APPLE, "How much capital did Apple return to shareholders in the quarter?"),
]


def page_key(doc) -> tuple:
    return (doc.metadata.get("source_file"), doc.metadata.get("page"))


def keys(docs) -> list[tuple]:
    return [page_key(d) for d in docs]


def overlap(a: list, b: list) -> int:
    return len(set(a) & set(b))


def first_heading(doc) -> str:
    match = re.search(r"^#+\s+(.+)$", doc.page_content, re.M)
    text = match.group(1) if match else doc.page_content.strip().replace("\n", " ")
    return text[:70]


def describe(docs) -> list[str]:
    return [f"p.{d.metadata.get('page')}  {first_heading(d)}" for d in docs]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modes", nargs="+", default=["hard", "hybrid"], choices=["hard", "hybrid"])
    parser.add_argument("--k", type=int, default=5, help="final pages per question (the app default is 5)")
    args = parser.parse_args()

    from app.config import get_settings
    from app.agent.retrieval.filters import generate_ranking_keywords
    from app.agent.retrieval.ranking import rank_documents_by_keywords
    from app.agent.retrieval.vectorstore import search_docs
    from app.agent.retrieval.azure_search_store import search_docs_azure

    candidates_k = 10 * args.k  # same as retriever_docs
    report = ["# Retrieval comparison: Chroma vs Azure AI Search\n"]
    summary = []

    for label, filters, question in QUESTIONS:
        keywords = generate_ranking_keywords(question)
        chroma_cand = search_docs(question, filters, keywords, k=candidates_k)
        chroma_final = rank_documents_by_keywords(chroma_cand, keywords, k=args.k)
        chroma_keys = keys(chroma_final)

        report += [f"## {label}: {question}", f"filters: {filters}", f"keywords: {keywords}", "",
                   f"**Chroma** candidates: {len(chroma_cand)}", ""]
        report += [f"- {line}" for line in describe(chroma_final)] + [""]

        for mode in args.modes:
            os.environ["AZURE_SEARCH_KEYWORD_MODE"] = mode
            get_settings.cache_clear()
            azure_cand = search_docs_azure(question, filters, keywords, k=candidates_k)
            azure_final = rank_documents_by_keywords(azure_cand, keywords, k=args.k)

            top10 = overlap(keys(chroma_cand)[:10], keys(azure_cand)[:10])
            final_shared = overlap(chroma_keys, keys(azure_final))
            chroma_final_available = overlap(chroma_keys, keys(azure_cand))
            summary.append((label, question, mode, len(chroma_cand), len(azure_cand), top10,
                            final_shared, chroma_final_available, len(chroma_keys)))
            report += [f"**Azure ({mode})** candidates: {len(azure_cand)}; top-10 shared with Chroma: {top10}; "
                       f"final pages shared with Chroma: {final_shared}/{len(chroma_keys)}", ""]
            report += [f"- {line}" for line in describe(azure_final)] + [""]

    print(f"{'PDF':18s} {'mode':7s} {'cand C/A':>9s} {'top10':>6s} {'final':>6s} {'C-final in A-cand':>18s}  question")
    for label, question, mode, nc, na, top10, shared, avail, nfinal in summary:
        print(f"{label:18s} {mode:7s} {f'{nc}/{na}':>9s} {top10:>6d} {f'{shared}/{nfinal}':>6s} "
              f"{f'{avail}/{nfinal}':>18s}  {question[:60]}")

    out = get_settings().resolved_debug_logs_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / "retrieval_compare.md"
    path.write_text("\n".join(report), encoding="utf-8")
    print(f"\nReport: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
