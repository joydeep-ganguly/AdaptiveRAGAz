"""
The Function app keeps its own copy of the file-name rules (it cannot import the backend).
This test keeps the two parsers in agreement: parse_filename in
app/storage/rag_data_store.py and azure_functions/rag_skills/skills/filename.py.
"""

import sys
from pathlib import Path

import pytest

from app.storage.rag_data_store import InvalidRagDataFieldsError, parse_filename

SKILLS_DIR = Path(__file__).resolve().parents[3] / "azure_functions" / "rag_skills"


@pytest.fixture(scope="module")
def skill_parser():
    sys.path.insert(0, str(SKILLS_DIR))
    try:
        from skills.errors import SkillError
        from skills.filename import parse_filename as skill_parse
    finally:
        sys.path.remove(str(SKILLS_DIR))
    return skill_parse, SkillError


NAMES = [
    "amazon 10-k 2023.pdf",
    "amazon 10-q q1 2024.pdf",
    "amazon 10-q q4 2024.pdf",
    "apple 8-k q4 2023.pdf",
    "google 10-k 2023.pdf",
    "some-company other 2020.pdf",
    "co2 10-k 1950.pdf",
    "co2 10-k 2050.pdf",
    # not valid
    "", "amazon 10-k 2023.PDF", "amazon 10-k 2023", "Amazon 10-k 2023.pdf", "amazon 10-x 2023.pdf",
    "amazon 10-q q5 2024.pdf", "amazon 10-k abcd.pdf", "amazon 10-k 1949.pdf", "amazon 10-k 2051.pdf",
    "amazon 10-k +2023.pdf", "amazon  10-k 2023.pdf", "amazon 10-k 2023 extra more.pdf", "amazon.pdf",
    "amazon 10-k 02023.pdf", "am_azon 10-k 2023.pdf", "amazon 10-K 2023.pdf", "amazon 10-q Q1 2024.pdf",
]


@pytest.mark.parametrize("name", NAMES)
def test_both_parsers_agree(name, skill_parser):
    skill_parse, skill_error = skill_parser
    try:
        expected = parse_filename(name)
    except InvalidRagDataFieldsError:
        with pytest.raises(skill_error):
            skill_parse(name)
        return

    assert skill_parse(name) == {
        "company_name": expected.company_name,
        "doc_type": expected.doc_type,
        "fiscal_year": expected.fiscal_year,
        "fiscal_quarter": expected.fiscal_quarter,
    }
