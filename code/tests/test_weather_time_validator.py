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


def _validate(time_text: str, candidate: str = "forecast") -> dict:
    return WeatherTimeValidator().validate(
        time_text,
        request_type_candidate=candidate,
        reference_datetime=REFERENCE,
    )


def test_current_time_is_authoritatively_classified_by_python() -> None:
    result = _validate("bây giờ", "forecast")

    assert result["status"] == "valid"
    assert result["request_type"] == "current"
    assert result["reference_datetime"].startswith("2026-07-13T10:00:00+07:00")


def test_tomorrow_uses_ho_chi_minh_reference_date() -> None:
    result = _validate("ngày mai")

    assert result["request_type"] == "forecast"
    assert result["start_date"] == "2026-07-14"
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


def test_invalid_calendar_date_requires_clarification() -> None:
    result = _validate("ngày 31/2/2026")

    assert result["status"] == "needs_clarification"
    assert result["code"] == "invalid_date"
