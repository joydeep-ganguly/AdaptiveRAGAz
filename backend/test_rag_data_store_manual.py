"""
Manual smoke check of Azure Blob access for the RAG PDFs (Sub-Plan 3, Step 3).
Needs `az login` and backend/.env. Run from inside backend/:

    python test_rag_data_store_manual.py

It lists the 3 PDFs, downloads one, and uploads then DELETES a tiny throwaway PDF
under a "smoke-test" company folder. The delete runs in a `finally` block: leave
no stray blob behind, because the Azure AI Search indexer (a later sub-plan)
will ingest whatever is in the container.
"""

from app.agent.schemas import DocType
from app.storage import rag_data_store as store

EXPECTED = {
    ("amazon", "amazon 10-k 2023.pdf"): 800598,
    ("apple", "apple 8-k q4 2023.pdf"): 101359,
    ("google", "google 10-k 2023.pdf"): 1371793,
}

# 1. Listing --------------------------------------------------------------
files = store.list_files()
for f in files:
    print(f"{f.company_name:<8} {f.name:<28} {f.size:>9} bytes")

assert {(f.company_name, f.name): f.size for f in files} == EXPECTED, (
    "blob listing differs from the expected 3 files"
)
print("Listing matches the 3 expected files.")

# Every blob name must follow the naming convention Sub-Plan 4 relies on.
for f in files:
    fields = store.parse_filename(f.name)
    assert fields.company_name == f.company_name, f"{f.name}: company folder does not match the name"
    print(f"  parsed: {fields}")
print("All names follow the convention.")

only_apple = store.list_files("apple")
assert [f.name for f in only_apple] == ["apple 8-k q4 2023.pdf"]
print("Company filter OK.")

# 2. Download -------------------------------------------------------------
data = store.download("amazon", "amazon 10-k 2023.pdf")
print(f"Downloaded amazon 10-k 2023.pdf: {len(data)} bytes; starts with {data[:8]!r}")
assert len(data) == EXPECTED[("amazon", "amazon 10-k 2023.pdf")]
assert data.startswith(b"%PDF-")
print("Download OK.")

# 3. Upload + delete a throwaway PDF ---------------------------------------
# The store has no delete function (the app never deletes), so this script uses
# the container client directly for the cleanup.
container = store._get_container_client()
blob_name = "rag_data/smoke-test/smoke-test other 2000.pdf"
try:
    result = store.upload(
        "smoke.pdf", "smoke-test", DocType.OTHER, 2000, None, b"%PDF-1.4\n% smoke test, safe to delete\n"
    )
    print(f"Uploaded: {result}")

    props = container.get_blob_client(blob_name).get_blob_properties()
    print(f"  content type: {props.content_settings.content_type}")
    print(f"  metadata:     {props.metadata}")
    assert props.content_settings.content_type == "application/pdf"
    assert props.metadata == {"company_name": "smoke-test", "doc_type": "other", "fiscal_year": "2000"}

    assert [f.name for f in store.list_files("smoke-test")] == ["smoke-test other 2000.pdf"]
    print("Upload, content type, metadata and listing OK.")
finally:
    try:
        container.delete_blob(blob_name)
        print("Throwaway blob deleted.")
    except Exception as exc:  # noqa: BLE001 - report it loudly, never hide a leftover blob
        print(f"!!! COULD NOT DELETE {blob_name}: {exc}\n!!! Delete it by hand in the Portal.")

assert store.list_files("smoke-test") == []
assert {(f.company_name, f.name) for f in store.list_files()} == set(EXPECTED)
print("Container is back to the 3 original PDFs.")
