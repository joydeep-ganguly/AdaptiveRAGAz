def test_register_creates_user(client):
    response = client.post(
        "/auth/register",
        json={"username": "newuser", "email": "newuser@example.com", "password": "testpass123"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "newuser"
    assert "hashed_password" not in body


def test_register_duplicate_username_fails(client):
    payload = {"username": "dupeuser", "email": "dupe1@example.com", "password": "testpass123"}
    client.post("/auth/register", json=payload)

    response = client.post(
        "/auth/register",
        json={"username": "dupeuser", "email": "dupe2@example.com", "password": "testpass123"},
    )
    assert response.status_code == 400


def test_login_with_correct_credentials_returns_token(client):
    client.post(
        "/auth/register",
        json={"username": "loginuser", "email": "login@example.com", "password": "testpass123"},
    )
    response = client.post("/auth/token", data={"username": "loginuser", "password": "testpass123"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_with_wrong_password_returns_401(client):
    client.post(
        "/auth/register",
        json={"username": "wrongpassuser", "email": "wrongpass@example.com", "password": "testpass123"},
    )
    response = client.post("/auth/token", data={"username": "wrongpassuser", "password": "incorrect"})
    assert response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user(client, auth_headers):
    response = client.get("/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"