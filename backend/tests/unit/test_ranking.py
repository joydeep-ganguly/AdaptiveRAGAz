from types import SimpleNamespace

from app.agent.retrieval.ranking import extract_headings_with_context, rank_documents_by_keywords


def _doc(text):
    """Minimal stand-in for a langchain Document — rank_documents_by_keywords only reads .page_content."""
    return SimpleNamespace(page_content=text)


def test_extract_headings_with_context_pairs_heading_with_next_paragraph():
    text = "## Revenue\n\nAmazon's revenue grew 12% year over year.\n\n## Risks\n\nSupply chain risk increased."
    chunks = extract_headings_with_context(text)
    assert chunks == [
        "## Revenue\n\nAmazon's revenue grew 12% year over year.",
        "## Risks\n\nSupply chain risk increased.",
    ]


def test_extract_headings_with_context_no_headings_returns_empty():
    text = "Just a plain paragraph with no markdown headings at all."
    assert extract_headings_with_context(text) == []


def test_rank_documents_by_keywords_orders_most_relevant_first():
    docs = [
        _doc("## Risks\n\nSupply chain and logistics risk discussion."),
        _doc("## Revenue\n\nAmazon revenue grew significantly this quarter."),
        _doc("## Employees\n\nHeadcount details for the workforce."),
    ]
    ranked = rank_documents_by_keywords(docs, keywords=["amazon", "revenue"], k=2)
    assert len(ranked) == 2
    assert ranked[0].page_content.startswith("## Revenue")


def test_rank_documents_by_keywords_returns_input_unchanged_when_no_keywords():
    docs = [_doc("## Revenue\n\nSome content.")]
    assert rank_documents_by_keywords(docs, keywords=[], k=5) == docs