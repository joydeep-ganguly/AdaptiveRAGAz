"""The two skills. Each takes the data of one record (or all records) and returns the output."""

import base64
import binascii
import io
from urllib.parse import unquote, urlparse

from skills import config
from skills.errors import SkillError
from skills.filename import parse_filename
from skills.pages import build_pages


def _file_name(data: dict) -> str:
    name = data.get("file_name")
    if not name and data.get("url"):
        name = unquote(urlparse(data["url"]).path.rsplit("/", 1)[-1])
    if not name:
        raise SkillError("Input 'file_name' (or 'url') is required")
    return name


def _decode_file_data(file_data) -> bytes:
    payload = file_data.get("data") if isinstance(file_data, dict) else file_data
    if not isinstance(payload, str) or not payload:
        raise SkillError("Input 'file_data' has no base64 'data'")
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise SkillError(f"Input 'file_data' is not valid base64: {exc}") from exc


def _count_pages(pdf: bytes) -> int:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except (PyPdfError, ValueError, OSError) as exc:
        raise SkillError(f"The file could not be read as a PDF: {exc}") from exc


def pdf_pages(data: dict, fetch_pdf=None, analyze=None) -> tuple[dict, list[str]]:
    """
    Input: file_name (bare name), and either file_data (base64 file) or url (blob URL).
    Output: company_name, doc_type, fiscal_year, fiscal_quarter, source_file and
    pages [{page, text}]. Pages with no text are left out (a warning lists their numbers).
    """
    from skills import docint

    name = _file_name(data)
    fields = parse_filename(name)

    if data.get("file_data"):
        pdf = _decode_file_data(data["file_data"])
    elif data.get("url"):
        if fetch_pdf is None:
            from skills.blobs import download_blob

            fetch_pdf = lambda url: download_blob(url, config.allowed_blob_host())  # noqa: E731
        pdf = fetch_pdf(data["url"])
    else:
        raise SkillError("Input 'file_data' or 'url' is required")

    if not pdf.startswith(b"%PDF-"):
        raise SkillError("The file is not a PDF")

    total = _count_pages(pdf)
    numbered = docint.analyze_pdf(
        pdf,
        total_pages=total,
        chunk_pages=config.di_chunk_pages(),
        parallelism=config.di_parallelism(),
        **({"analyze": analyze} if analyze else {}),
    )
    pages, skipped = build_pages(numbered)
    if not pages:
        raise SkillError("No page of the PDF has any text")

    warnings = []
    if skipped:
        warnings.append(f"Pages with no text were skipped: {', '.join(str(n) for n in skipped)}")

    return {**fields, "source_file": name, "pages": pages}, warnings


def embed(records: list[tuple[object, dict]], embed_texts=None) -> dict:
    """Input per record: text. Output: vector (3072 floats). All records go to Gemini together."""
    if embed_texts is None:
        from skills.embeddings import embed_texts

    outcomes: dict = {}
    todo: list[tuple[object, str]] = []
    for record_id, data in records:
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            outcomes[record_id] = SkillError("Input 'text' is required and must not be empty")
        else:
            todo.append((record_id, text))

    if todo:
        results = embed_texts([text for _, text in todo])
        for (record_id, _), result in zip(todo, results):
            if isinstance(result, SkillError):
                outcomes[record_id] = result
            else:
                outcomes[record_id] = ({"vector": result.vector}, [result.warning] if result.warning else [])
    return outcomes
