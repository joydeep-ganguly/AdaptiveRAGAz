import pytest

from skills.docint import analyze_pdf
from skills.errors import SkillError


def fake_analyzer(calls):
    def analyze(data, page_range):
        calls.append(page_range)
        if page_range is None:
            numbers = list(range(1, 4))
        else:
            start, end = (int(x) for x in page_range.split("-"))
            numbers = list(range(start, end + 1))
        return numbers, "\n<!-- PageBreak -->\n".join(f"page {n}" for n in numbers)
    return analyze


def test_short_file_is_one_call_without_a_range():
    calls = []
    result = analyze_pdf(b"x", total_pages=3, chunk_pages=50, parallelism=3, analyze=fake_analyzer(calls))
    assert calls == [None]
    assert [(n, t.strip()) for n, t in result] == [(1, "page 1"), (2, "page 2"), (3, "page 3")]


def test_long_file_is_split_into_ranges_and_joined_in_order():
    calls = []
    result = analyze_pdf(b"x", total_pages=7, chunk_pages=3, parallelism=2, analyze=fake_analyzer(calls))
    assert sorted(calls) == ["1-3", "4-6", "7-7"]
    assert [n for n, _ in result] == [1, 2, 3, 4, 5, 6, 7]
    assert (result[4][0], result[4][1].strip()) == (5, "page 5")


def test_wrong_page_numbers_from_the_service_are_an_error():
    def analyze(data, page_range):
        return [1, 2], "a\n<!-- PageBreak -->\nb"

    with pytest.raises(SkillError):
        analyze_pdf(b"x", total_pages=7, chunk_pages=3, parallelism=1, analyze=analyze)
