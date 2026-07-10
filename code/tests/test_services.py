from types import SimpleNamespace

import rag_manager.services.news_api as news_api
import rag_manager.services.weather_api as weather_api
import rag_manager.services.wiki_api as wiki_api


def test_fetch_weather_uses_http_helper_and_compacts_response(monkeypatch) -> None:
    calls = []

    def fake_get_json(url, *, source, params, timeout_seconds):
        calls.append(
            {
                "url": url,
                "source": source,
                "params": params,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "ok": True,
            "data": {
                "name": "Ha Noi",
                "dt": 1783670000,
                "timezone": 25200,
                "weather": [{"main": "Clouds", "description": "mây rải rác"}],
                "main": {
                    "temp": 30.5,
                    "feels_like": 34,
                    "temp_min": 29,
                    "temp_max": 32,
                    "humidity": 70,
                    "pressure": 1008,
                },
                "wind": {"speed": 3.4, "deg": 120},
                "clouds": {"all": 40},
                "sys": {"country": "VN"},
            },
        }

    monkeypatch.setattr(weather_api, "get_json", fake_get_json)

    response = weather_api.fetch_weather("Hà Nội", api_key="weather-key", timeout_seconds=3)

    assert calls[0]["params"]["appid"] == "weather-key"
    assert calls[0]["params"]["q"] == "Hà Nội"
    assert response["ok"] is True
    assert response["data"]["location"] == "Ha Noi"
    assert response["data"]["condition"]["description"] == "mây rải rác"
    assert response["data"]["temperature"]["current_celsius"] == 30.5


def test_fetch_weather_forecast_uses_http_helper_and_compacts_daily_response(monkeypatch) -> None:
    calls = []

    def fake_get_json(url, *, source, params, timeout_seconds):
        calls.append(
            {
                "url": url,
                "source": source,
                "params": params,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "ok": True,
            "data": {
                "city": {"name": "Ha Noi", "country": "VN", "timezone": 25200},
                "list": [
                    {
                        "dt": 1783670000,
                        "dt_txt": "2026-07-10 00:00:00",
                        "weather": [{"main": "Rain", "description": "mua nhe"}],
                        "main": {"temp": 28.5, "feels_like": 32, "humidity": 83},
                        "wind": {"speed": 4.1},
                        "clouds": {"all": 88},
                        "rain": {"3h": 0.5},
                        "pop": 0.6,
                    },
                    {
                        "dt": 1783680800,
                        "dt_txt": "2026-07-10 03:00:00",
                        "weather": [{"main": "Clouds", "description": "nhieu may"}],
                        "main": {"temp": 30.0, "feels_like": 34, "humidity": 78},
                        "wind": {"speed": 3.8},
                        "clouds": {"all": 90},
                        "pop": 0.2,
                    },
                ],
            },
        }

    monkeypatch.setattr(weather_api, "get_json", fake_get_json)

    response = weather_api.fetch_weather_forecast(
        "Ha Noi",
        api_key="weather-key",
        timeout_seconds=3,
        days=3,
    )

    assert calls[0]["url"] == weather_api.OPENWEATHER_FORECAST_URL
    assert calls[0]["params"]["q"] == "Ha Noi"
    assert calls[0]["params"]["cnt"] == 24
    assert response["ok"] is True
    assert response["data"]["location"] == "Ha Noi"
    assert response["data"]["requested_days"] == 3
    assert response["data"]["days"][0]["date"] == "2026-07-10"
    assert response["data"]["days"][0]["temperature"]["min_celsius"] == 28.5
    assert response["data"]["days"][0]["temperature"]["max_celsius"] == 30.0
    assert response["data"]["days"][0]["max_rain_probability"] == 0.6
    assert response["data"]["days"][0]["total_rain_mm"] == 0.5


def test_fetch_news_uses_http_helper_and_compacts_response(monkeypatch) -> None:
    calls = []

    def fake_get_json(url, *, source, params, timeout_seconds):
        calls.append(
            {
                "url": url,
                "source": source,
                "params": params,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "ok": True,
            "data": {
                "totalArticles": 3,
                "articles": [
                    {
                        "title": "A",
                        "description": "D1",
                        "source": {"name": "Src1"},
                        "publishedAt": "2026-07-10T01:00:00Z",
                        "url": "https://a",
                        "content": "drop",
                    },
                    {
                        "title": "B",
                        "description": "D2",
                        "source": {"name": "Src2"},
                        "publishedAt": "2026-07-10T02:00:00Z",
                        "url": "https://b",
                    },
                ],
            },
        }

    monkeypatch.setattr(news_api, "get_json", fake_get_json)

    response = news_api.fetch_news("AI", api_key="news-key", timeout_seconds=4, max_results=1)

    assert calls[0]["params"]["apikey"] == "news-key"
    assert calls[0]["params"]["q"] == "AI"
    assert calls[0]["params"]["max"] == 1
    assert response["ok"] is True
    assert response["data"]["total_articles"] == 3
    assert response["data"]["articles"] == [
        {
            "title": "A",
            "description": "D1",
            "source": "Src1",
            "published_at": "2026-07-10T01:00:00Z",
            "url": "https://a",
        }
    ]


def test_fetch_wiki_summary_uses_wikipedia_package(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(wiki_api.wikipedia, "set_lang", lambda lang: calls.append(("lang", lang)))
    monkeypatch.setattr(wiki_api.wikipedia, "search", lambda topic, results=1: ["OpenAI"])
    monkeypatch.setattr(
        wiki_api.wikipedia,
        "page",
        lambda title, auto_suggest=False: SimpleNamespace(
            title="OpenAI",
            url="https://vi.wikipedia.org/wiki/OpenAI",
        ),
    )
    monkeypatch.setattr(
        wiki_api.wikipedia,
        "summary",
        lambda title, sentences=3, auto_suggest=False: "Summary text.",
    )

    response = wiki_api.fetch_wiki_summary("OpenAI", timeout_seconds=1)

    assert calls == [("lang", "vi")]
    assert response == {
        "ok": True,
        "data": {
            "title": "OpenAI",
            "summary": "Summary text.",
            "url": "https://vi.wikipedia.org/wiki/OpenAI",
        },
    }
