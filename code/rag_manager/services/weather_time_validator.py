"""Deterministic Vietnamese time validation for cached weather requests."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

WEATHER_TIMEZONE = "Asia/Ho_Chi_Minh"
EXPECTED_TIMEZONE_OFFSET_SECONDS = 7 * 60 * 60
MAX_FORECAST_DAYS = 5

_VIETNAMESE_WEEKDAYS = (
    "thứ Hai",
    "thứ Ba",
    "thứ Tư",
    "thứ Năm",
    "thứ Sáu",
    "thứ Bảy",
    "Chủ nhật",
)
_WEEKDAY_WORDS = {
    "2": 0,
    "hai": 0,
    "3": 1,
    "ba": 1,
    "4": 2,
    "tu": 2,
    "5": 3,
    "nam": 3,
    "6": 4,
    "sau": 4,
    "7": 5,
    "bay": 5,
}
_DMY_PATTERN = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})\s*[/-]\s*(?P<month>\d{1,2})"
    r"(?:\s*[/-]\s*(?P<year>\d{4}))?(?!\d)"
)
_ISO_PATTERN = re.compile(
    r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)"
)


class WeatherTimeValidator:
    """Convert one raw Vietnamese time phrase into a canonical cache request."""

    def __init__(
        self,
        *,
        timezone_name: str = WEATHER_TIMEZONE,
        max_forecast_days: int = MAX_FORECAST_DAYS,
    ) -> None:
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        self.max_forecast_days = max(1, int(max_forecast_days))

    def validate(
        self,
        time_text: str | None,
        *,
        request_type_candidate: str | None = None,
        reference_datetime: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a valid canonical time request or a clarification result."""

        raw_text = time_text.strip() if isinstance(time_text, str) else ""
        if not raw_text:
            return _time_issue("missing_time", {"requested_text": raw_text})

        reference = self._reference_datetime(reference_datetime)
        reference_date = reference.date()
        normalized = _normalize_text(raw_text)

        if _contains_current_expression(normalized):
            return self._valid_current(reference)

        duration_match = re.search(r"\b(\d+)\s+ngay\s+(?:toi|sap toi)\b", normalized)
        if duration_match:
            requested_days = int(duration_match.group(1))
            start_date = reference_date + timedelta(days=1)
            return self._valid_forecast_or_range_issue(
                start_date=start_date,
                days=requested_days,
                reference=reference,
            )

        date_tokens, invalid_date_text = _extract_date_tokens(
            raw_text,
            reference_year=reference_date.year,
        )
        if invalid_date_text is not None:
            return _time_issue(
                "invalid_date",
                {"requested_text": invalid_date_text},
            )

        weekday = _extract_weekday(normalized)
        if len(date_tokens) >= 2:
            start_date = date_tokens[0][0]
            end_date = date_tokens[1][0]
            if not date_tokens[1][2] and end_date < start_date:
                try:
                    end_date = end_date.replace(year=end_date.year + 1)
                except ValueError:
                    return _time_issue(
                        "invalid_date",
                        {"requested_text": date_tokens[1][1]},
                    )
            if end_date < start_date:
                return _time_issue(
                    "invalid_date_range",
                    {
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                    },
                )
            weekday_issue = _weekday_date_issue(weekday, start_date)
            if weekday_issue:
                return weekday_issue
            requested_days = (end_date - start_date).days + 1
            return self._valid_forecast_or_range_issue(
                start_date=start_date,
                days=requested_days,
                reference=reference,
                end_date=end_date,
            )

        if date_tokens:
            requested_date = date_tokens[0][0]
            weekday_issue = _weekday_date_issue(weekday, requested_date)
            if weekday_issue:
                return weekday_issue
            return self._valid_forecast_or_range_issue(
                start_date=requested_date,
                days=1,
                reference=reference,
            )

        if _contains_today_expression(normalized):
            weekday_issue = _weekday_date_issue(weekday, reference_date)
            if weekday_issue:
                return weekday_issue
            return self._valid_forecast_or_range_issue(
                start_date=reference_date,
                days=1,
                reference=reference,
            )
        if _contains_day_after_tomorrow_expression(normalized):
            requested_date = reference_date + timedelta(days=2)
            weekday_issue = _weekday_date_issue(weekday, requested_date)
            if weekday_issue:
                return weekday_issue
            return self._valid_forecast_or_range_issue(
                start_date=requested_date,
                days=1,
                reference=reference,
            )
        if _contains_tomorrow_expression(normalized):
            requested_date = reference_date + timedelta(days=1)
            weekday_issue = _weekday_date_issue(weekday, requested_date)
            if weekday_issue:
                return weekday_issue
            return self._valid_forecast_or_range_issue(
                start_date=requested_date,
                days=1,
                reference=reference,
            )

        if weekday is not None:
            weekday_index, weekday_text = weekday
            requested_date = _qualified_weekday_date(
                normalized,
                reference_date=reference_date,
                weekday_index=weekday_index,
            )
            if requested_date is None:
                return _time_issue(
                    "ambiguous_time",
                    {
                        "requested_text": raw_text,
                        "provided_weekday": weekday_text,
                        "required_qualifier": "tuần này hoặc tuần tới",
                    },
                )
            return self._valid_forecast_or_range_issue(
                start_date=requested_date,
                days=1,
                reference=reference,
            )

        return _time_issue(
            "unrecognized_time",
            {
                "requested_text": raw_text,
                "request_type_candidate": _normalized_candidate(
                    request_type_candidate
                ),
            },
        )

    def _reference_datetime(self, value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(self.timezone)
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    def _valid_current(self, reference: datetime) -> dict[str, Any]:
        return {
            "status": "valid",
            "request_type": "current",
            "reference_datetime": reference.isoformat(),
            "timezone": self.timezone_name,
        }

    def _valid_forecast_or_range_issue(
        self,
        *,
        start_date: date,
        days: int,
        reference: datetime,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        if days < 1:
            return _time_issue(
                "invalid_date_range",
                {
                    "start_date": start_date.isoformat(),
                    "days": days,
                },
            )
        if days > self.max_forecast_days:
            calculated_end = end_date or start_date + timedelta(days=days - 1)
            return _time_issue(
                "forecast_range_exceeded",
                {
                    "start_date": start_date.isoformat(),
                    "end_date": calculated_end.isoformat(),
                    "requested_days": days,
                    "max_forecast_days": self.max_forecast_days,
                },
            )
        return {
            "status": "valid",
            "request_type": "forecast",
            "start_date": start_date.isoformat(),
            "days": days,
            "reference_datetime": reference.isoformat(),
            "timezone": self.timezone_name,
        }


def _normalized_candidate(value: str | None) -> str | None:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    return normalized if normalized in {"current", "forecast"} else None


def _normalize_text(value: str) -> str:
    normalized = value.casefold().replace("đ", "d")
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(character) != "Mn"
    )
    return " ".join(normalized.split())


def _contains_current_expression(value: str) -> bool:
    return any(
        expression in value
        for expression in (
            "hien tai",
            "bay gio",
            "ngay luc nay",
            "luc nay",
            "right now",
            "current conditions",
        )
    )


def _contains_today_expression(value: str) -> bool:
    return any(expression in value for expression in ("hom nay", "toi nay", "today", "tonight"))


def _contains_tomorrow_expression(value: str) -> bool:
    return "ngay mai" in value or "tomorrow" in value


def _contains_day_after_tomorrow_expression(value: str) -> bool:
    return "ngay kia" in value or "day after tomorrow" in value


def _extract_weekday(value: str) -> tuple[int, str] | None:
    if re.search(r"\bchu\s+nhat\b", value):
        return 6, _VIETNAMESE_WEEKDAYS[6]
    match = re.search(
        r"\bthu\s+(2|hai|3|ba|4|tu|5|nam|6|sau|7|bay)\b",
        value,
    )
    if not match:
        return None
    weekday_index = _WEEKDAY_WORDS[match.group(1)]
    return weekday_index, _VIETNAMESE_WEEKDAYS[weekday_index]


def _qualified_weekday_date(
    value: str,
    *,
    reference_date: date,
    weekday_index: int,
) -> date | None:
    start_of_week = reference_date - timedelta(days=reference_date.weekday())
    if "tuan nay" in value:
        return start_of_week + timedelta(days=weekday_index)
    if "tuan toi" in value or "tuan sau" in value:
        return start_of_week + timedelta(days=7 + weekday_index)
    if re.search(r"\b(?:toi|sap toi)\b", value):
        delta = (weekday_index - reference_date.weekday()) % 7
        return reference_date + timedelta(days=delta or 7)
    return None


def _extract_date_tokens(
    value: str,
    *,
    reference_year: int,
) -> tuple[list[tuple[date, str, bool]], str | None]:
    matches: list[tuple[int, int, re.Match[str], bool]] = []
    iso_spans: list[tuple[int, int]] = []
    for match in _ISO_PATTERN.finditer(value):
        matches.append((match.start(), match.end(), match, True))
        iso_spans.append((match.start(), match.end()))
    for match in _DMY_PATTERN.finditer(value):
        if any(match.start() < end and match.end() > start for start, end in iso_spans):
            continue
        matches.append((match.start(), match.end(), match, False))
    matches.sort(key=lambda item: item[0])

    tokens: list[tuple[date, str, bool]] = []
    for _start, _end, match, is_iso in matches:
        raw = match.group(0)
        explicit_year = is_iso or match.groupdict().get("year") is not None
        year_text = match.groupdict().get("year")
        year = int(year_text) if year_text else reference_year
        try:
            parsed = date(
                year,
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            return [], raw
        tokens.append((parsed, raw, explicit_year))
    return tokens, None


def _weekday_date_issue(
    weekday: tuple[int, str] | None,
    requested_date: date,
) -> dict[str, Any] | None:
    if weekday is None or weekday[0] == requested_date.weekday():
        return None
    weekday_index, weekday_text = weekday
    start_of_week = requested_date - timedelta(days=requested_date.weekday())
    matching_date = start_of_week + timedelta(days=weekday_index)
    return _time_issue(
        "weekday_date_conflict",
        {
            "provided_date": requested_date.isoformat(),
            "provided_weekday": weekday_text,
            "actual_weekday": _VIETNAMESE_WEEKDAYS[requested_date.weekday()],
            "matching_weekday_date": matching_date.isoformat(),
        },
    )


def _time_issue(code: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "needs_clarification",
        "stage": "time",
        "code": code,
        "details": details,
    }
