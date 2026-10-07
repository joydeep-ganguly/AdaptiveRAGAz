"""
Manual smoke check of the Foundry web-search agent (Sub-Plan 2, Step 3).
Needs `az login` and FOUNDRY_PROJECT_ENDPOINT in backend/.env. Run from inside backend/:

    python test_foundry_web_search_manual.py
"""

import time

from app.agent.tools import foundry_web_search as fws

assert fws.is_configured(), "FOUNDRY_PROJECT_ENDPOINT is not set in backend/.env"

QUERY = "What are the top technology news headlines today?"

start = time.perf_counter()
result = fws.search(QUERY)
elapsed = time.perf_counter() - start

print(f"Answered in {elapsed:.1f} s; {len(result.text)} characters; {len(result.sources)} source(s)\n")
print(fws.format_answer(result))

assert result.text, "empty answer"
print("\nFoundry web search OK.")