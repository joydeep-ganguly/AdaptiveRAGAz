"""Turning Document Intelligence markdown into one text per page."""

import re

from skills.errors import SkillError

PAGE_BREAK = re.compile(r"<!--\s*PageBreak\s*-->")
# Layout comments carry no page content (headers, footers and page numbers repeat on every page).
LAYOUT_COMMENT = re.compile(r"<!--\s*(?:PageBreak|PageHeader|PageNumber|PageFooter)\b.*?-->", re.DOTALL)
_BLANK_RUNS = re.compile(r"\n{3,}")


def split_markdown_pages(markdown: str) -> list[str]:
    return PAGE_BREAK.split(markdown or "")


def clean_page(text: str) -> str:
    text = LAYOUT_COMMENT.sub("", text or "")
    text = _BLANK_RUNS.sub("\n\n", text)
    return text.strip()


def page_ranges(total_pages: int, chunk_pages: int) -> list[tuple[int, int]]:
    """[(1, 50), (51, 100), (101, 111)] for 111 pages in chunks of 50."""
    if total_pages < 1:
        raise SkillError("The PDF has no pages")
    return [
        (start, min(start + chunk_pages - 1, total_pages))
        for start in range(1, total_pages + 1, chunk_pages)
    ]


def number_pages(page_numbers: list[int], markdown: str) -> list[tuple[int, str]]:
    """
    Pairs the page numbers that Document Intelligence reports with the page texts found
    in its markdown. The markdown has a page-break marker between pages and none before
    the first page; a leading empty piece is tolerated.
    """
    pieces = split_markdown_pages(markdown)
    if len(pieces) == len(page_numbers) + 1 and not clean_page(pieces[0]):
        pieces = pieces[1:]
    if len(pieces) != len(page_numbers):
        raise SkillError(
            f"Document Intelligence returned {len(page_numbers)} pages but the markdown "
            f"has {len(pieces)} page pieces"
        )
    return list(zip(page_numbers, pieces))


def build_pages(numbered: list[tuple[int, str]]) -> tuple[list[dict], list[int]]:
    """
    Cleans every page. Pages with no text are dropped (they cannot be embedded) and
    their numbers returned; the others keep their real page number.
    """
    pages: list[dict] = []
    skipped: list[int] = []
    for number, raw in sorted(numbered, key=lambda item: item[0]):
        text = clean_page(raw)
        if text:
            pages.append({"page": number, "text": text})
        else:
            skipped.append(number)
    return pages, skipped
