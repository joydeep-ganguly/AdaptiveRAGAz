"""
Manual end-to-end check of the web-search route (Sub-Plan 2, Step 4).
Prints how long each graph node takes, to show where the time goes.
Needs `az login` and backend/.env (Gemini + Foundry settings). Run from inside backend/:

    python test_web_search_agent_manual.py
"""

import time

from langchain_core.messages import HumanMessage

from app.agent.graph import create_adaptive_rag
from app.config import get_settings

QUERY = "What are the top technology news headlines today?"


def _text(message) -> str:
    """Readable text of a message (Gemini sometimes returns a list of content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            return "\n".join(parts)
    return str(content)


def ask(label: str, thread_id: str) -> None:
    agent = create_adaptive_rag()
    config = {'configurable': {'thread_id': thread_id}}

    print(f"\n--- {label}: per-node timing ---")
    start = time.perf_counter()
    previous = start
    final = None

    for update in agent.stream({'messages': [HumanMessage(QUERY)]}, config=config, stream_mode="updates"):
        now = time.perf_counter()
        for node_name, payload in update.items():
            print(f"[{now - start:6.1f} s] node '{node_name}' finished (+{now - previous:.1f} s)")
            if payload and payload.get('messages'):
                final = payload['messages'][-1]
        previous = now

    print(f"\n=== {label} ({time.perf_counter() - start:.1f} s total) ===")
    print(_text(final) if final is not None else "(no answer)")


# 1. Foundry agent: expect a markdown answer ending with a numbered Sources list
ask("Foundry", "step4-foundry")

# 2. Fallback path: pretend Foundry is not configured, so the DuckDuckGo agent answers
get_settings().foundry_project_endpoint = ""
ask("DuckDuckGo fallback", "step4-fallback")