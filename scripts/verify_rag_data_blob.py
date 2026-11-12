"""
Read-only check: compares the RAG PDFs in Azure Blob Storage with the local
backend/data/rag_data folder (Sub-Plan 3, Step 6). It never uploads, changes or
deletes anything.

For every PDF it compares size and SHA-256 and reports one status per file:

    IDENTICAL      in blob and local, same content
    DIFFERENT      in blob and local, content differs      -> error
    PENDING        local only (not copied to blob yet)     -> fine for now
    BLOB ONLY      in blob but not in the local folder     -> error
    BAD NAME       name does not follow the convention     -> error

Needs `az login` and backend/.env. Run from anywhere:

    python scripts/verify_rag_data_blob.py

Before the remaining PDFs are copied to blob (Step 6) PENDING is expected. After
they are copied (Step 6b), add --require-all so PENDING also counts as an error:

    python scripts/verify_rag_data_blob.py --require-all

Exit code 0 = no errors, 1 = at least one error.
"""

import argparse
import hashlib
import sys
from pathlib import Path

# scripts/ lives at the project root, one level above backend/ — see seed_admin_user.py.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.storage import rag_data_store  # noqa: E402

DEFAULT_LOCAL_DIR = BACKEND_DIR / "data" / "rag_data"

IDENTICAL = "IDENTICAL"
DIFFERENT = "DIFFERENT"
PENDING = "PENDING"
BLOB_ONLY = "BLOB ONLY"
BAD_NAME = "BAD NAME"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_local(local_dir: Path) -> dict[tuple[str, str], bytes]:
    """{(company, file name): content} for every local {company}/*.pdf."""
    files: dict[tuple[str, str], bytes] = {}
    for company_dir in sorted(p for p in local_dir.iterdir() if p.is_dir()):
        for pdf in sorted(company_dir.glob("*.pdf")):
            files[(company_dir.name, pdf.name)] = pdf.read_bytes()
    return files


def compare(local: dict, blob_files: list, download) -> list[tuple[str, str, str, str]]:
    """
    Returns rows of (status, company, name, detail), sorted by company and name.
    `blob_files` is a list of objects with company_name / name / size, and
    `download(company, name)` returns the blob's bytes.
    """
    rows = []
    in_blob = set()

    for blob in blob_files:
        key = (blob.company_name, blob.name)
        in_blob.add(key)

        try:
            fields = rag_data_store.parse_filename(blob.name)
        except rag_data_store.InvalidRagDataFieldsError as exc:
            rows.append((BAD_NAME, blob.company_name, blob.name, str(exc)))
            continue
        if fields.company_name != blob.company_name:
            rows.append((BAD_NAME, blob.company_name, blob.name, "company folder does not match the name"))
            continue

        if key not in local:
            rows.append((BLOB_ONLY, blob.company_name, blob.name, f"{blob.size} bytes in blob, no local file"))
            continue

        blob_bytes = download(blob.company_name, blob.name)
        local_bytes = local[key]
        if len(blob_bytes) == len(local_bytes) and _sha256(blob_bytes) == _sha256(local_bytes):
            rows.append((IDENTICAL, blob.company_name, blob.name, f"{len(blob_bytes)} bytes"))
        else:
            rows.append(
                (
                    DIFFERENT, blob.company_name, blob.name,
                    f"blob {len(blob_bytes)} bytes (sha256 {_sha256(blob_bytes)[:12]}…) vs "
                    f"local {len(local_bytes)} bytes (sha256 {_sha256(local_bytes)[:12]}…)",
                )
            )

    for (company, name), content in local.items():
        if (company, name) in in_blob:
            continue
        # Catch a badly named local file now, before it is copied to blob.
        try:
            fields = rag_data_store.parse_filename(name)
            name_problem = None if fields.company_name == company else "company folder does not match the name"
        except rag_data_store.InvalidRagDataFieldsError as exc:
            name_problem = str(exc)
        if name_problem:
            rows.append((BAD_NAME, company, name, f"local file: {name_problem}"))
        else:
            rows.append((PENDING, company, name, f"{len(content)} bytes, not in blob yet"))

    return sorted(rows, key=lambda r: (r[1], r[2].lower()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local-dir", type=Path, default=DEFAULT_LOCAL_DIR, help="local rag_data folder")
    parser.add_argument("--require-all", action="store_true", help="treat PENDING files as errors")
    args = parser.parse_args(argv)

    if not args.local_dir.is_dir():
        print(f"Local folder not found: {args.local_dir}")
        return 1

    local = read_local(args.local_dir)
    rows = compare(local, rag_data_store.list_files(), rag_data_store.download)

    for status, company, name, detail in rows:
        print(f"{status:<10} {company:<8} {name:<28} {detail}")

    counts = {s: sum(1 for r in rows if r[0] == s) for s in (IDENTICAL, DIFFERENT, PENDING, BLOB_ONLY, BAD_NAME)}
    print()
    print("Summary: " + ", ".join(f"{n} {s.lower()}" for s, n in counts.items()))

    errors = counts[DIFFERENT] + counts[BLOB_ONLY] + counts[BAD_NAME]
    if args.require_all:
        errors += counts[PENDING]

    print("RESULT: OK" if errors == 0 else f"RESULT: {errors} problem(s) found")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
