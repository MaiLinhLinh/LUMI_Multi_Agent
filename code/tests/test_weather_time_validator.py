from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from rag_manager.services.weather_time_validator import WeatherTimeValidator


REFERENCE = datetime(
    2026,
    7,
    13,
    10,
    tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"),
)


def _validate(
    date_text: str | None,
    candidate: str = "forecast",
    *,
    date_range: dict | None | object = ...,
    source_query: str | None = None,
    time_of_day_text: str | None = None,
    normalized_time: str | None = None,
) -> dict:
    if date_range is ...:
        date_range = (
            None
            if candidate == "current" or date_text is None
            else {
                "type": "single_day",
                "quantity": None,
                "end_date_text": None,
            }
        )
    return WeatherTimeValidator().validate(
        date_text,
        date_range=date_range,
        source_query=source_query,
        time_of_day_text=time_of_day_text,
        normalized_time=normalized_time,
        request_type_candidate=candidate,
        reference_datetime=REFERENCE,
    )


def test_current_time_is_authoritatively_classified_by_python() -> None:
    result = _validate("bây giờ", "forecast", date_range=None)

    assert result["status"] == "valid"
    assert result["request_type"] == "current"
    assert result["reference_datetime"].startswith("2026-07-13T10:00:00+07:00")


def test_current_candidate_does_not_require_a_date() -> None:
    result = _validate(None, "current")

    assert result["status"] == "valid"
    assert result["request_type"] == "current"


def test_specific_hour_is_canonicalized_for_hourly_forecast() -> None:
    result = _validate(
        "hôm nay",
        time_of_day_text="lúc 9 giờ",
        normalized_time="09:00",
    )

    assert result["status"] == "valid"
    assert result["request_type"] == "forecast"
    assert result["start_date"] == "2026-07-13"
    assert result["requested_time_of_day"] == "09:00"
    assert result["forecast_interval_start_time"] == "09:00"
    assert result["forecast_interval_minutes"] == 60


def test_specific_minute_uses_the_containing_hourly_interval() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="lúc 14:30",
        normalized_time="14:30",
    )

    assert result["requested_time_of_day"] == "14:30"
    assert result["forecast_interval_start_time"] == "14:00"
    assert result["requested_hour"] == 14
    assert result["requested_minute"] == 30


def test_vietnamese_pm_period_is_supported() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="2 giờ chiều",
        normalized_time="14:00",
    )

    assert result["requested_time_of_day"] == "14:00"


def test_vietnamese_pm_period_keeps_an_existing_24_hour_value() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="13 giờ chiều",
        normalized_time="13:00",
    )

    assert result["status"] == "valid"
    assert result["requested_time_of_day"] == "13:00"


def test_vietnamese_pm_period_keeps_late_24_hour_value() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="23 giờ chiều",
        normalized_time="23:00",
    )

    assert result["status"] == "valid"
    assert result["requested_time_of_day"] == "23:00"


def test_vietnamese_morning_period_rejects_conflicting_24_hour_value() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="23 giờ sáng",
        normalized_time="23:00",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "invalid_time_of_day"


def test_specific_hour_without_a_date_requires_clarification() -> None:
    result = _validate(
        None,
        time_of_day_text="9h",
        normalized_time="09:00",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "missing_date"


def test_invalid_specific_hour_requires_clarification() -> None:
    result = _validate(
        "hôm nay",
        time_of_day_text="25h",
        normalized_time="23:00",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "invalid_time_of_day"
    assert result["details"]["field"] == "time_of_day_text"


def test_unrecognized_date_identifies_date_field() -> None:
    result = _validate(
        "sáng mai",
        time_of_day_text="9h",
        normalized_time="09:00",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "unrecognized_date"
    assert result["details"]["field"] == "date_text"


def test_wrapped_clock_phrase_uses_normalized_time() -> None:
    result = _validate(
        "ngày kia",
        time_of_day_text="vào khoảng 12h trưa thì sao",
        normalized_time="12:00",
    )

    assert result["status"] == "valid"
    assert result["requested_time_of_day"] == "12:00"


def test_normalized_time_must_match_explicit_clock_text() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="khoảng 9h sáng",
        normalized_time="21:00",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "normalized_time_conflict"
    assert result["details"]["time_supported_by_text"] == "09:00"


def test_normalized_time_cannot_be_fabricated_without_raw_time() -> None:
    result = _validate("ngày mai", normalized_time="09:00")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "unexpected_normalized_time"


def test_exact_raw_time_requires_normalized_time() -> None:
    result = _validate("ngày mai", time_of_day_text="khoảng 9h sáng")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "missing_normalized_time"


def test_vague_period_cannot_be_turned_into_an_exact_time() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="vào buổi sáng",
        normalized_time="09:00",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "normalized_time_without_explicit_clock"


def test_spelled_vietnamese_clock_is_cross_checked() -> None:
    result = _validate(
        "ngày mai",
        time_of_day_text="khoảng chín giờ rưỡi sáng",
        normalized_time="09:30",
    )

    assert result["status"] == "valid"
    assert result["requested_time_of_day"] == "09:30"


def test_tomorrow_uses_ho_chi_minh_reference_date() -> None:
    result = _validate("ngày mai")

    assert result["request_type"] == "forecast"
    assert result["start_date"] == "2026-07-14"
    assert result["days"] == 1


def test_today_remains_a_valid_forecast_date() -> None:
    result = _validate("hôm nay")

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 1


def test_date_range_is_inclusive() -> None:
    result = _validate(
        "13/7/2026",
        date_range={
            "type": "explicit_range",
            "quantity": None,
            "end_date_text": "15/7/2026",
        },
    )

    assert result["start_date"] == "2026-07-13"
    assert result["end_date"] == "2026-07-15"
    assert result["days"] == 3
    assert result["date_range_type"] == "explicit_range"


def test_explicit_range_from_20_to_23_is_four_inclusive_days() -> None:
    validator = WeatherTimeValidator(forecast_horizon_days=30)

    result = validator.validate(
        "20/7/2026",
        date_range={
            "type": "explicit_range",
            "quantity": None,
            "end_date_text": "23/7/2026",
        },
        request_type_candidate="forecast",
        reference_datetime=REFERENCE,
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-20"
    assert result["end_date"] == "2026-07-23"
    assert result["days"] == 4


def test_upcoming_days_include_today_and_requested_future_days() -> None:
    result = _validate(
        "3 ngày tới",
        date_range={
            "type": "next_days",
            "quantity": 3,
            "end_date_text": None,
        },
    )

    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 4


@pytest.mark.parametrize("quantity", [4, 5, 6])
def test_variable_next_days_quantities_include_the_anchor_day(quantity: int) -> None:
    result = _validate(
        None,
        date_range={
            "type": "next_days",
            "quantity": quantity,
            "end_date_text": None,
        },
        source_query=f"Cho tôi xem {quantity} ngày tới",
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-13"
    assert result["days"] == quantity + 1


def test_bare_weekday_requires_clarification() -> None:
    result = _validate("thứ Tư")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "ambiguous_time"


def test_qualified_weekday_is_calculated() -> None:
    result = _validate("thứ Năm tuần này")

    assert result["start_date"] == "2026-07-16"
    assert result["days"] == 1


def test_weekday_and_date_conflict_returns_both_calendar_options() -> None:
    result = _validate("thứ Tư ngày 17/7/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "weekday_date_conflict"
    assert result["details"] == {
        "field": "date_text",
        "provided_date": "2026-07-17",
        "provided_weekday": "thứ Tư",
        "actual_weekday": "thứ Sáu",
        "matching_weekday_date": "2026-07-15",
    }


def test_weekday_and_relative_date_conflict_is_detected() -> None:
    result = _validate("thứ Tư ngày mai")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "weekday_date_conflict"
    assert result["details"]["provided_date"] == "2026-07-14"
    assert result["details"]["actual_weekday"] == "thứ Ba"


def test_more_than_eight_inclusive_days_requires_confirmation() -> None:
    result = _validate(
        "8 ngày tới",
        date_range={
            "type": "next_days",
            "quantity": 8,
            "end_date_text": None,
        },
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_range_exceeded"
    assert result["details"]["max_forecast_days"] == 8


def test_vietnamese_word_range_includes_today() -> None:
    result = _validate(
        "hai ngày tiếp theo",
        date_range={
            "type": "next_days",
            "quantity": 2,
            "end_date_text": None,
        },
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 3


def test_vietnamese_upcoming_phrase_is_supported() -> None:
    result = _validate(
        "hai ngày sắp tới",
        date_range={
            "type": "next_days",
            "quantity": 2,
            "end_date_text": None,
        },
    )

    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 3


def test_one_upcoming_week_includes_today_and_seven_future_days() -> None:
    result = _validate(
        None,
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-13"
    assert result["end_date"] == "2026-07-20"
    assert result["days"] == 8


def test_one_upcoming_week_can_start_tomorrow() -> None:
    result = _validate(
        "ngày mai",
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-14"
    assert result["end_date"] == "2026-07-21"
    assert result["days"] == 8


def test_raw_one_week_phrase_still_starts_today() -> None:
    result = _validate(
        "một tuần tới",
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 8


def test_full_week_quantity_is_checked_from_query_when_anchor_is_null() -> None:
    result = _validate(
        None,
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
        source_query="Cho tôi dự báo 2 tuần tới",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "date_range_quantity_conflict"
    assert result["details"]["quantity_supported_by_text"] == 2


def test_range_type_is_cross_checked_against_query_wording() -> None:
    result = _validate(
        None,
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
        source_query="Cho tôi dự báo 3 ngày tới",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "date_range_type_conflict"
    assert result["details"]["type_supported_by_text"] == "next_days"


def test_full_week_can_use_an_inherited_anchor_date() -> None:
    validator = WeatherTimeValidator(forecast_horizon_days=30)

    result = validator.validate(
        "ngày 23/7/2026",
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
        request_type_candidate="forecast",
        reference_datetime=REFERENCE,
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-23"
    assert result["end_date"] == "2026-07-30"
    assert result["days"] == 8


def test_next_week_uses_the_next_monday_as_its_anchor() -> None:
    validator = WeatherTimeValidator(forecast_horizon_days=30)

    result = validator.validate(
        "tuần tới",
        date_range={
            "type": "full_week",
            "quantity": 1,
            "end_date_text": None,
        },
        request_type_candidate="forecast",
        reference_datetime=REFERENCE,
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-20"
    assert result["end_date"] == "2026-07-27"
    assert result["days"] == 8


def test_next_days_quantity_is_cross_checked_against_raw_phrase() -> None:
    result = _validate(
        "3 ngày tới",
        date_range={
            "type": "next_days",
            "quantity": 5,
            "end_date_text": None,
        },
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "date_range_quantity_conflict"
    assert result["details"]["quantity_supported_by_text"] == 3


def test_next_days_quantity_is_checked_from_query_when_anchor_is_null() -> None:
    result = _validate(
        None,
        date_range={
            "type": "next_days",
            "quantity": 4,
            "end_date_text": None,
        },
        source_query="Thời tiết Hà Nội 3 ngày tới thế nào?",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "date_range_quantity_conflict"


def test_explicit_range_is_cross_checked_against_current_query() -> None:
    result = _validate(
        "20/7/2026",
        date_range={
            "type": "explicit_range",
            "quantity": None,
            "end_date_text": "24/7/2026",
        },
        source_query="Từ 20/7/2026 đến 23/7/2026",
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "date_range_text_conflict"


def test_explicit_range_rejects_an_llm_supplied_quantity() -> None:
    result = _validate(
        "20/7/2026",
        date_range={
            "type": "explicit_range",
            "quantity": 4,
            "end_date_text": "23/7/2026",
        },
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "unexpected_date_range_quantity"


def test_vietnamese_word_range_exceeds_business_limit() -> None:
    result = _validate(
        "tám ngày tới",
        date_range={
            "type": "next_days",
            "quantity": 8,
            "end_date_text": None,
        },
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_range_exceeded"


def test_relative_single_date_uses_day_offset() -> None:
    result = _validate("3 hôm nữa")

    assert result["start_date"] == "2026-07-16"
    assert result["days"] == 1


def test_relative_date_beyond_provider_horizon_is_rejected() -> None:
    result = _validate("chín bữa sau")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_horizon_exceeded"
    assert result["details"]["forecast_horizon_end"] == "2026-07-21"


def test_forecast_range_end_beyond_provider_horizon_is_rejected() -> None:
    result = _validate(
        "20/7/2026",
        date_range={
            "type": "explicit_range",
            "quantity": None,
            "end_date_text": "22/7/2026",
        },
    )

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_horizon_exceeded"


def test_forecast_date_in_past_is_rejected() -> None:
    result = _validate("ngày 12/7/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_date_in_past"


def test_five_upcoming_days_include_reference_plus_five() -> None:
    result = _validate(
        "năm ngày tới",
        date_range={
            "type": "next_days",
            "quantity": 5,
            "end_date_text": None,
        },
    )

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 6


def test_invalid_calendar_date_requires_clarification() -> None:
    result = _validate("ngày 31/2/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "invalid_date"
