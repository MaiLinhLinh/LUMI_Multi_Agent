import time

from rag_manager.cache import INFINITE_TTL, MemoryCache


def test_cache_miss_on_first_lookup() -> None:
    cache = MemoryCache()

    value = cache.get("weather:ha-noi:2026071011", default="miss")

    assert value == "miss"
    assert cache.stats() == {
        "hits": 0,
        "misses": 1,
        "size": 0,
    }


def test_cache_hit_after_set() -> None:
    cache = MemoryCache()
    cache.set("wiki:openai", {"title": "OpenAI"})

    value = cache.get("wiki:openai")

    assert value == {"title": "OpenAI"}
    assert cache.stats() == {
        "hits": 1,
        "misses": 0,
        "size": 1,
    }


def test_cache_ttl_expire_returns_miss() -> None:
    cache = MemoryCache()
    cache.set("news:ai:202607101100", ["article"], ttl_seconds=0.01)

    time.sleep(0.03)
    value = cache.get("news:ai:202607101100", default="miss")

    assert value == "miss"
    assert not cache.contains("news:ai:202607101100")
    assert cache.stats() == {
        "hits": 0,
        "misses": 1,
        "size": 0,
    }


def test_wiki_cache_infinite_ttl_does_not_expire() -> None:
    cache = MemoryCache()
    cache.set(
        "wiki:openai",
        {"title": "OpenAI", "summary": "AI research company."},
        ttl_seconds=INFINITE_TTL,
    )

    time.sleep(0.03)
    value = cache.get("wiki:openai")

    assert value == {"title": "OpenAI", "summary": "AI research company."}
    assert cache.contains("wiki:openai")
    assert cache.stats() == {
        "hits": 1,
        "misses": 0,
        "size": 1,
    }
