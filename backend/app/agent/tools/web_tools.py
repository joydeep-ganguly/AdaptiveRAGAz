from ddgs import DDGS
from ddgs.exceptions import DDGSException
from langchain_core.tools import tool

@tool
def web_search(query:str, num_results: int = 10) -> str:
    """Use this tool whenever you need to access realtime or latest information.
        Search the web using DuckDuckGo.
    
    Args:
        query: Search query string
        num_results: Number of results to return (default: 5)
    
    Returns:
        Formatted search results with titles, descriptions, and URLs
    """

    try:
        results = DDGS().text(query=query, max_results=num_results, region='us-en')
    except DDGSException:
        # ddgs raises (instead of returning an empty list) when nothing is found or it is rate-limited.
        results = []

    if not results:
        return f"No results found for '{query}'. Do not search again; tell the user the web search returned nothing."
    
    formatted_results = [f"Search results for search query: '{query}'"]
    for i, result in enumerate(results, 1):
        title = result.get('title', 'No title')
        href = result.get('href', '')
        body = result.get('body', 'No description available')
        
        text = f"{i}. **{title}**\n   {body}\n   {href}"
        
        formatted_results.append(text)

    return "\n\n".join(formatted_results)
