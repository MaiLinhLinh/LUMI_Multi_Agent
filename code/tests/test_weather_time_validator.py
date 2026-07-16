from datetime import datetime
from zoneinfo import ZoneInfo

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
    time_of_day_text: str | None = None,
) -> dict:
    return WeatherTimeValidator().validate(
        date_text,
        time_of_day_text=time_of_day_text,
        request_type_candidate=candidate,
        reference_datetime=REFERENCE,
    )


def test_current_time_is_authoritatively_classified_by_python() -> None:
    result = _validate("bây giờ", "forecast")

    assert result["status"] == "valid"
    assert result["request_type"] == "current"
    assert result["reference_datetime"].startswith("2026-07-13T10:00:00+07:00")


def test_current_candidate_does_not_require_a_date() -> None:
    result = _validate(None, "current")

    assert result["status"] == "valid"
    assert result["request_type"] == "current"


def test_specific_hour_is_canonicalized_for_hourly_forecast() -> None:
    result = _validate("hôm nay", time_of_day_text="lúc 9 giờ")

    assert result["status"] == "valid"
    assert result["request_type"] == "forecast"
    assert result["start_date"] == "2026-07-13"
    assert result["requested_time_of_day"] == "09:00"
    assert result["forecast_interval_start_time"] == "09:00"
    assert result["forecast_interval_minutes"] == 60


def test_specific_minute_uses_the_containing_hourly_interval() -> None:
    result = _validate("ngày mai", time_of_day_text="lúc 14:30")

    assert result["requested_time_of_day"] == "14:30"
    assert result["forecast_interval_start_time"] == "14:00"
    assert result["requested_hour"] == 14
    assert result["requested_minute"] == 30


def test_vietnamese_pm_period_is_supported() -> None:
    result = _validate("ngày mai", time_of_day_text="2 giờ chiều")

    assert result["requested_time_of_day"] == "14:00"


def test_specific_hour_without_a_date_requires_clarification() -> None:
    result = _validate(None, time_of_day_text="9h")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "missing_date"


def test_invalid_specific_hour_requires_clarification() -> None:
    result = _validate("hôm nay", time_of_day_text="25h")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "invalid_time_of_day"


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
    result = _validate("từ 13/7/2026 đến 15/7/2026")

    assert result["start_date"] == "2026-07-13"
    assert result["days"] == 3


def test_upcoming_days_start_tomorrow() -> None:
    result = _validate("3 ngày tới")

    assert result["start_date"] == "2026-07-14"
    assert result["days"] == 3


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


def test_more_than_five_days_requires_confirmation() -> None:
    result = _validate("6 ngày tới")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_range_exceeded"
    assert result["details"]["max_forecast_days"] == 5


def test_vietnamese_word_range_starts_tomorrow() -> None:
    result = _validate("hai ngày tiếp theo")

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-14"
    assert result["days"] == 2


def test_vietnamese_upcoming_phrase_is_supported() -> None:
    result = _validate("hai ngày sắp tới")

    assert result["start_date"] == "2026-07-14"
    assert result["days"] == 2


def test_vietnamese_word_range_exceeds_business_limit() -> None:
    result = _validate("sáu ngày tới")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_range_exceeded"


def test_relative_single_date_uses_day_offset() -> None:
    result = _validate("3 hôm nữa")

    assert result["start_date"] == "2026-07-16"
    assert result["days"] == 1


def test_relative_date_beyond_provider_horizon_is_rejected() -> None:
    result = _validate("sáu bữa sau")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_horizon_exceeded"
    assert result["details"]["forecast_horizon_end"] == "2026-07-18"


def test_forecast_range_end_beyond_provider_horizon_is_rejected() -> None:
    result = _validate("từ 17/7/2026 đến 19/7/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_horizon_exceeded"


def test_forecast_date_in_past_is_rejected() -> None:
    result = _validate("ngày 12/7/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "forecast_date_in_past"


def test_five_upcoming_days_include_reference_plus_five() -> None:
    result = _validate("năm ngày tới")

    assert result["status"] == "valid"
    assert result["start_date"] == "2026-07-14"
    assert result["days"] == 5


def test_invalid_calendar_date_requires_clarification() -> None:
    result = _validate("ngày 31/2/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "invalid_date"
