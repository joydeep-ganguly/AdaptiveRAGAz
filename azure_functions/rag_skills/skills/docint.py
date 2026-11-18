"""Document Intelligence layout model: PDF bytes to (page number, markdown) pairs."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Callable

from skills import config
from skills.errors import SkillError
from skills.pages import number_pages, page_ranges

# analyze(pdf_bytes, page_range) -> (page numbers, markdown); page_range is None for the whole file
Analyze = Callable[[bytes, "str | None"], "tuple[list[int], str]"]


@lru_cache
def _client():
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.identity import DefaultAzureCredential

    return DocumentIntelligenceClient(
        endpoint=config.document_intelligence_endpoint(), credential=DefaultAzureCredential()
    )


def analyze_with_service(data: bytes, page_range: "str | None") -> "tuple[list[int], str]":
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentContentFormat
    from azure.core.exceptions import AzureError

    kwargs = {"pages": page_range} if page_range else {}
    try:
        poller = _client().begin_analyze_document(
            "prebuilt-layout",
            AnalyzeDocumentRequest(bytes_source=data),
            output_content_format=DocumentContentFormat.MARKDOWN,
            **kwargs,
        )
        result = poller.result()
    except AzureError as exc:
        raise SkillError(f"Document Intelligence failed: {exc}") from exc
    return [page.page_number for page in result.pages], result.content or ""


def analyze_pdf(
    data: bytes,
    total_pages: int,
    chunk_pages: int,
    parallelism: int,
    analyze: Analyze = analyze_with_service,
) -> list[tuple[int, str]]:
    """
    A file of at most chunk_pages pages is analysed in one call. A longer file is cut into
    page ranges that are analysed in parallel, so that one skill call stays well under the
    230 s limit (about 1.1 s per page in the spike).
    """
    if total_pages <= chunk_pages:
        numbers, markdown = analyze(data, None)
        return number_pages(numbers, markdown)

    ranges = page_ranges(total_pages, chunk_pages)

    def run(span: tuple[int, int]) -> list[tuple[int, str]]:
        start, end = span
        numbers, markdown = analyze(data, f"{start}-{end}")
        expected = list(range(start, end + 1))
        if sorted(numbers) != expected:
            raise SkillError(f"Document Intelligence returned pages {numbers} for the range {start}-{end}")
        return number_pages(numbers, markdown)

    with ThreadPoolExecutor(max_workers=min(parallelism, len(ranges))) as pool:
        parts = list(pool.map(run, ranges))

    return [page for part in parts for page in part]
