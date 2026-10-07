from langgraph.graph import StateGraph, START, END

from app.agent.state import AgentState
from app.agent.router import route_question
from app.agent.checkpointer import get_checkpointer

from app.agent.nodes.rag_nodes import (
    retrieve_node,
    grade_documents_node,
    generate_node,
    transform_query_node,
    should_generate,
    check_answer_quality,
)
from app.agent.nodes.sql_agent_node import sql_agent_node
from app.agent.nodes.web_search_node import web_search_node
from app.agent.nodes.code_execution_node import code_execution_node


def create_adaptive_rag():
    builder = StateGraph(AgentState)

    builder.add_node('retrieve', retrieve_node)
    builder.add_node('grade_documents', grade_documents_node)
    builder.add_node('generate', generate_node)
    builder.add_node('transform_query', transform_query_node)

    builder.add_node('sql_agent', sql_agent_node)
    builder.add_node('web_search', web_search_node)
    builder.add_node('code_execution', code_execution_node)

    builder.add_conditional_edges(START, route_question, ['retrieve', 'sql_agent', 'web_search', 'code_execution'])

    builder.add_edge('retrieve', 'grade_documents')
    builder.add_edge('transform_query', 'retrieve')

    builder.add_conditional_edges('grade_documents', should_generate, ['transform_query', 'generate'])
    builder.add_conditional_edges('generate', check_answer_quality, ['generate', END, 'transform_query'])

    builder.add_edge('sql_agent', END)
    builder.add_edge('web_search', END)
    builder.add_edge('code_execution', END)

    graph = builder.compile(checkpointer=get_checkpointer())
    return graph