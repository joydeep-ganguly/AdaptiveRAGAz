"""
Calls the two skills the way the search service does and prints a short summary.

  python tools/call_skills.py --base http://localhost:7071 embed
  python tools/call_skills.py --base http://localhost:7071 pages --blob "apple/apple 8-k q4 2023.pdf"
  python tools/call_skills.py --base https://<app>.azurewebsites.net --code <function key> pages --file "C:\\path\\apple 8-k q4 2023.pdf"

`--blob` sends the blob URL (the Function reads it with its own identity); `--file` sends the
file as base64, like the search service does with file_data. Needs no packages beyond Python.
"""

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote


def post(base: str, route: str, code: "str | None", payload: dict, timeout: int = 240) -> dict:
    url = f"{base.rstrip('/')}/api/{route}" + (f"?code={code}" if code else "")
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
            status = response.status
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}")
        sys.exit(1)
    print(f"HTTP {status} in {time.time() - started:.1f}s")
    return body


def first_heading(text: str) -> str:
    match = re.search(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip()[:60] if match else "-"


_LAYOUT = re.compile(r"<!--\s*(?:PageBreak|PageHeader|PageNumber|PageFooter)\b.*?-->", re.DOTALL)
_BREAK = re.compile(r"<!--\s*PageBreak\s*-->")


def compare_with_spike(spike_dir: str, source_file: str, pages: list) -> None:
    """Page texts against the markdown that scripts/spike_di_layout.py saved for the same file."""
    path = Path(spike_dir) / (source_file[: -len(".pdf")] + ".md")
    if not path.exists():
        print(f"no spike file at {path}")
        return
    pieces = _BREAK.split(path.read_text(encoding="utf-8"))
    expected = {}
    for number, piece in enumerate(pieces, start=1):
        text = re.sub(r"\n{3,}", "\n\n", _LAYOUT.sub("", piece)).strip()
        if text:
            expected[number] = text
    got = {p["page"]: p["text"] for p in pages}

    def loose(text: str) -> str:
        """Same words in the same order: ignores heading depth ('##' vs '###') and spacing."""
        return re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)).strip()

    different = sorted(n for n in expected if got.get(n) != expected[n])
    different_loose = sorted(n for n in expected if loose(got.get(n, "")) != loose(expected[n]))
    total = len(expected)
    print(f"same text as the spike: {total - len(different)}/{total} pages exactly; "
          f"{total - len(different_loose)}/{total} ignoring heading depth and spacing")
    print(f"differ in more than that: {different_loose[:20]}")
    for number in different[:3]:
        a, b = expected[number].splitlines(), got.get(number, "").splitlines()
        for x, y in zip(a, b):
            if x != y:
                print(f"  page {number}: spike {x[:70]!r}  |  function {y[:70]!r}")
                break
        else:
            print(f"  page {number}: spike {len(a)} lines, function {len(b)} lines")


def run_embed(args) -> None:
    payload = {"values": [
        {"recordId": "1", "data": {"text": "Total net sales increased 11% year over year."}},
        {"recordId": "2", "data": {"text": "Cash and cash equivalents were $73.4 billion."}},
        {"recordId": "3", "data": {"text": ""}},
    ]}
    body = post(args.base, "embed", args.code, payload)
    for value in body["values"]:
        vector = value["data"].get("vector")
        print(f"record {value['recordId']}: vector length {len(vector) if vector else None}, "
              f"errors {value['errors']}, warnings {value['warnings']}")
    print("expected: records 1 and 2 have length 3072; record 3 has an error about 'text'")


def run_pages(args) -> None:
    if args.blob:
        url = f"https://{args.storage_host}/{args.container}/{args.prefix}/" + quote(args.blob)
        name = args.blob.rsplit("/", 1)[-1]
        data = {"file_name": name, "url": url}
    else:
        path = Path(args.file)
        data = {"file_name": path.name,
                "file_data": {"$type": "file", "data": base64.b64encode(path.read_bytes()).decode()}}
    body = post(args.base, "pdf-pages", args.code, {"values": [{"recordId": "0", "data": data}]})
    value = body["values"][0]
    print(f"errors: {value['errors']}")
    print(f"warnings: {value['warnings']}")
    output = value["data"]
    if not output:
        return
    pages = output["pages"]
    print({k: v for k, v in output.items() if k != "pages"})
    numbers = [p["page"] for p in pages]
    print(f"pages: {len(pages)}, first {numbers[0]}, last {numbers[-1]}, "
          f"gaps {[n for n in range(numbers[0], numbers[-1] + 1) if n not in set(numbers)]}")
    print("sample:", [(p["page"], first_heading(p["text"]), len(p["text"])) for p in pages[:3]])
    if args.spike_dir:
        compare_with_spike(args.spike_dir, output["source_file"], pages)
    if args.save:
        Path(args.save).write_text(json.dumps(output, indent=1), encoding="utf-8")
        print(f"saved to {args.save}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:7071")
    parser.add_argument("--code", help="function key (deployed app)")
    sub = parser.add_subparsers(dest="skill", required=True)
    sub.add_parser("embed")
    pages = sub.add_parser("pages")
    pages.add_argument("--blob", help="company/file name, e.g. 'apple/apple 8-k q4 2023.pdf'")
    pages.add_argument("--file", help="local PDF path")
    pages.add_argument("--storage-host", default="stadaptiverag32.blob.core.windows.net")
    pages.add_argument("--container", default="rag")
    pages.add_argument("--prefix", default="rag_data")
    pages.add_argument("--spike-dir", help="folder with the spike's markdown files, to compare page texts")
    pages.add_argument("--save", help="write the skill output to this JSON file")
    args = parser.parse_args()
    if args.skill == "embed":
        run_embed(args)
    else:
        if bool(args.blob) == bool(args.file):
            parser.error("give exactly one of --blob or --file")
        run_pages(args)


if __name__ == "__main__":
    main()
