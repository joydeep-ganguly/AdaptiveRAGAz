"""
Manual smoke check of Azure Blob access for code_data (plan Step 3).
Needs `az login` and backend/.env. Run from inside backend/:

    python test_code_data_store_manual.py
"""

from app.storage import code_data_store as store

EXPECTED = {
    "IMDB-Movie-Data.csv": 308767,
    "apple_2024.xlsx": 15759,
    "google_2024.xlsx": 16630,
    "titanic.csv": 60302,
}

files = store.list_files()
for f in files:
    print(f"{f.name:<25} {f.size:>8} bytes")

assert {f.name: f.size for f in files} == EXPECTED, "blob listing differs from the expected local files"
print("Listing matches the 4 expected files.")

data = store.download("titanic.csv")
print(f"Downloaded titanic.csv: {len(data)} bytes; header: {data.splitlines()[0].decode()}")
assert len(data) == EXPECTED["titanic.csv"]
print("Download OK.")