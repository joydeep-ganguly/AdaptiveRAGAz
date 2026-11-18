import pytest

from skills.errors import SkillError
from skills.pages import build_pages, clean_page, number_pages, page_ranges, split_markdown_pages

MD = (
    '<!-- PageHeader="Header" -->\n\n# Title\n\ntext one\n\n<!-- PageNumber="1" -->\n\n'
    "<!-- PageBreak -->\n\n"
    "## Second\n\ntext two\n\n<!-- PageNumber=\"2\" -->\n\n"
    "<!--  PageBreak  -->\n\n"
    '<!-- PageNumber="3" -->\n'
)


def test_split_gives_one_piece_per_page():
    assert len(split_markdown_pages(MD)) == 3


def test_clean_page_removes_layout_comments_only():
    assert clean_page('<!-- PageHeader="x" -->\n\n# T\n\n\n\n\nbody\n<!-- PageNumber="1" -->') == "# T\n\nbody"
    assert "<!-- FigureContent" in clean_page('<!-- FigureContent="a chart" -->\ntext')


def test_empty_page_is_skipped_and_numbers_are_kept():
    numbered = number_pages([1, 2, 3], MD)
    pages, skipped = build_pages(numbered)
    assert [p["page"] for p in pages] == [1, 2]
    assert skipped == [3]
    assert pages[0]["text"].startswith("# Title")


def test_numbers_are_the_real_ones_for_a_page_range():
    pages, _ = build_pages(number_pages([51, 52], "a\n<!-- PageBreak -->\nb"))
    assert [p["page"] for p in pages] == [51, 52]


def test_leading_marker_is_tolerated():
    assert len(number_pages([1, 2], "<!-- PageBreak -->\na\n<!-- PageBreak -->\nb")) == 2


def test_mismatch_is_an_error():
    with pytest.raises(SkillError):
        number_pages([1, 2, 3], "a\n<!-- PageBreak -->\nb")


def test_page_ranges():
    assert page_ranges(111, 50) == [(1, 50), (51, 100), (101, 111)]
    assert page_ranges(9, 50) == [(1, 9)]
    assert page_ranges(100, 50) == [(1, 50), (51, 100)]
    with pytest.raises(SkillError):
        page_ranges(0, 50)
