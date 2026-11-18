"""
Seed the trial Azure AI Search index from Chroma (Sub-Plan 4, Step 4).

Copies the pages of the 3 test PDFs (text, metadata and the stored Gemini vectors)
from the local Chroma index into the Azure AI Search index, so the Azure query path
can be tested before the skillset exists. Chroma is only read.

Seeded documents have the id "seed-<chroma id>" and parent_id "seed", which is how
they are found and removed again (Step 6) before the indexer runs.

    python scripts/seed_search_from_chroma.py --dry-run   # read Chroma, upload nothing
    python scripts/seed_search_from_chroma.py             # upload (safe to repeat: same ids are overwritten)
    python scripts/seed_search_from_chroma.py --delete    # remove the seeded documents

Needs `az login` (role: Search Index Data Contributor) and backend/.env
(AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_INDEX_NAME). Stop the backend first so that
only one process opens the local Chroma store.

Exit code 0 = ok, 1 = a check failed.
"""

import argparse
import sys
import time
from pathlib import Path

# scripts/ lives at the project root, one level above backend/ (see seed_admin_user.py).
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SEED_PARENT_ID = "seed"
VECTOR_DIMENSIONS = 3072
BATCH_SIZE = 25

# source_file -> ($filter that selects exactly this PDF's pages in the index, expected page count)
SEED_FILES = {
    "amazon 10-k 2023.pdf": ("company_name eq 'amazon' and doc_type eq '10-k' and fiscal_year eq 2023", 93),
    "apple 8-k q4 2023.pdf": (
        "company_name eq 'apple' and doc_type eq '8-k' and fiscal_year eq 2023 and fiscal_quarter eq 'q4'", 9),
    "google 10-k 2023.pdf": ("company_name eq 'google' and doc_type eq '10-k' and fiscal_year eq 2023", 111),
}


def to_search_doc(chroma_id: str, metadata: dict, text: str, embedding) -> dict:
    """One Chroma page record -> one search document (fiscal_quarter is left out for annual reports)."""
    vector = [float(x) for x in embedding]
    if len(vector) != VECTOR_DIMENSIONS:
        raise ValueError(f"{chroma_id}: expected {VECTOR_DIMENSIONS} dimensions, got {len(vector)}")
    doc = {
        "id": f"seed-{chroma_id}",
        "parent_id": SEED_PARENT_ID,
        "company_name": metadata["company_name"],
        "doc_type": metadata["doc_type"],
        "fiscal_year": int(metadata["fiscal_year"]),
        "page": int(metadata["page"]),
        "source_file": metadata["source_file"],
        "page_text": text,
        "vector": vector,
    }
    if metadata.get("fiscal_quarter"):
        doc["fiscal_quarter"] = metadata["fiscal_quarter"]
    return doc


def read_chroma_docs() -> list[dict]:
    from app.agent.retrieval.vectorstore import vector_store  # opens the local Chroma store (read only here)

    data = vector_store.get(
        where={"source_file": {"$in": list(SEED_FILES)}},
        include=["documents", "metadatas", "embeddings"],
    )
    ids = data["ids"]
    return [
        to_search_doc(ids[i], data["metadatas"][i], data["documents"][i], data["embeddings"][i])
        for i in range(len(ids))
    ]


def count_by_file(docs: list[dict]) -> dict[str, int]:
    counts = {name: 0 for name in SEED_FILES}
    for doc in docs:
        counts[doc["source_file"]] = counts.get(doc["source_file"], 0) + 1
    return counts


def get_client():
    from app.agent.retrieval.azure_search_store import get_search_client

    return get_search_client()


def count_matching(client, odata_filter: str) -> int:
    results = client.search(search_text="*", filter=odata_filter, include_total_count=True, top=0)
    return results.get_count()


def wait_for_count(client, odata_filter: str, expected: int, timeout_s: int = 30) -> int:
    """Indexing is eventually consistent: poll for a few seconds."""
    deadline = time.time() + timeout_s
    count = count_matching(client, odata_filter)
    while count != expected and time.time() < deadline:
        time.sleep(2)
        count = count_matching(client, odata_filter)
    return count


def seeded_keys(client) -> list[str]:
    results = client.search(search_text="*", filter=f"parent_id eq '{SEED_PARENT_ID}'", select=["id"], top=1000)
    return [hit["id"] for hit in results]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="read Chroma and report; upload nothing")
    parser.add_argument("--delete", action="store_true", help="remove the seeded documents from the index")
    args = parser.parse_args()

    from app.config import get_settings

    settings = get_settings()
    print(f"Target: {settings.azure_search_endpoint}  index: {settings.azure_search_index_name}")

    if args.delete:
        client = get_client()
        keys = seeded_keys(client)
        print(f"Seeded documents found: {len(keys)}")
        for start in range(0, len(keys), 100):
            client.delete_documents(documents=[{"id": key} for key in keys[start:start + 100]])
        left = wait_for_count(client, f"parent_id eq '{SEED_PARENT_ID}'", 0)
        print(f"Seeded documents left: {left}")
        return 0 if left == 0 else 1

    docs = read_chroma_docs()
    counts = count_by_file(docs)
    ok = True
    print("Pages read from Chroma:")
    for name, (_, expected) in SEED_FILES.items():
        flag = "ok" if counts[name] == expected else f"EXPECTED {expected}"
        print(f"  {name:28s} {counts[name]:4d}  {flag}")
        ok = ok and counts[name] == expected
    print(f"  total {len(docs)}")
    if not ok or len(docs) != sum(n for _, n in SEED_FILES.values()):
        print("Chroma does not hold the expected pages; nothing uploaded.")
        return 1
    if args.dry_run:
        print("Dry run: nothing uploaded.")
        return 0

    client = get_client()
    failed = []
    for start in range(0, len(docs), BATCH_SIZE):
        for result in client.upload_documents(documents=docs[start:start + BATCH_SIZE]):
            if not result.succeeded:
                failed.append((result.key, result.error_message))
    print(f"Uploaded {len(docs) - len(failed)} of {len(docs)} documents.")
    for key, message in failed[:10]:
        print(f"  FAILED {key}: {message}")

    print("Documents in the index (parent_id 'seed'):")
    for name, (odata_filter, expected) in SEED_FILES.items():
        got = wait_for_count(client, f"parent_id eq '{SEED_PARENT_ID}' and {odata_filter}", expected)
        flag = "ok" if got == expected else f"EXPECTED {expected}"
        print(f"  {name:28s} {got:4d}  {flag}")
        ok = ok and got == expected
    return 0 if ok and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
