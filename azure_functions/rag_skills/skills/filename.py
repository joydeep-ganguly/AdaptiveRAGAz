"""
File-name convention: '{company} {doc_type} [{quarter}] {year}.pdf'.

Same rules as parse_filename in backend/app/storage/rag_data_store.py (strict: the
name must be exactly what the builder would produce). This module must stay free of
Azure imports; backend/tests/unit/test_rag_skills_filename_parity.py checks that
both parsers agree.
"""

import os
import re

from skills.errors import SkillError

DOC_TYPES = ("10-k", "10-q", "8-k", "other")
QUARTERS = ("q1", "q2", "q3", "q4")
MIN_FISCAL_YEAR = 1950
MAX_FISCAL_YEAR = 2050

_COMPANY = re.compile(r"[a-z0-9\-]+")
_SHAPE = "'{company} {doc_type} [{quarter}] {year}.pdf'"


def parse_filename(name: str) -> dict:
    base, extension = os.path.splitext(name or "")
    parts = base.split(" ")
    if extension != ".pdf" or len(parts) not in (3, 4):
        raise SkillError(f"'{name}' does not match {_SHAPE}")

    quarter = parts[2] if len(parts) == 4 else None
    company, doc_type, year_text = parts[0], parts[1], parts[-1]

    try:
        year = int(year_text)
    except ValueError:
        raise SkillError(f"'{name}' does not match {_SHAPE}") from None

    valid = (
        _COMPANY.fullmatch(company) is not None
        and doc_type in DOC_TYPES
        and (quarter is None or quarter in QUARTERS)
        and MIN_FISCAL_YEAR <= year <= MAX_FISCAL_YEAR
    )
    rebuilt = " ".join([company, doc_type] + ([quarter] if quarter else []) + [str(year)]) + ".pdf"
    if not valid or rebuilt != name:
        raise SkillError(f"'{name}' does not match {_SHAPE}")

    return {
        "company_name": company,
        "doc_type": doc_type,
        "fiscal_year": year,
        "fiscal_quarter": quarter,
    }
