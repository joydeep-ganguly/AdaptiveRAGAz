"""
Web search through the Foundry agent `adaptiverag-websearch` (Sub-Plan 2).

The agent owns the Web Search tool. This module sends it one query, statelessly
(no Foundry conversation), and turns the reply into the answer text plus the
URL citations Foundry attaches to it.

Auth is DefaultAzureCredential: `az login` locally, managed identity in Azure.

Import-time rule: importing this module must not read settings or touch Azure.
tests/conftest.py imports app.main -> agent graph -> web_search_node, which will
import this module. The client is therefore built lazily on first use.
"""

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from app.config import get_settings

logger = logging.getLogger(__name__)


class FoundryWebSearchError(Exception):
    """Foundry is not configured, unreachable, too slow, or returned nothing usable."""

class _DeadlineExceeded(Exception):
    """Internal: the Foundry call did not finish within the wall-clock deadline."""

@dataclass(frozen=True)
class WebSearchSource:
    title: str
    url: str


@dataclass(frozen=True)
class WebSearchResult:
    text: str
    sources: tuple[WebSearchSource, ...]


# --------------------------------------------------------------------------- #
# Configuration / client
# --------------------------------------------------------------------------- #
def is_configured() -> bool:
    """True when a Foundry project endpoint is set (empty or blank counts as not set)."""
    return bool((get_settings().foundry_project_endpoint or "").strip())


@lru_cache
def _get_openai_client():
    """Built once per process, on first use (never at import)."""
    endpoint = (get_settings().foundry_project_endpoint or "").strip()
    if not endpoint:
        raise FoundryWebSearchError("FOUNDRY_PROJECT_ENDPOINT is not configured")

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return project.get_openai_client()


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _get(obj, name, default=None):
    """Attribute access that also works for plain dicts (SDK objects and raw JSON both occur)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clean_title(title, url: str) -> str:
    """One line, no square brackets (they would break the markdown link); host name if empty."""
    cleaned = " ".join((title or "").replace("[", "").replace("]", "").split())
    return cleaned or urlparse(url).netloc or url


def _parse(response) -> WebSearchResult:
    """Answer text and de-duplicated URL citations from a Responses-API reply."""
    texts: list[str] = []
    sources: list[WebSearchSource] = []
    seen_urls: set[str] = set()

    for item in _get(response, "output", None) or []:
        if _get(item, "type") != "message":
            continue
        for part in _get(item, "content", None) or []:
            if _get(part, "type") != "output_text":
                continue

            text = (_get(part, "text", "") or "").strip()
            if text:
                texts.append(text)

            for annotation in _get(part, "annotations", None) or []:
                if _get(annotation, "type") != "url_citation":
                    continue
                url = (_get(annotation, "url", "") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                sources.append(
                    WebSearchSource(title=_clean_title(_get(annotation, "title", ""), url), url=url)
                )

    answer = "\n\n".join(texts).strip()
    if not answer:
        # Same text through the SDK's convenience property, if the structure had none.
        answer = (_get(response, "output_text", "") or "").strip()
    if not answer:
        raise FoundryWebSearchError("Foundry returned an empty answer")

    return WebSearchResult(text=answer, sources=tuple(sources))

# --------------------------------------------------------------------------- #
# Wall-clock deadline
# --------------------------------------------------------------------------- #
def _run_with_deadline(func, seconds):
    """
    Runs func() and returns its result, or raises _DeadlineExceeded after `seconds`.

    The OpenAI client's `timeout` is a per-phase network timeout (connect / read / write),
    not a total limit, so it cannot guarantee how long a call takes. The call therefore runs
    in a daemon thread. A thread cannot be killed: after a timeout it finishes in the
    background, its result is discarded, and it never blocks interpreter exit.
    """
    outcome = {}

    def target():
        try:
            outcome["value"] = func()
        except Exception as exc:  # handed back to the caller's thread below
            outcome["error"] = exc

    worker = threading.Thread(target=target, name="foundry-web-search", daemon=True)
    worker.start()
    worker.join(seconds)

    if worker.is_alive():
        raise _DeadlineExceeded()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def search(query: str) -> WebSearchResult:
    """
    Asks the Foundry web-search agent one question. Stateless: no conversation is
    created. Waits at most FOUNDRY_REQUEST_TIMEOUT_SECONDS in total. Any failure
    (not configured, auth, timeout, API error, empty answer) becomes
    FoundryWebSearchError; detail goes to the log, never the query.
    """
    query = (query or "").strip()
    if not query:
        raise FoundryWebSearchError("Empty search query")

    settings = get_settings()
    deadline = settings.foundry_request_timeout_seconds

    def call_foundry():
        # The client's own timeout is a second line of defence (it frees the connection
        # eventually); the deadline below is what bounds the caller. No automatic retries:
        # the caller falls back to DuckDuckGo instead.
        client = _get_openai_client().with_options(timeout=deadline, max_retries=0)
        return client.responses.create(
            extra_body={
                "agent_reference": {
                    "name": settings.foundry_web_search_agent_name,
                    "type": "agent_reference",
                }
            },
            input=query,
        )

    try:
        response = _run_with_deadline(call_foundry, deadline)
        return _parse(response)
    except FoundryWebSearchError:
        raise
    except _DeadlineExceeded as exc:
        logger.warning("Foundry web search did not finish within %s s", deadline)
        raise FoundryWebSearchError(f"Foundry web search timed out after {deadline} s") from exc
    except Exception as exc:  # single boundary: the node's fallback must always get its chance
        logger.warning("Foundry web search failed: %s", exc, exc_info=True)
        raise FoundryWebSearchError(
            f"Foundry web search request failed ({type(exc).__name__})"
        ) from exc


def format_answer(result: WebSearchResult) -> str:
    """Answer text, followed by a numbered markdown Sources list when there are sources."""
    if not result.sources:
        return result.text

    lines = []
    for number, source in enumerate(result.sources, start=1):
        url = source.url.replace(" ", "%20").replace(")", "%29")  # keep the markdown link intact
        lines.append(f"{number}. [{source.title}]({url})")

    return f"{result.text}\n\n**Sources**\n\n" + "\n".join(lines)