# -----------------------------------------------------------------------------
# Query Analysis & Routing
# -----------------------------------------------------------------------------

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState, RouterQuery
from app.agent.nodes.rag_nodes import get_latest_user_query
from app.agent.llm import get_llm


def route_question(state: AgentState):

    query = get_latest_user_query(state['messages'])

    print(f"[QUERY] {query}")

    llm_router = get_llm().with_structured_output(RouterQuery)

    system_prompt = """You are an expert at routing user questions to the appropriate datasource.

                    **Datasources:**
                    1. **retrieve**: Financial documents (10-K, 10-Q, 8-K reports) about companies like Amazon, Apple, Google
                    - Questions about: revenue, operating income, cash flow, segments, financial metrics, quarterly/annual performance
                    - Examples: "What was Amazon's revenue in 2023?", "Compare Apple vs Google Q1 2024 revenue"

                    2. **sql_agent**: Employee database with structured data
                    - Questions about: employees, departments, salaries, managers, job titles, hiring dates
                    - Examples: "Who is the CFO?", "List employees in Finance", "What is John's salary?"

                    3. **web_search**: General knowledge outside our databases
                    - Questions about: current events, general facts, topics unrelated to our data
                    - Examples: "What is inflation rate?", "How does RAG work?", "Capital of France?"

                    4. **code_execution**: Data analysis and chart creation against csv and excel files
                    - Questions about: average, ratio, comparison, show bar/line chart related to uploaded csv or excel files
                    - Examples: "Analyze Google's cashflow","Calculate average rate","Create a bar chart"

                    **Decision Logic:**
                    - Check for financial terms (revenue, income, cash flow, quarterly, annual, 10-K, 10-Q) -> retrieve
                    - Check for company names in our database (amazon, apple, google) -> retrieve
                    - Check for employee-related terms (employee, department, salary, CFO, manager) -> sql_agent
                    - Check for analysis, calculations, chart creation (table, dataset) -> code_execution
                    - Everything else -> web_search
                    Return the datasource field only."""

    messages = [SystemMessage(system_prompt), HumanMessage(query)]
    response = llm_router.invoke(messages)

    datasource = response.datasource

    print(f"[DESICION] Routing to {datasource}")

    return datasource