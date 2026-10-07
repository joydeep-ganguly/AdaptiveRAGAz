from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agent.state import AgentState
from app.agent.nodes.rag_nodes import get_latest_user_query
from app.agent.llm import get_llm
from app.agent.tools.code_tools import list_code_data_files, upload_file, run_python_code
from app.agent.prompts import CODE_EXECUTION_PROMPT

llm = get_llm()


def code_execution_node(state: AgentState, config: RunnableConfig):

    query = get_latest_user_query(state['messages'])

    agent = create_agent(
        model=llm,
        tools=[list_code_data_files, upload_file, run_python_code],
        system_prompt=CODE_EXECUTION_PROMPT,
    )

    # Passing `config` through propagates thread_id (== conversation_id) to the
    # tools above, so each conversation gets its own E2B sandbox.
    result = agent.invoke({'messages': [HumanMessage(query)]}, config=config)

    print(f"[CODE] Agent is completed")

    return {'messages': result['messages']}