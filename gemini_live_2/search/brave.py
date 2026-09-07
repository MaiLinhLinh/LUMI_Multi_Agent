"""Minimal Brave Search adapter used only by backend-owned capabilities.

The adapter forwards the Plan Agent's query unchanged as ``q``. It adds only
fixed product policy: Vietnamese result preference, Vietnamese locale, strict
SafeSearch and one ranked result per request. It never calls an LLM or rewrites
provider text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_WEB_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_IMAGE_ENDPOINT = "https://api.search.brave.com/res/v1/images/search"
logger = logging.getLogger("lumi.search.brave")


class BraveSearchError(RuntimeError):
    """Raised when Brave cannot provide one schema-valid result."""


class BraveSearchConfigurationError(BraveSearchError):
    """Raised before a network call when the backend provider config is invalid."""


class _Response(Protocol):
    def read(self) -> bytes: ...

    def __enter__(self) -> "_Response": ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...


UrlOpener = Callable[..., _Response]


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BraveSearchError(f"Brave response is missing a non-empty {field}.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class BraveWebResult:
    """Provider fields retained verbatim after selecting Brave's first result."""

    title: str
    snippet: str
    source_url: str


@dataclass(frozen=True, slots=True)
class BraveImageResult:
    """Provider fields needed later by SearchResultStore/Compiler only."""

    remote_url: str
    source_url: str
    caption: str


class BraveSearchQuota:
    """In-memory per-Live-session guard; attachment to tool execution is AF3."""

    def __init__(self, *, max_requests_per_session: int) -> None:
        if isinstance(max_requests_per_session, bool) or not isinstance(max_requests_per_session, int):
            raise ValueError("max_requests_per_session must be an integer.")
        if max_requests_per_session < 1:
            raise ValueError("max_requests_per_session must be at least 1.")
        self._max_requests = max_requests_per_session
        self._used_by_session: dict[str, int] = {}

    def consume(self, session_id: str) -> None:
        safe_session_id = _text(session_id, field="session_id")
        used = self._used_by_session.get(safe_session_id, 0)
        if used >= self._max_requests:
            raise BraveSearchError("Search quota reached for this Live session.")
        self._used_by_session[safe_session_id] = used + 1

    def reset(self, session_id: str) -> None:
        self._used_by_session.pop(_text(session_id, field="session_id"), None)


class BraveSearchClient:
    """Synchronous, dependency-free Brave API client with a fixed safe policy."""

    def __init__(self, *, api_key: str, timeout_seconds: float, opener: UrlOpener = urlopen) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise BraveSearchConfigurationError("BRAVE_SEARCH_API_KEY is not configured.")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise BraveSearchConfigurationError("BRAVE_SEARCH_TIMEOUT_SECONDS must be a positive number.")
        if timeout_seconds <= 0:
            raise BraveSearchConfigurationError("BRAVE_SEARCH_TIMEOUT_SECONDS must be a positive number.")
        self._api_key = api_key.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def search_web(self, query: str) -> BraveWebResult:
        payload = self._search(_WEB_ENDPOINT, query)
        web = payload.get("web")
        if not isinstance(web, Mapping):
            raise BraveSearchError("Brave web search returned no web results.")
        results = web.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise BraveSearchError("Brave web search returned no web results.")
        result = results[0]
        logger.info(
            "[BRAVE_WEB_RESULT_SCHEMA] result_keys=%s required_candidates=%s",
            sorted(str(key) for key in result),
            {
                key: ("present" if isinstance(result.get(key), str) and result[key].strip() else "missing")
                for key in ("title", "description", "url")
            },
        )
        return BraveWebResult(
            title=_text(result.get("title"), field="web result title"),
            snippet=_text(result.get("description"), field="web result description"),
            source_url=_text(result.get("url"), field="web result url"),
        )

    def search_image(self, query: str) -> BraveImageResult:
        payload = self._search(_IMAGE_ENDPOINT, query)
        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise BraveSearchError("Brave image search returned no image results.")
        result = results[0]
        properties = result.get("properties")
        if not isinstance(properties, Mapping):
            raise BraveSearchError("Brave image search returned an image without properties.")
        logger.info(
            "[BRAVE_IMAGE_RESULT_SCHEMA] result_keys=%s properties_keys=%s source_candidates=%s",
            sorted(str(key) for key in result),
            sorted(str(key) for key in properties),
            {
                key: ("present" if isinstance(result.get(key), str) and result[key].strip() else "missing")
                for key in ("page_url", "url", "source_url", "source")
            },
        )
        return BraveImageResult(
            remote_url=_text(properties.get("url"), field="image result URL"),
            source_url=_text(result.get("url"), field="image result source URL"),
            caption=_text(result.get("title"), field="image result title"),
        )

    def _search(self, endpoint: str, query: str) -> Mapping[str, Any]:
        safe_query = _text(query, field="query")
        if len(safe_query) > 400 or len(safe_query.split()) > 50:
            raise BraveSearchError("query exceeds Brave's 400-character / 50-word limit.")
        request = Request(
            endpoint + "?" + urlencode({
                "q": safe_query,
                "count": "1",
                "country": "ALL",
                "search_lang": "vi",
                # Brave does not offer a Vietnamese UI locale. Search results
                # can still prefer Vietnamese through ``search_lang``.
                "ui_lang": "en-US",
                "safesearch": "strict",
                "spellcheck": "false",
            }),
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            # urllib keeps the provider's diagnostic body on the HTTPError.  It
            # is essential for distinguishing invalid request parameters from a
            # product/subscription restriction, but must never include headers
            # (and therefore never the subscription token).
            error_body = _error_body(exc)
            logger.warning(
                "[BRAVE_SEARCH_HTTP_ERROR] status=%s endpoint=%s body=%s",
                exc.code,
                endpoint,
                error_body,
            )
            raise BraveSearchError(f"Brave Search returned HTTP {exc.code}.") from exc
        except URLError as exc:
            raise BraveSearchError("Brave Search request failed.") from exc
        except TimeoutError as exc:
            raise BraveSearchError("Brave Search timed out.") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BraveSearchError("Brave Search returned invalid JSON.") from exc
        if not isinstance(decoded, Mapping):
            raise BraveSearchError("Brave Search returned a non-object JSON payload.")
        return decoded


def _error_body(error: HTTPError, *, limit: int = 1_000) -> str:
    """Return a bounded, header-free provider diagnostic for server logs."""

    try:
        raw = error.read(limit + 1)
    except OSError:
        return "<unavailable>"
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception:  # pragma: no cover - bytes decoding is defensive only.
        return "<unreadable>"
    if not text:
        return "<empty>"
    if len(raw) > limit:
        return f"{text[:limit]}…<truncated>"
    return text
