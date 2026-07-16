from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import rag_manager.services.news_api as news_api
import rag_manager.services.weather_api as weather_api
import rag_manager.services.wiki_api as wiki_api


def test_geocode_weather_location_returns_normalized_candidates(monkeypatch) -> None:
    calls = []

    def fake_get_json_list(url, *, source, params, timeout_seconds):
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
            "data": [
                {
                    "name": "Hanoi",
                    "local_names": {"vi": "Hà Nội"},
                    "state": "Hà Nội",
                    "country": "VN",
                    "lat": 21.0294498,
                    "lon": 105.8544441,
                }
            ],
        }

    monkeypatch.setattr(weather_api, "get_json_list", fake_get_json_list)

    response = weather_api.geocode_weather_location(
        "Hanoi,VN",
        api_key="weather-key",
        timeout_seconds=3,
    )

    assert calls[0]["url"] == weather_api.OPENWEATHER_GEOCODING_URL
    assert calls[0]["params"]["q"] == "Hanoi,VN"
    assert calls[0]["params"]["limit"] == 5
    assert response["ok"] is True
    assert response["data"]["candidates"][0]["country"] == "VN"
    assert response["data"]["candidates"][0]["local_names"]["vi"] == "Hà Nội"


def test_fetch_weather_uses_http_helper_and_compacts_response(monkeypatch, capsys) -> None:
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
        raw_text = '{"name":"Ha Noi","main":{"temp":30.5}}'
        return {
            "ok": True,
            "raw_text": raw_text,
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
    assert response["data"]["timezone_offset_seconds"] == 25200
    assert response["data"]["observed_at_utc"] == "2026-07-10T07:53:20+00:00"
    assert response["data"]["observed_at_local"] == "2026-07-10T14:53:20+07:00"
    assert response["raw_data"]["main"]["temp"] == 30.5
    terminal_output = capsys.readouterr().out
    assert "[OpenWeather][current][RAW_RESPONSE_TEXT]" in terminal_output
    assert '{"name":"Ha Noi","main":{"temp":30.5}}' in terminal_output
    assert "[OpenWeather][current][RAW_API_RESPONSE]" in terminal_output
    assert '"temp": 30.5' in terminal_output
    assert "weather-key" not in terminal_output


def test_fetch_weather_uses_verified_coordinates_when_provided(monkeypatch) -> None:
    calls = []

    def fake_get_json(url, *, source, params, timeout_seconds):
        calls.append(params)
        return {
            "ok": True,
            "data": {
                "name": "Vinh",
                "weather": [],
                "main": {},
                "wind": {},
                "clouds": {},
                "sys": {"country": "VN"},
            },
        }

    monkeypatch.setattr(weather_api, "get_json", fake_get_json)

    result = weather_api.fetch_weather(
        "Vinh,VN",
        api_key="weather-key",
        latitude=18.676346,
        longitude=105.676548,
    )

    assert result["ok"] is True
    assert calls[0]["lat"] == 18.676346
    assert calls[0]["lon"] == 105.676548
    assert "q" not in calls[0]


def test_fetch_weather_forecast_uses_http_helper_and_compacts_daily_response(monkeypatch, capsys) -> None:
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
        raw_text = '{"city":{"name":"Ha Noi"},"list":[]}'
        return {
            "ok": True,
            "raw_text": raw_text,
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
    assert response["data"]["available_day_count"] == 1
    assert response["data"]["interval_count"] == 2
    assert response["data"]["coverage_start_local"] == "2026-07-10T14:53:20+07:00"
    assert response["data"]["coverage_end_local"] == "2026-07-10T17:53:20+07:00"
    assert response["data"]["days"][0]["date"] == "2026-07-10"
    assert response["data"]["days"][0]["temperature"]["min_celsius"] == 28.5
    assert response["data"]["days"][0]["temperature"]["max_celsius"] == 30.0
    assert response["data"]["days"][0]["max_rain_probability"] == 0.6
    assert response["data"]["days"][0]["total_rain_mm"] == 0.5
    assert response["data"]["days"][0]["rain_data_complete"] is True
    assert response["data"]["days"][0]["temperature_feels_like_celsius"] == 34
    first_interval = response["data"]["days"][0]["intervals"][0]
    assert first_interval["forecast_at_utc"] == "2026-07-10T07:53:20+00:00"
    assert first_interval["forecast_at_local"] == "2026-07-10T14:53:20+07:00"
    assert first_interval["time"] == "2026-07-10 14:53:20"
    assert first_interval["local_date"] == "2026-07-10"
    assert first_interval["provider_time_utc"] == "2026-07-10 00:00:00"
    assert response["data"]["days"][0]["intervals"][1]["rain_3h_mm"] == 0.0
    assert response["raw_data"]["list"][0]["pop"] == 0.6
    terminal_output = capsys.readouterr().out
    assert "[OpenWeather][forecast][RAW_RESPONSE_TEXT]" in terminal_output
    assert '{"city":{"name":"Ha Noi"},"list":[]}' in terminal_output
    assert "[OpenWeather][forecast][RAW_API_RESPONSE]" in terminal_output
    assert '"dt_txt": "2026-07-10 00:00:00"' in terminal_output
    assert "weather-key" not in terminal_output


def test_fetch_weather_forecast_full_mode_omits_cnt_and_keeps_all_provider_data(
    monkeypatch,
) -> None:
    calls = []
    first = datetime(2026, 7, 10, tzinfo=timezone.utc)
    raw_items = []
    for offset in range(6):
        timestamp = int((first + timedelta(days=offset)).timestamp())
        raw_items.append(
            {
                "dt": timestamp,
                "dt_txt": "provider value",
                "weather": [{"main": "Clouds", "description": "có mây"}],
                "main": {
                    "temp": 30,
                    "feels_like": 32,
                    "humidity": 70,
                    "pressure": 1005,
                },
                "wind": {"speed": 2},
                "clouds": {"all": 50},
                "pop": 0.1,
            }
        )

    def fake_get_json(url, *, source, params, timeout_seconds):
        calls.append(params)
        return {
            "ok": True,
            "data": {
                "city": {"name": "Hà Nội", "country": "VN", "timezone": 25200},
                "list": raw_items,
            },
        }

    monkeypatch.setattr(weather_api, "get_json", fake_get_json)

    response = weather_api.fetch_weather_forecast(
        "Hà Nội",
        api_key="weather-key",
    )

    assert "cnt" not in calls[0]
    assert response["ok"] is True
    assert response["data"]["requested_days"] is None
    assert response["data"]["available_day_count"] == 6
    assert response["data"]["interval_count"] == 6
    assert len(response["data"]["days"]) == 6
    assert len(response["raw_data"]["list"]) == 6


def test_forecast_normalizer_groups_and_summarizes_by_vietnam_local_date() -> None:
    local_day_values = [
        (1783965600, 28.16, 32.84, 82),
        (1783976400, 27.72, 32.12, 85),
        (1783987200, 28.92, 34.52, 80),
        (1783998000, 33.30, 39.92, 59),
        (1784008800, 36.25, 42.43, 47),
        (1784019600, 35.25, 40.87, 49),
        (1784030400, 30.66, 36.70, 70),
        (1784041200, 29.63, 35.55, 76),
    ]

    def interval(timestamp, temperature, feels_like, humidity):
        return {
            "dt": timestamp,
            "dt_txt": "provider UTC text must not control grouping",
            "main": {
                "temp": temperature,
                "feels_like": feels_like,
                "humidity": humidity,
                "pressure": 1000,
            },
            "weather": [{"main": "Clouds", "description": "mây đen u ám"}],
            "wind": {"speed": 2.0},
            "clouds": {"all": 100},
            "pop": 0,
        }

    raw_items = [interval(*values) for values in local_day_values]
    raw_items.append(interval(1784052000, 28.88, 34.41, 80))
    result = weather_api.compact_forecast_data(
        {
            "city": {"name": "Hà Nội", "country": "VN", "timezone": 25200},
            "list": raw_items,
        },
        days=2,
    )

    assert result["day_grouping"] == "location_local_date"
    assert result["interval_time_basis"] == "location_local_time"
    assert result["available_day_count"] == 2
    assert result["interval_count"] == 9
    assert [day["date"] for day in result["days"]] == [
        "2026-07-14",
        "2026-07-15",
    ]
    july_14 = result["days"][0]
    assert july_14["interval_count"] == 8
    assert july_14["is_partial_day"] is False
    assert july_14["temperature"] == {
        "min_celsius": 27.72,
        "max_celsius": 36.25,
    }
    assert july_14["temperature_feels_like_celsius"] == 42.43
    assert july_14["humidity_percent"] == 68.5
    assert july_14["max_rain_probability"] == 0
    assert july_14["total_rain_mm"] == 0.0
    assert july_14["rain_data_complete"] is True
    assert july_14["intervals"][0]["time"] == "2026-07-14 01:00:00"
    assert july_14["intervals"][-1]["time"] == "2026-07-14 22:00:00"
    assert result["days"][1]["intervals"][0]["time"] == "2026-07-15 01:00:00"


def test_forecast_normalizer_rejects_missing_timezone_instead_of_using_utc() -> None:
    with pytest.raises(
        weather_api.WeatherNormalizationError,
        match="city.timezone",
    ):
        weather_api.compact_forecast_data(
            {"city": {"name": "Hà Nội"}, "list": []},
            days=1,
        )


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
