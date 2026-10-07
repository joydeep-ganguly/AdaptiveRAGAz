def test_create_conversation(client, auth_headers):
    response = client.post("/conversations", json={"title": "My chat"}, headers=auth_headers)
    assert response.status_code == 201
    assert response.json()["title"] == "My chat"


def test_list_conversations_only_shows_own(client, auth_headers):
    client.post("/conversations", json={"title": "Chat 1"}, headers=auth_headers)
    client.post("/conversations", json={"title": "Chat 2"}, headers=auth_headers)

    response = client.get("/conversations", headers=auth_headers)
    assert response.status_code == 200
    titles = {c["title"] for c in response.json()}
    assert titles == {"Chat 1", "Chat 2"}


def test_get_messages_for_nonexistent_conversation_returns_404(client, auth_headers):
    response = client.get("/conversations/does-not-exist/messages", headers=auth_headers)
    assert response.status_code == 404


def test_conversations_require_authentication(client):
    response = client.get("/conversations")
    assert response.status_code == 401


def test_delete_conversation(client, auth_headers):
    create_response = client.post("/conversations", json={"title": "To delete"}, headers=auth_headers)
    conversation_id = create_response.json()["id"]

    delete_response = client.delete(f"/conversations/{conversation_id}", headers=auth_headers)
    assert delete_response.status_code == 204

    get_response = client.get(f"/conversations/{conversation_id}/messages", headers=auth_headers)
    assert get_response.status_code == 404


def test_user_cannot_access_another_users_conversation(client):
    client.post("/auth/register", json={"username": "userA", "email": "a@example.com", "password": "testpass123"})
    token_a = client.post("/auth/token", data={"username": "userA", "password": "testpass123"}).json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}
    conversation_id = client.post("/conversations", json={"title": "A's chat"}, headers=headers_a).json()["id"]

    client.post("/auth/register", json={"username": "userB", "email": "b@example.com", "password": "testpass123"})
    token_b = client.post("/auth/token", data={"username": "userB", "password": "testpass123"}).json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    response = client.get(f"/conversations/{conversation_id}/messages", headers=headers_b)
    assert response.status_code == 404