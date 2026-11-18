import base64

import pytest

from skills.embeddings import Embedded
from skills.errors import SkillError
from skills.handlers import embed, pdf_pages


def make_pdf(pages=2):
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def fake_analyze(data, page_range):
    return [1, 2], "# One\n<!-- PageBreak -->\n<!-- PageNumber=\"2\" -->"


def test_pdf_pages_from_file_data_with_metadata_and_skipped_page_warning():
    pdf = make_pdf(2)
    data = {"file_name": "amazon 10-q q1 2024.pdf", "file_data": {"$type": "file", "data": base64.b64encode(pdf).decode()}}
    output, warnings = pdf_pages(data, analyze=fake_analyze)
    assert output["company_name"] == "amazon" and output["fiscal_quarter"] == "q1" and output["fiscal_year"] == 2024
    assert output["source_file"] == "amazon 10-q q1 2024.pdf"
    assert output["pages"] == [{"page": 1, "text": "# One"}]
    assert warnings == ["Pages with no text were skipped: 2"]


def test_pdf_pages_from_url_takes_the_name_from_the_url(monkeypatch):
    pdf = make_pdf(2)
    fetched = []
    data = {"url": "https://acct.blob.core.windows.net/rag/rag_data/amazon/amazon%2010-k%202023.pdf"}
    output, _ = pdf_pages(data, fetch_pdf=lambda url: fetched.append(url) or pdf, analyze=fake_analyze)
    assert output["source_file"] == "amazon 10-k 2023.pdf" and output["fiscal_quarter"] is None
    assert fetched == [data["url"]]


@pytest.mark.parametrize("data, message", [
    ({}, "file_name"),
    ({"file_name": "bad name.pdf", "url": "https://x"}, "does not match"),
    ({"file_name": "amazon 10-k 2023.pdf"}, "file_data"),
    ({"file_name": "amazon 10-k 2023.pdf", "file_data": {"data": base64.b64encode(b"not a pdf").decode()}}, "not a PDF"),
])
def test_pdf_pages_input_errors(data, message):
    with pytest.raises(SkillError, match=message):
        pdf_pages(data, analyze=fake_analyze)


def test_pdf_pages_with_no_text_anywhere_is_an_error():
    pdf = make_pdf(1)
    data = {"file_name": "amazon 10-k 2023.pdf", "file_data": base64.b64encode(pdf).decode()}
    with pytest.raises(SkillError, match="No page"):
        pdf_pages(data, analyze=lambda d, r: ([1], "<!-- PageNumber=\"1\" -->"))


def test_embed_validates_text_and_maps_results_back_to_record_ids():
    def fake(texts):
        return [Embedded([1.0], "short") if t == "b" else Embedded([2.0]) for t in texts]

    out = embed([("1", {"text": "a"}), ("2", {"text": ""}), ("3", {"text": "b"}), ("4", {})], embed_texts=fake)
    assert out["1"] == ({"vector": [2.0]}, [])
    assert isinstance(out["2"], SkillError) and isinstance(out["4"], SkillError)
    assert out["3"] == ({"vector": [1.0]}, ["short"])
