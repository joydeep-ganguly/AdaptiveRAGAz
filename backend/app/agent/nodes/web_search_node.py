# -----------------------------------------------------------------------------
# Web Search Path
#
# Primary:  the Foundry agent `adaptiverag-websearch`, which owns its own web-search tool.
# Fallback: the original DuckDuckGo agent, used when Foundry is not configured or fails.
# -----------------------------------------------------------------------------

import logging
import time

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.state import AgentState
from app.agent.nodes.rag_nodes import get_latest_user_query
from app.agent.llm import get_llm
from app.agent.tools import foundry_web_search
from app.agent.tools.web_tools import web_search
from app.config import get_settings

logger = logging.getLogger(__name__)

llm = get_llm()

UNAVAILABLE_MESSAGE = "Web search is temporarily unavailable. Please try again later."

# Prompt of the DuckDuckGo fallback agent. The Foundry agent keeps its own copy of
# these instructions inside Foundry, so the two can drift apart.
FALLBACK_SYSTEM_PROMPT = """You are a helpful web search assistant. Be concise and accurate.

                    **Workflow:**
                    1. Use web_search tool to find relevant information
                    2. Synthesize information from multiple sources
                    3. Provide a clear, comprehensive answer

                    **OUTPUT FORMAT:**
                    Write a clear answer (150-250 words) in **MARKDOWN** format:
                    - Use ## headings for sections
                    - Use **bold** for emphasis
                    - Use bullet points for key facts

                    **GUIDELINES:**
                    - Base your answer on web search results
                    - Synthesize information from multiple sources
                    - Be factual and objective"""


def _duckduckgo_answer(query: str):
    """The original DuckDuckGo ReAct agent, unchanged: returns its full message list."""
    agent = create_agent(model=llm, tools=[web_search], system_prompt=FALLBACK_SYSTEM_PROMPT)

    result = agent.invoke({'messages': [HumanMessage(query)]})

    print(f"[WEB] DuckDuckGo agent is completed")

    return result['messages']

def _safe_duckduckgo_answer(query: str):
    """Run the DuckDuckGo agent; any failure becomes the unavailable message instead of crashing the graph."""
    try:
        return _duckduckgo_answer(query)
    except Exception as exc:
        logger.warning("DuckDuckGo fallback failed (%s): %s", type(exc).__name__, exc)
        print(f"[WEB] DuckDuckGo failed ({type(exc).__name__})")
        return [AIMessage(content=UNAVAILABLE_MESSAGE)]


def web_search_node(state: AgentState):

    query = get_latest_user_query(state['messages'])

    # No Foundry endpoint configured (e.g. local run without Azure): DuckDuckGo only.
    if not foundry_web_search.is_configured():
        print("[WEB] Foundry is not configured, using DuckDuckGo")
        return {'messages': _safe_duckduckgo_answer(query)}

    started = time.perf_counter()
    try:
        result = foundry_web_search.search(query)
    except foundry_web_search.FoundryWebSearchError as exc:
        logger.warning("Foundry web search failed after %.1f s: %s", time.perf_counter() - started, exc)

        if get_settings().web_search_fallback_enabled:
            print("[WEB] Foundry failed, falling back to DuckDuckGo")
            return {'messages': _safe_duckduckgo_answer(query)}

        print("[WEB] Foundry failed and the fallback is disabled")
        return {'messages': [AIMessage(content=UNAVAILABLE_MESSAGE)]}

    print(f"[WEB] Foundry agent is completed ({len(result.sources)} sources) in {time.perf_counter() - started:.1f} s")
    
    # Only the answer is returned: the user's question is already in the state.
    return {'messages': [AIMessage(content=foundry_web_search.format_answer(result))]}