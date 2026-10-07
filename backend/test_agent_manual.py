from langchain_core.messages import HumanMessage

from app.agent.graph import create_adaptive_rag

agent = create_adaptive_rag()

config = {'configurable': {'thread_id': 'demo1'}}

# 1. Financial document retrieval (self-RAG path)
query = "show me amazon's revenue in 2023"
result = agent.invoke({'messages': [HumanMessage(query)]}, config=config)
print("\n=== RETRIEVE ===")
result['messages'][-1].pretty_print()

# 2. Web search path
result = agent.invoke({'messages': [HumanMessage("What is the latest AI news?")]}, config=config)
print("\n=== WEB SEARCH ===")
result['messages'][-1].pretty_print()

# # 3. SQL agent path
result = agent.invoke({'messages': [HumanMessage("How many employees are there?")]}, config=config)
print("\n=== SQL AGENT ===")
print(result['messages'][-1].content)

# # 4. SQL agent path (aggregation)
result = agent.invoke({'messages': [HumanMessage("show me average salary department wise")]}, config=config)
print("\n=== SQL AGENT (aggregation) ===")
print(result['messages'][-1].content)

# # 5. Code execution path
result = agent.invoke(
    {'messages': [HumanMessage("Upload IMDB-Movie-Data.csv and create a line chart showing average ratings over years")]},
    config=config,
)
print("\n=== CODE EXECUTION ===")
print(result['messages'][-1].content)