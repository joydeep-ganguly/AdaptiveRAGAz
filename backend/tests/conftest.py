import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from app.db.models import Base
from app.db.session import get_db_session
from app.deps import get_agent_graph
from app.main import app
from app.storage import code_data_store


@pytest.fixture()
def db_session():
    """
    A fresh, isolated in-memory SQLite database per test — never touches the
    real storage/sqlite/app.db. StaticPool keeps the same connection alive
    for the whole test (plain in-memory SQLite would otherwise give every
    new connection its own empty database).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class FakeGraph:
    """
    Stands in for the compiled LangGraph agent in tests, so no test ever
    calls the real Gemini/E2B/DuckDuckGo APIs. `reply_text` can be changed
    per-test; `invocations` lets a test assert on how it was called.
    `_messages` mirrors the real graph's accumulating checkpointed state so
    chat_service's get_state()-based turn-scoping logic works against it too.
    """
    def __init__(self, reply_text: str = "This is a fake agent reply."):
        self.reply_text = reply_text
        self.invocations = []
        self._messages: list = []

    def get_state(self, config=None):
        return SimpleNamespace(values={"messages": list(self._messages)})

    def invoke(self, input, config=None):
        self.invocations.append({"input": input, "config": config})
        self._messages.extend(input.get("messages", []))
        reply = AIMessage(content=self.reply_text)
        self._messages.append(reply)
        return {"messages": list(self._messages)}


@pytest.fixture()
def fake_graph():
    return FakeGraph()


@pytest.fixture()
def client(db_session, fake_graph):
    """
    A TestClient with the real database and real agent graph swapped out for
    test doubles. Deliberately NOT used as `with TestClient(app) as ...` —
    that would fire FastAPI's lifespan and build the real agent graph
    (Gemini/Chroma/E2B) on every test run. get_agent_graph is overridden
    below, so app.state.graph never needs to exist.
    """
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db
    app.dependency_overrides[get_agent_graph] = lambda: fake_graph

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(client):
    """
    Registers a user, logs in, and returns ready-to-use Authorization headers.
    Note: since this is always the FIRST user in this test's fresh database,
    it is automatically type="admin" (see auth_service.register_user). Use
    non_admin_auth_headers below for tests that specifically need a
    non-admin user.
    """
    client.post(
        "/auth/register",
        json={"username": "testuser", "email": "test@example.com", "password": "testpass123"},
    )
    response = client.post("/auth/token", data={"username": "testuser", "password": "testpass123"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def non_admin_auth_headers(client, auth_headers):
    """
    A second, definitely-non-admin user. Depends on auth_headers so `testuser`
    registers first (and becomes admin) — this fixture's user registers
    second, so it gets the ordinary "user" type.
    """
    client.post(
        "/auth/register",
        json={"username": "seconduser", "email": "second@example.com", "password": "testpass123"},
    )
    response = client.post("/auth/token", data={"username": "seconduser", "password": "testpass123"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_upload_dirs(tmp_path, monkeypatch):
    """
    Points the admin router's RAG upload destination at a throwaway tmp_path
    directory instead of the real backend/data/rag_data. Without this, the RAG
    upload endpoint would write real files into the actual project data folder
    on every test run — get_settings() is called directly inside admin.py's
    route body, not via Depends(), so app.dependency_overrides can't intercept
    it the way it does for the DB and the agent graph.
    (Code-data uploads go to Azure Blob — see fake_code_data_blob.)
    """
    rag_dir = tmp_path / "rag_data"

    fake_settings = SimpleNamespace(resolved_rag_data_dir=rag_dir)
    monkeypatch.setattr("app.api.routers.admin.get_settings", lambda: fake_settings)

    return SimpleNamespace(rag_dir=rag_dir)


@pytest.fixture()
def fake_code_data_blob(monkeypatch):
    """
    In-memory stand-in for the Azure container behind code_data_store, so the
    admin code-data endpoint is tested through the real validation and size-limit
    logic without ever touching Azure. The limit is 1 MB here to keep oversize
    tests small. Set `.error` to make the fake container raise on upload.
    """
    settings = SimpleNamespace(
        azure_storage_account_name="test-account",
        azure_storage_container="test-container",
        azure_storage_code_data_prefix="code_data/",
        code_data_max_upload_mb=1,
    )
    container = SimpleNamespace(blobs={}, uploads=[], error=None)

    def upload_blob(name, data, overwrite=False, content_settings=None):
        if container.error:
            raise container.error
        container.blobs[name] = data.read() if hasattr(data, "read") else data
        container.uploads.append(
            SimpleNamespace(name=name, overwrite=overwrite, content_type=content_settings.content_type)
        )

    container.upload_blob = upload_blob

    monkeypatch.setattr(code_data_store, "get_settings", lambda: settings)
    monkeypatch.setattr(code_data_store, "_get_container_client", lambda: container)
    return container