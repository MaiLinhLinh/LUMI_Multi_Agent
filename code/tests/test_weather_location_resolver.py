from rag_manager.services.weather_location_resolver import (
    WeatherLocationRecord,
    WeatherLocationResolver,
    get_weather_location_resolver,
    normalize_location_text,
)


def test_default_resolver_matches_name_alias_and_typo() -> None:
    resolver = get_weather_location_resolver()

    exact = resolver.resolve("Tỉnh Nghệ An")
    compact_alias = resolver.resolve("nghean")
    fuzzy = resolver.resolve("nghe annn")

    assert exact["ok"] is True
    assert exact["location_id"] == "nghe_an"
    assert compact_alias["location_id"] == "nghe_an"
    assert fuzzy["location_id"] == "nghe_an"
    assert fuzzy["match_type"] == "fuzzy"


def test_default_resolver_is_cached_for_process_lifetime() -> None:
    assert get_weather_location_resolver() is get_weather_location_resolver()


def test_resolver_returns_structured_missing_and_unknown_errors() -> None:
    resolver = get_weather_location_resolver()

    missing = resolver.resolve("   ")
    unknown = resolver.resolve("Atlantis")

    assert missing["error"]["code"] == "missing_location"
    assert unknown["error"]["code"] == "location_not_found"
    assert unknown["candidates"]


def test_resolver_does_not_choose_between_near_equal_candidates() -> None:
    resolver = WeatherLocationResolver(
        [
            WeatherLocationRecord("alpha_one", "Alpha One", ()),
            WeatherLocationRecord("alpha_two", "Alpha Two", ()),
        ],
        fuzzy_threshold=0.5,
        ambiguity_margin=0.2,
    )

    result = resolver.resolve("Alpha")

    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous_location"
    assert len(result["candidates"]) == 2


def test_location_normalization_handles_vietnamese_prefixes_and_diacritics() -> None:
    assert normalize_location_text("Thành phố Đà Nẵng, Việt Nam") == "da nang"
