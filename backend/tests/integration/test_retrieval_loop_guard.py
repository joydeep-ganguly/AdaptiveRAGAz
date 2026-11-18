from langgraph.graph import END, START, StateGraph

from app.agent.state import AgentState


def test_rewritting_count_is_kept_in_the_graph_state():
    """
    The retry limit (MAX_ITERATIONS) counts through `rewritting_count`. LangGraph drops
    any key a node returns that is not declared on the state, so without the field the
    counter never reaches the router and a query with no documents rewrites forever.
    """

    def bump(state):
        return {"rewritting_count": state.get("rewritting_count", 0) + 1}

    builder = StateGraph(AgentState)
    builder.add_node("first", bump)
    builder.add_node("second", bump)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)

    result = builder.compile().invoke({"messages": [], "rewritting_count": 0})

    assert result["rewritting_count"] == 2


def test_every_question_starts_with_fresh_retrieval_counters(client, auth_headers, fake_graph):
    """The checkpointer keeps state for the whole conversation, so each question must reset it."""
    conversation_id = client.post("/conversations", json={"title": "Chat"}, headers=auth_headers).json()["id"]

    client.post(f"/chat/{conversation_id}/query", json={"message": "First question"}, headers=auth_headers)
    client.post(f"/chat/{conversation_id}/query", json={"message": "Second question"}, headers=auth_headers)

    assert len(fake_graph.invocations) == 2
    for call in fake_graph.invocations:
        assert call["input"]["rewritting_count"] == 0
        assert call["input"]["rewritten_queries"] == []
        assert call["config"]["recursion_limit"] == 50
