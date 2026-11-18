import pytest

from skills.errors import SkillError
from skills.filename import parse_filename


def test_annual_report_has_no_quarter():
    assert parse_filename("amazon 10-k 2023.pdf") == {
        "company_name": "amazon", "doc_type": "10-k", "fiscal_year": 2023, "fiscal_quarter": None,
    }


def test_quarterly_report():
    assert parse_filename("amazon 10-q q3 2024.pdf") == {
        "company_name": "amazon", "doc_type": "10-q", "fiscal_year": 2024, "fiscal_quarter": "q3",
    }


@pytest.mark.parametrize("name", [
    "", "amazon 10-k 2023.PDF", "amazon 10-k 2023", "Amazon 10-k 2023.pdf", "amazon 10-x 2023.pdf",
    "amazon 10-q q5 2024.pdf", "amazon 10-k abcd.pdf", "amazon 10-k 1800.pdf", "amazon 10-k +2023.pdf",
    "amazon  10-k 2023.pdf", "amazon 10-k 2023 extra more.pdf", "amazon.pdf", "amazon 10-k 02023.pdf",
])
def test_bad_names_are_refused(name):
    with pytest.raises(SkillError):
        parse_filename(name)
