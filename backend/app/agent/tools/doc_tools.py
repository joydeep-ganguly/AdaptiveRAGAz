from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.agent.retrieval.filters import extract_filters, generate_ranking_keywords
from app.agent.retrieval.vectorstore import search_docs
from app.agent.retrieval.ranking import rank_documents_by_keywords
from app.agent.debug_utils import write_debug_log


@tool
def retriever_docs(query: str, config: RunnableConfig, k: int = 5):
    """
    Retrieve relevant financial documents from ChromaDB.
    Extracts filters from query and retrieves matching documents.
    """
    print(f"\n[TOOL] retrieve_docs called")
    print(f"[QUERY] {query}")

    filters = extract_filters(query)
    ranking_keywords = generate_ranking_keywords(query)

    results = search_docs(query, filters, ranking_keywords, k=10 * k)
    docs = rank_documents_by_keywords(results, ranking_keywords, k=k)

    print(f"[RETRIEVED] {len(docs)} documents")

    if len(docs) == 0:
        return f"No ducuments found for the query: '{query}'. Try rephrasing query or use different filter."

    retrieved_text = []
    for i, doc in enumerate(docs, 1):
        doc_text = [f"--- Document {i} ---"]
        for key, value in doc.metadata.items():
            doc_text.append(f"{key}: {value}")
        doc_text.append(f"\nContent:\n{doc.page_content}")
        retrieved_text.append('\n'.join(doc_text))

    retrieved_text = '\n'.join(retrieved_text)

    write_debug_log(config, "retrieved_reranked_docs.md", retrieved_text)

    return retrieved_text