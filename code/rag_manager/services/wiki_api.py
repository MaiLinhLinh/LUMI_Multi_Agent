"""Wikipedia service client."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

import wikipedia

from rag_manager.services.http_client import ServiceResponse


WIKI_SOURCE = "wiki"


class WikiTopicNotFound(Exception):
    """Raised when Wikipedia has no usable result for a topic."""


def fetch_wiki_summary(
    topic: str,
    *,
    timeout_seconds: float = 8,
    sentences: int = 3,
    language: str = "vi",
) -> ServiceResponse:
    if not topic.strip():
        return _wiki_error("Missing wiki topic.")

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_fetch_wiki_summary, topic, sentences, language)
        data = future.result(timeout=timeout_seconds)
    except TimeoutError:
        executor.shutdown(wait=False, cancel_futures=True)
        return _wiki_error("Wikipedia request timed out.")
    except wikipedia.DisambiguationError as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        options = ", ".join(exc.options[:5])
        return _wiki_error(f"Wikipedia topic is ambiguous. Options: {options}")
    except wikipedia.PageError:
        executor.shutdown(wait=False, cancel_futures=True)
        return _wiki_error("Wikipedia topic was not found.")
    except WikiTopicNotFound as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        return _wiki_error(str(exc))
    except Exception as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        return _wiki_error(f"Wikipedia error: {exc}")
    finally:
        if "data" in locals():
            executor.shutdown(wait=True)

    return {"ok": True, "data": data}


def _fetch_wiki_summary(
    topic: str,
    sentences: int,
    language: str,
) -> dict[str, str]:
    wikipedia.set_lang(language)
    search_results = wikipedia.search(topic, results=1)
    if not search_results:
        raise WikiTopicNotFound(f"Wikipedia topic was not found: {topic}")

    page_title = search_results[0]
    page = wikipedia.page(page_title, auto_suggest=False)
    summary = wikipedia.summary(page.title, sentences=sentences, auto_suggest=False)
    return {
        "title": page.title,
        "summary": summary,
        "url": page.url,
    }


def _wiki_error(message: str) -> ServiceResponse:
    return {
        "ok": False,
        "error": {
            "source": WIKI_SOURCE,
            "message": message,
            "status_code": None,
        },
    }
