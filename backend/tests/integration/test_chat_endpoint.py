def test_query_returns_fake_agent_reply(client, auth_headers, fake_graph):
    fake_graph.reply_text = "Amazon's 2023 revenue was $574.8 billion."

    conversation_id = client.post("/conversations", json={"title": "Chat"}, headers=auth_headers).json()["id"]

    response = client.post(
        f"/chat/{conversation_id}/query",
        json={"message": "What was Amazon's revenue in 2023?"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Amazon's 2023 revenue was $574.8 billion."


def test_query_persists_both_messages(client, auth_headers):
    conversation_id = client.post("/conversations", json={"title": "Chat"}, headers=auth_headers).json()["id"]

    client.post(
        f"/chat/{conversation_id}/query",
        json={"message": "Hello there"},
        headers=auth_headers,
    )

    messages = client.get(f"/conversations/{conversation_id}/messages", headers=auth_headers).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Hello there"


def test_query_against_nonexistent_conversation_returns_404(client, auth_headers):
    response = client.post("/chat/does-not-exist/query", json={"message": "Hello"}, headers=auth_headers)
    assert response.status_code == 404


def test_query_never_calls_the_real_agent_graph(client, auth_headers, fake_graph):
    """Confirms the test suite is actually isolated from the live LangGraph agent."""
    conversation_id = client.post("/conversations", json={"title": "Chat"}, headers=auth_headers).json()["id"]

    client.post(
        f"/chat/{conversation_id}/query",
        json={"message": "test message"},
        headers=auth_headers,
    )

    assert len(fake_graph.invocations) == 1
    assert fake_graph.invocations[0]["config"]["configurable"]["thread_id"] == conversation_id