from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from app.agent.nodes import code_execution_node
from app.agent.prompts import CODE_EXECUTION_PROMPT
from app.agent.tools import code_tools
from app.storage import code_data_store

CONFIG = {"configurable": {"thread_id": "conv-1"}}

FILES = {"titanic.csv": b"a,b\n1,2\n", "IMDB-Movie-Data.csv": b"x" * 2048}


@pytest.fixture()
def store(monkeypatch):
    """Fake blob store; download() keeps the real filename validation."""
    def fake_download(name):
        code_data_store.validate_filename(name)
        if name not in FILES:
            raise code_data_store.CodeDataFileNotFoundError(f"'{name}' was not found in code data storage")
        return FILES[name]

    def fake_list_files():
        return [code_data_store.CodeDataFile(n, len(d)) for n, d in FILES.items()]

    monkeypatch.setattr(code_data_store, "download", fake_download)
    monkeypatch.setattr(code_data_store, "list_files", fake_list_files)


@pytest.fixture()
def sandbox(monkeypatch):
    fake = SimpleNamespace(written=[], requested_for=[])

    def _write(path, data):
        fake.written.append((path, data))
        return SimpleNamespace(path=f"/home/user/{path}")

    fake.files = SimpleNamespace(write=_write)

    def _get_sandbox(conversation_id):
        fake.requested_for.append(conversation_id)
        return fake

    monkeypatch.setattr(code_tools, "get_sandbox", _get_sandbox)
    return fake


# ------------------------------------------------------------------ upload_file
def test_upload_file_copies_blob_bytes_into_the_sandbox(store, sandbox):
    result = code_tools.upload_file.invoke({"file_name": "titanic.csv"}, config=CONFIG)

    assert "File uploaded successfully" in result
    assert "/home/user/data/titanic.csv" in result
    assert sandbox.written == [("data/titanic.csv", b"a,b\n1,2\n")]
    assert sandbox.requested_for == ["conv-1"]


def test_upload_file_tolerates_a_leading_slash(store, sandbox):
    code_tools.upload_file.invoke({"file_name": "/titanic.csv"}, config=CONFIG)
    assert sandbox.written[0][0] == "data/titanic.csv"


def test_upload_file_unknown_file_returns_error_and_starts_no_sandbox(store, sandbox):
    result = code_tools.upload_file.invoke({"file_name": "missing.csv"}, config=CONFIG)

    assert result.startswith("Error :")
    assert "list_code_data_files" in result
    assert sandbox.requested_for == []


@pytest.mark.parametrize("bad_name", ["../titanic.csv", "data/titanic.csv", "notes.txt"])
def test_upload_file_rejects_invalid_names_and_starts_no_sandbox(store, sandbox, bad_name):
    result = code_tools.upload_file.invoke({"file_name": bad_name}, config=CONFIG)

    assert result.startswith("Error :")
    assert sandbox.requested_for == []


def test_upload_file_reports_storage_errors(store, sandbox, monkeypatch):
    def failing_download(name):
        raise code_data_store.CodeDataStorageError("Azure Blob Storage request failed while downloading 'x'")

    monkeypatch.setattr(code_data_store, "download", failing_download)

    result = code_tools.upload_file.invoke({"file_name": "titanic.csv"}, config=CONFIG)

    assert result.startswith("Error :")
    assert sandbox.requested_for == []


# ------------------------------------------------------------ list_code_data_files
def test_list_code_data_files_lists_names_and_sizes(store):
    result = code_tools.list_code_data_files.invoke({})

    assert "- titanic.csv (8 bytes)" in result
    assert "- IMDB-Movie-Data.csv (2,048 bytes)" in result


def test_list_code_data_files_filters_case_insensitively(store):
    result = code_tools.list_code_data_files.invoke({"name_filter": "imdb"})

    assert "IMDB-Movie-Data.csv" in result
    assert "titanic.csv" not in result


def test_list_code_data_files_no_match(store):
    assert code_tools.list_code_data_files.invoke({"name_filter": "zzz"}) == "No matching data files found."


def test_list_code_data_files_reports_storage_errors(monkeypatch):
    def failing_list():
        raise code_data_store.CodeDataStorageError("AZURE_STORAGE_ACCOUNT_NAME is not configured")

    monkeypatch.setattr(code_data_store, "list_files", failing_list)

    result = code_tools.list_code_data_files.invoke({})
    assert result.startswith("Error :")
    assert "AZURE_STORAGE_ACCOUNT_NAME" in result


# ---------------------------------------------------------------- node + prompt
def test_node_uses_blob_tools_and_no_filesystem_middleware(monkeypatch):
    captured = {}

    class _FakeAgent:
        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            return {"messages": ["done"]}

    def _fake_create_agent(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeAgent()

    monkeypatch.setattr(code_execution_node, "create_agent", _fake_create_agent)

    result = code_execution_node.code_execution_node(
        {"messages": [HumanMessage("analyze titanic.csv")]}, CONFIG
    )

    assert {t.name for t in captured["kwargs"]["tools"]} == {
        "list_code_data_files", "upload_file", "run_python_code",
    }
    assert "middleware" not in captured["kwargs"]
    assert captured["config"] is CONFIG
    assert result == {"messages": ["done"]}


def test_prompt_describes_the_blob_tools_not_local_search():
    assert "glob_search" not in CODE_EXECUTION_PROMPT
    assert "./data" not in CODE_EXECUTION_PROMPT
    for tool_name in ("list_code_data_files", "upload_file", "run_python_code"):
        assert tool_name in CODE_EXECUTION_PROMPT