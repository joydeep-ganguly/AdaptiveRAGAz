from langchain_chroma import Chroma

from app.config import get_settings
from app.agent.llm import get_embeddings

# configurations
COLLECTION_NAME = "financial_docs"

_settings = get_settings()
embeddings = get_embeddings()

vector_store = Chroma(
  collection_name=COLLECTION_NAME,
  embedding_function=embeddings,
  persist_directory=str(_settings.resolved_chroma_dir)
)


def build_search_kwargs(filters, ranking_keywords, k=3):

  search_kwargs = {'k': k, 'fetch_k': k*20}

  if filters:
    if len(filters) == 1:
      search_kwargs['filter'] = filters    # filters = {'company_name' : 'amazon'}
    else:
      # filters = {'company_name' : 'amazon', 'doc_type' : '10-k'}
      # [{'company_name' : 'amazon'},{'doc_type' : '10-k'}]
      filters_conditions = [{k:v} for k,v in filters.items()]
      search_kwargs['filter'] = {'$and': filters_conditions}

  # Add document content filters using ranking keywords
  if ranking_keywords:
    if len(ranking_keywords) == 1:
      search_kwargs['where_document'] = {'$contains': ranking_keywords[0]}
    else:
      search_kwargs['where_document'] = {
        '$or': [{'$contains' : keyword} for keyword in ranking_keywords]
      }

  return search_kwargs

def search_docs(query, filters={}, ranking_keywords=[], k=3):
    """
      Search documents with metadata and content filters.
      
      Args:
          query (str): Search query text
          filters (dict): Metadata filters (e.g., {"company_name": "amazon", "fiscal_year": 2023})
          ranking_keywords (list): Keywords for content filtering (documents must contain at least one)
          k (int): Number of results (default: 5)
      
      Returns:
          list: Matching Document objects
      
      Example:
          docs = search_docs(
              query="Analyze cash flow",
              filters={"company_name": "amazon", "doc_type": "10-k"},
              ranking_keywords=["cash flow", "liquidity"],
              k=10
          )
      """

    search_kwargs = build_search_kwargs(filters, ranking_keywords, k)

    retriever = vector_store.as_retriever(
        search_type='mmr',
        search_kwargs = search_kwargs           #{'k': k, 'fetch_k': k*20}
    )

    return retriever.invoke(query)

