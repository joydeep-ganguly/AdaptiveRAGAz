import threading
import time
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError

from app.agent.tools import foundry_web_search as fws

_REAL_GET_CLIENT = fws._get_openai_client  # handle on the real lru_cache'd function


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    settings = SimpleNamespace(
        foundry_project_endpoint="https://example.test/api/projects/p",
        foundry_web_search_agent_name="my-agent",
        foundry_request_timeout_seconds=7,
        web_search_fallback_enabled=True,
    )
    monkeypatch.setattr(fws, "get_settings", lambda: settings)
    _REAL_GET_CLIENT.cache_clear()
    yield settings
    _REAL_GET_CLIENT.cache_clear()


# ------------------------------------------------------------------ test doubles
def _citation(title, url, type="url_citation"):
    return SimpleNamespace(type=type, title=title, url=url, start_index=0, end_index=1)


def _message(text, annotations=()):
    part = SimpleNamespace(type="output_text", text=text, annotations=list(annotations))
    return SimpleNamespace(type="message", content=[part])


def _response(*items, output_text=""):
    return SimpleNamespace(output=list(items), output_text=output_text)


class _FakeOpenAI:
    """Mirrors the two calls the module makes: with_options(...) and responses.create(...)."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.options = None
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)

    def with_options(self, **options):
        self.options = options
        return self

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture()
def client(monkeypatch):
    fake = _FakeOpenAI(response=_response(_message("An answer.")))
    monkeypatch.setattr(fws, "_get_openai_client", lambda: fake)
    return fake


# ------------------------------------------------------------------ is_configured
@pytest.mark.parametrize("endpoint,expected", [(None, False), ("", False), ("   ", False), ("https://x", True)])
def test_is_configured(fake_settings, endpoint, expected):
    fake_settings.foundry_project_endpoint = endpoint
    assert fws.is_configured() is expected


# ----------------------------------------------------------------------- search
def test_search_returns_answer_and_deduplicated_sources_in_order(client):
    client.response = _response(
        SimpleNamespace(type="reasoning"),
        SimpleNamespace(type="web_search_call"),
        _message(
            "## Top headlines\n- one\n- two",
            [
                _citation("OpenAI will start watermarking", "https://msn.example/a"),
                _citation("TikTok shopping assistant", "https://tc.example/b"),
                _citation("Chipmaker lends startup billions", "https://cnbc.example/c"),
                _citation("OpenAI will start watermarking", "https://msn.example/a"),  # duplicate
            ],
        ),
    )

    result = fws.search("tech news")

    assert result.text == "## Top headlines\n- one\n- two"
    assert result.sources == (
        fws.WebSearchSource("OpenAI will start watermarking", "https://msn.example/a"),
        fws.WebSearchSource("TikTok shopping assistant", "https://tc.example/b"),
        fws.WebSearchSource("Chipmaker lends startup billions", "https://cnbc.example/c"),
    )


def test_search_sends_agent_reference_input_timeout_and_no_conversation(client):
    fws.search("  latest tech news  ")

    call = client.calls[0]
    assert call["extra_body"] == {"agent_reference": {"name": "my-agent", "type": "agent_reference"}}
    assert call["input"] == "latest tech news"
    assert "conversation" not in call
    assert client.options == {"timeout": 7, "max_retries": 0}


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_rejects_empty_query_without_calling_foundry(client, query):
    with pytest.raises(fws.FoundryWebSearchError):
        fws.search(query)
    assert client.calls == []


def test_search_without_annotations_has_no_sources(client):
    client.response = _response(_message("Plain answer."))
    assert fws.search("q").sources == ()


def test_search_skips_blank_urls_and_other_annotation_types(client):
    client.response = _response(
        _message(
            "Answer.",
            [
                _citation("No url", "   "),
                _citation("A file", "https://x.example/f", type="file_citation"),
                _citation("Good", "https://x.example/good"),
            ],
        )
    )
    assert fws.search("q").sources == (fws.WebSearchSource("Good", "https://x.example/good"),)


def test_search_cleans_titles(client):
    client.response = _response(
        _message(
            "Answer.",
            [
                _citation("  Two\n lines [with] brackets  ", "https://x.example/1"),
                _citation("", "https://www.example.org/path"),
                _citation(None, "https://y.example/2"),
            ],
        )
    )

    titles = [source.title for source in fws.search("q").sources]

    assert titles == ["Two lines with brackets", "www.example.org", "y.example"]


def test_search_joins_several_message_items(client):
    client.response = _response(_message("First."), _message("Second."))
    assert fws.search("q").text == "First.\n\nSecond."


def test_search_falls_back_to_output_text_property(client):
    client.response = _response(output_text="  Only the convenience text.  ")
    result = fws.search("q")
    assert result.text == "Only the convenience text."
    assert result.sources == ()


def test_search_accepts_plain_dict_responses(client):
    client.response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "From JSON.",
                        "annotations": [{"type": "url_citation", "title": "T", "url": "https://j.example"}],
                    }
                ],
            }
        ]
    }
    result = fws.search("q")
    assert result.text == "From JSON."
    assert result.sources == (fws.WebSearchSource("T", "https://j.example"),)


def test_search_empty_answer_raises(client):
    client.response = _response(SimpleNamespace(type="web_search_call"), _message("   "))
    with pytest.raises(fws.FoundryWebSearchError, match="empty"):
        fws.search("q")


@pytest.mark.parametrize(
    "error",
    [TimeoutError("boom secret"), ClientAuthenticationError("boom secret"), RuntimeError("boom secret")],
)
def test_search_wraps_any_failure_without_leaking_detail(client, error):
    client.error = error

    with pytest.raises(fws.FoundryWebSearchError) as excinfo:
        fws.search("q")

    assert "boom" not in str(excinfo.value)
    assert excinfo.value.__cause__ is error


@pytest.mark.parametrize("endpoint", [None, "", "   "])
def test_search_without_endpoint_raises_clear_error(fake_settings, endpoint):
    fake_settings.foundry_project_endpoint = endpoint
    with pytest.raises(fws.FoundryWebSearchError, match="FOUNDRY_PROJECT_ENDPOINT"):
        fws.search("q")


# ------------------------------------------------------------------------ client
def test_client_uses_endpoint_default_credential_and_is_cached(monkeypatch):
    created = []
    sentinel = object()

    class _FakeProject:
        def __init__(self, endpoint, credential=None):
            created.append((endpoint, credential))

        def get_openai_client(self):
            return sentinel

    monkeypatch.setattr(fws, "AIProjectClient", _FakeProject)
    monkeypatch.setattr(fws, "DefaultAzureCredential", lambda: "fake-credential")

    first = _REAL_GET_CLIENT()
    second = _REAL_GET_CLIENT()

    assert created == [("https://example.test/api/projects/p", "fake-credential")]
    assert first is sentinel and second is sentinel


# ------------------------------------------------------------------ format_answer
def test_format_answer_appends_numbered_sources():
    result = fws.WebSearchResult(
        text="The answer.",
        sources=(
            fws.WebSearchSource("First", "https://a.example/1"),
            fws.WebSearchSource("Second", "https://b.example/2"),
        ),
    )

    assert fws.format_answer(result) == (
        "The answer.\n\n**Sources**\n\n"
        "1. [First](https://a.example/1)\n"
        "2. [Second](https://b.example/2)"
    )


def test_format_answer_without_sources_is_just_the_text():
    assert fws.format_answer(fws.WebSearchResult(text="Only text.", sources=())) == "Only text."


def test_format_answer_keeps_markdown_links_intact_for_awkward_urls():
    result = fws.WebSearchResult(
        text="A.",
        sources=(fws.WebSearchSource("Wiki", "https://en.example/wiki/Foo_(bar) baz"),),
    )
    assert "(https://en.example/wiki/Foo_(bar%29%20baz)" in fws.format_answer(result)


# ------------------------------------------------------------------------ deadline
def test_search_enforces_a_wall_clock_deadline(client, fake_settings):
    release = threading.Event()

    def blocking_create(**kwargs):
        release.wait(timeout=5)
        return client.response

    client.responses = SimpleNamespace(create=blocking_create)
    fake_settings.foundry_request_timeout_seconds = 0.2

    started = time.perf_counter()
    try:
        with pytest.raises(fws.FoundryWebSearchError, match="timed out"):
            fws.search("q")
        assert time.perf_counter() - started < 2
    finally:
        release.set()  # let the abandoned worker thread finish


def test_run_with_deadline_returns_the_value():
    assert fws._run_with_deadline(lambda: 42, 1) == 42


def test_run_with_deadline_reraises_the_workers_exception():
    def boom():
        raise ValueError("inside the worker")

    with pytest.raises(ValueError, match="inside the worker"):
        fws._run_with_deadline(boom, 1)