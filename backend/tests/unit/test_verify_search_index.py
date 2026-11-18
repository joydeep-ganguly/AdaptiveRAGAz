import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_search_index.py"
spec = importlib.util.spec_from_file_location("verify_search_index", SCRIPT)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def page(n, name="apple 8-k q4 2023.pdf", **over):
    meta = v.metadata_from_name(name)
    doc = {"id": f"id-{name}-{n}", "parent_id": "p1", "page": n, "source_file": name, "page_text": f"# H{n}\ntext", **meta}
    doc.update(over)
    return doc


def test_metadata_from_name():
    assert v.metadata_from_name("amazon 10-k 2023.pdf") == {
        "company_name": "amazon", "doc_type": "10-k", "fiscal_year": 2023, "fiscal_quarter": None}
    assert v.metadata_from_name("apple 8-k q4 2023.pdf")["fiscal_quarter"] == "q4"
    assert v.metadata_from_name("bad.pdf") == {}


def test_clean_index_has_no_problems_and_gaps_are_reported():
    docs = [page(n) for n in range(1, 10)]
    rows, problems = v.analyse(docs, {"apple 8-k q4 2023.pdf": 9})
    assert problems == [] and rows[0]["gaps"] == [] and rows[0]["count"] == 9

    docs = [page(n) for n in (1, 2, 4)]
    rows, problems = v.analyse(docs, {"apple 8-k q4 2023.pdf": 3})
    assert problems == [] and rows[0]["gaps"] == [3]


def test_problems_are_found():
    docs = [page(1), page(2, fiscal_quarter=None), page(3, page_text="  "), page(3, id="id-3b"),
            {"id": "parent", "source_file": None, "parent_id": None}]
    _, problems = v.analyse(docs, {"apple 8-k q4 2023.pdf": 9, "google 10-k 2023.pdf": 111})
    text = " | ".join(problems)
    assert "without a source_file" in text
    assert "4 page documents, expected 9" in text
    assert "metadata" in text and "appears more than once" in text and "empty page_text" in text
    assert "google 10-k 2023.pdf: 0 page documents, expected 111" in text


def test_unexpected_file_and_duplicate_ids():
    docs = [page(1, "amazon 10-k 2024.pdf"), page(1, "amazon 10-k 2024.pdf")]
    _, problems = v.analyse(docs, {})
    assert any("not an expected file" in p for p in problems)
    assert any("duplicate document ids" in p for p in problems)


def test_first_heading():
    assert v.first_heading("intro\n## Cash Flows\nx") == "Cash Flows"
    assert v.first_heading("plain text only") == "plain text only"
