# -----------------------------------------------------------------------------
# SQL Database Path (Agent-based)
# -----------------------------------------------------------------------------

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from app.agent.state import AgentState
from app.agent.nodes.rag_nodes import get_latest_user_query
from app.agent.llm import get_llm
from app.agent.tools.sql_tools import SCHEMA, ALL_SQL_TOOLS

llm = get_llm()


def sql_agent_node(state: AgentState):

    query = get_latest_user_query(state['messages'])

    system_prompt = f"""You are an expert SQL analyst working with an employees database.

                    Database Schema:
                    {SCHEMA}

                    Your workflow for answering questions:
                    1. Use `get_database_schema` first to understand available tables and columns (if needed)
                    2. Use `generate_sql_query` to create SQL based on the question
                    3. Use `execute_sql_query` to run the validated query
                    4. If there's an error, use `fix_sql_error` to correct it and try again (up to 3 times)

                    Rules:
                    - Always follow the workflow step by step
                    - If a query fails, use the fix tool and try again
                    - Provide clear, informative answers
                    - Be precise with table and column names
                    - Handle errors gracefully and try to fix them
                    - If you fail after 3 attempts, explain what went wrong

                    Remember:
                    - Always validate queries before executing them for safety.
                    - Provide clear, formatted results in MARKDOWN
                    - Use tables for structured data"""

    agent = create_agent(model=llm, tools=ALL_SQL_TOOLS, system_prompt=system_prompt)

    result = agent.invoke({'messages': [HumanMessage(query)]})

    print(f"[SQL] Agent completed")

    return {'messages': result['messages']}