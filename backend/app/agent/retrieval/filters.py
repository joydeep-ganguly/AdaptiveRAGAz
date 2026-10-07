from app.agent.schemas import ChunkMetadata, RankingKeywords
from app.agent.llm import get_llm

llm = get_llm()


def extract_filters(user_query:str):

  llm_structured = llm.with_structured_output(ChunkMetadata)

  prompt = f"""Extract metadata filters from the query. Return None for fields not mentioned.

            USER QUERY: {user_query}

            COMPANY MAPPINGS:
            - Amazon/AMZN -> amazon
            - Google/Alphabet/GOOGL/GOOG -> google
            - Apple/AAPL -> apple
            - Microsoft/MSFT -> microsoft
            - Tesla/TSLA -> tesla
            - Nvidia/NVDA -> nvidia
            - Meta/Facebook/FB -> meta

            DOC TYPE:
            - Annual report -> 10-k
            - Quarterly report -> 10-q
            - Current report -> 8-k

            EXAMPLES:
            "Amazon Q3 2024 revenue" -> {{"company_name": "amazon", "doc_type": "10-q", "fiscal_year": 2024, "fiscal_quarter": "q3"}}
            "Apple 2023 annual report" -> {{"company_name": "apple", "doc_type": "10-k", "fiscal_year": 2023}}
            "Tesla profitability" -> {{"company_name": "tesla"}}

            If only year is mentioned consider Annual Report

            Extract metadata:
            """
  
  metadata = llm_structured.invoke(prompt)
  filters = metadata.model_dump(exclude_none=True)

  return filters


# ##### Ranking keywords

def generate_ranking_keywords(user_query: str):

    prompt = f"""Generate EXACTLY 5 financial keywords from SEC filings terminology.

              USER QUERY: {user_query}

              USE EXACT TERMS FROM 10-K/10-Q FILINGS:

              STATEMENT HEADINGS:
              "consolidated statements of operations", "consolidated balance sheets", "consolidated statements of cash flows", "consolidated statements of stockholders equity"

              INCOME STATEMENT:
              "revenue", "net revenue", "cost of revenue", "gross profit", "operating income", "net income", "earnings per share"

              BALANCE SHEET:
              "total assets", "cash and cash equivalents", "total liabilities", "stockholders equity", "working capital", "long-term debt"

              CASH FLOWS:
              "cash flows from operating activities", "net cash provided by operating activities", "cash flows from investing activities", "free cash flow", "capital expenditures"

              RULES:
              - Return EXACTLY 5 keywords
              - Use exact phrases from SEC filings
              - Match query topic (revenue -> revenue terms, cash -> cash flow terms)
              - Use "cash flows" (plural), "stockholders equity"

              EXAMPLES:
              "revenue analysis" -> ["revenue", "net revenue", "total revenue", "consolidated statements of operations", "net sales"]
              "cash flow performance" -> ["consolidated statements of cash flows", "cash flows from operating activities", "net cash provided by operating activities", "free cash flow", "operating activities"]
              "balance sheet strength" -> ["consolidated balance sheets", "total assets", "stockholders equity", "cash and cash equivalents", "long-term debt"]

              Generate EXACTLY 5 keywords:
              """
    
    llm_structured = llm.with_structured_output(RankingKeywords)
    result = llm_structured.invoke(prompt)

    return result.keywords