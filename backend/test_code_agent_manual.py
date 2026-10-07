"""
Manual end-to-end check of the code-execution agent against Azure Blob (plan Step 4).
Needs `az login` and backend/.env (Gemini + E2B keys). Run from inside backend/:

    python test_code_agent_manual.py
"""

from langchain_core.messages import HumanMessage, ToolMessage

from app.agent.graph import create_adaptive_rag

agent = create_adaptive_rag()
config = {'configurable': {'thread_id': 'step4-manual'}}

query = (
    "Which data files are available? Then upload IMDB-Movie-Data.csv and "
    "create a line chart showing average ratings over years"
)
result = agent.invoke({'messages': [HumanMessage(query)]}, config=config)

for message in result['messages']:
    if isinstance(message, ToolMessage):
        print(f"[tool:{message.name}] {str(message.content)[:150]!r}")

print("\n=== FINAL ===")
print(result['messages'][-1].content)