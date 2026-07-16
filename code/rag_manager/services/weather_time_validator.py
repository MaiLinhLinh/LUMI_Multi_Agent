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
_NUMBER_TOKEN = r"(?:\d+|khong|mot|hai|ba|bon|tu|nam|lam|sau|bay|tam|chin|muoi)"
_RELATIVE_AFTER_PATTERN = re.compile(
    rf"(?<!thu )\b(?P<number>{_NUMBER_TOKEN}(?:\s+{_NUMBER_TOKEN}){{0,2}})\s+"
    r"(?:ngay|hom|bua)\s+"
    r"(?P<relation>sap\s+toi|tiep\s+theo|ke\s+tiep|toi|nua|sau)\b"
)
_RELATIVE_BEFORE_PATTERN = re.compile(
    rf"\bsau\s+(?P<number>{_NUMBER_TOKEN}(?:\s+{_NUMBER_TOKEN}){{0,2}})\s+"
    r"(?:ngay|hom|bua)\b"
)
_VIETNAMESE_NUMBER_UNITS = {
    "khong": 0,
    "mot": 1,
    "hai": 2,
    "ba": 3,
    "bon": 4,
    "tu": 4,
    "nam": 5,
    "lam": 5,
    "sau": 6,
    "bay": 7,
    "tam": 8,
    "chin": 9,
}
_TIME_OF_DAY_PATTERN = re.compile(
    r"^(?:(?:vao\s+)?luc\s+|vao\s+)?"
    r"(?P<hour>\d{1,2})\s*"
    r"(?:(?P<separator>:|h|gio)\s*(?P<minute>\d{1,2})?)?\s*"
    r"(?:phut\s*)?"
    r"(?P<period>sang|trua|chieu|toi|dem|am|pm)?$"
)


class WeatherTimeValidator:
    """Validate a Vietnamese date and optional clock time for cached weather."""

    def __init__(
        self,
        *,
        timezone_name: str = WEATHER_TIMEZONE,
        max_forecast_days: int = MAX_FORECAST_DAYS,
        forecast_horizon_days: int | None = None,
    ) -> None:
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        self.max_forecast_days = max(1, int(max_forecast_days))
        self.forecast_horizon_days = max(
            1,
            int(
                forecast_horizon_days
                if forecast_horizon_days is not None
                else self.max_forecast_days
            ),
        )

    def validate(
        self,
        date_text: str | None,
        *,
        time_of_day_text: str | None = None,
        request_type_candidate: str | None = None,
        reference_datetime: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a valid canonical time request or a clarification result."""

        raw_text = date_text.strip() if isinstance(date_text, str) else ""
        raw_time_of_day = (
            time_of_day_text.strip()
            if isinstance(time_of_day_text, str)
            else ""
        )
        reference = self._reference_datetime(reference_datetime)
        reference_date = reference.date()
        normalized = _normalize_text(raw_text)
        candidate = _normalized_candidate(request_type_candidate)

        time_of_day: dict[str, Any] | None = None
        if raw_time_of_day:
            time_of_day = _extract_time_of_day(raw_time_of_day)
            if time_of_day is None:
                return _time_issue(
                    "invalid_time_of_day",
                    {"requested_text": raw_time_of_day},
                )

        if not raw_text:
            if candidate == "current" and time_of_day is None:
                return self._valid_current(reference)
            return _time_issue(
                "missing_date",
                {
                    "requested_text": raw_text,
                    "time_of_day_text": raw_time_of_day or None,
                },
            )

        if _contains_current_expression(normalized) and time_of_day is None:
            return self._valid_current(reference)

        relative_request = _extract_relative_request(normalized)
        if relative_request is not None:
            relation, quantity = relative_request
            if relation == "range":
                start_date = reference_date + timedelta(days=1)
                requested_days = quantity
            else:
                start_date = reference_date + timedelta(days=quantity)
                requested_days = 1
            return self._valid_forecast_or_range_issue(
                start_date=start_date,
                days=requested_days,
                reference=reference,
                time_of_day=time_of_day,
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
                time_of_day=time_of_day,
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
                time_of_day=time_of_day,
            )

        if _contains_today_expression(normalized) or (
            _contains_current_expression(normalized) and time_of_day is not None
        ):
            weekday_issue = _weekday_date_issue(weekday, reference_date)
            if weekday_issue:
                return weekday_issue
            return self._valid_forecast_or_range_issue(
                start_date=reference_date,
                days=1,
                reference=reference,
                time_of_day=time_of_day,
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
                time_of_day=time_of_day,
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
                time_of_day=time_of_day,
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
                time_of_day=time_of_day,
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
        time_of_day: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reference_date = reference.date()
        calculated_end = end_date or start_date + timedelta(days=max(days - 1, 0))
        if days < 1:
            return _time_issue(
                "invalid_date_range",
                {
                    "start_date": start_date.isoformat(),
                    "days": days,
                },
            )
        if start_date < reference_date:
            return _time_issue(
                "forecast_date_in_past",
                {
                    "start_date": start_date.isoformat(),
                    "reference_date": reference_date.isoformat(),
                },
            )
        if days > self.max_forecast_days:
            return _time_issue(
                "forecast_range_exceeded",
                {
                    "start_date": start_date.isoformat(),
                    "end_date": calculated_end.isoformat(),
                    "requested_days": days,
                    "max_forecast_days": self.max_forecast_days,
                },
            )
        forecast_horizon_end = reference_date + timedelta(
            days=self.forecast_horizon_days
        )
        if calculated_end > forecast_horizon_end:
            return _time_issue(
                "forecast_horizon_exceeded",
                {
                    "start_date": start_date.isoformat(),
                    "end_date": calculated_end.isoformat(),
                    "forecast_horizon_end": forecast_horizon_end.isoformat(),
                    "forecast_horizon_days": self.forecast_horizon_days,
                },
            )
        result = {
            "status": "valid",
            "request_type": "forecast",
            "start_date": start_date.isoformat(),
            "days": days,
            "reference_datetime": reference.isoformat(),
            "timezone": self.timezone_name,
        }
        if time_of_day is not None:
            result.update(time_of_day)
        return result


def _normalized_candidate(value: str | None) -> str | None:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    return normalized if normalized in {"current", "forecast"} else None


def _extract_time_of_day(value: str) -> dict[str, Any] | None:
    normalized = _normalize_text(value).strip(" .,!?")
    match = _TIME_OF_DAY_PATTERN.fullmatch(normalized)
    if not match:
        return None

    separator = match.group("separator")
    minute_text = match.group("minute")
    period = match.group("period")
    if separator is None and period is None:
        return None

    hour = int(match.group("hour"))
    minute = int(minute_text) if minute_text is not None else 0
    if minute > 59:
        return None

    if period in {"am", "pm", "sang", "trua", "chieu", "toi", "dem"}:
        if not 1 <= hour <= 12:
            return None
        if period in {"am", "sang"}:
            hour = 0 if hour == 12 else hour
        elif period in {"pm", "trua", "chieu", "toi"}:
            hour = hour if hour == 12 else hour + 12
        elif period == "dem":
            if hour == 12:
                hour = 0
            elif hour >= 6:
                hour += 12
    elif hour > 23:
        return None

    return {
        "time_of_day_text": value.strip(),
        "requested_time_of_day": f"{hour:02d}:{minute:02d}",
        "forecast_interval_start_time": f"{hour:02d}:00",
        "requested_hour": hour,
        "requested_minute": minute,
        "forecast_interval_minutes": 60,
    }


def _extract_relative_request(value: str) -> tuple[str, int] | None:
    after_match = _RELATIVE_AFTER_PATTERN.search(value)
    if after_match:
        quantity = _parse_vietnamese_number(after_match.group("number"))
        if quantity is None:
            return None
        relation = after_match.group("relation")
        request_kind = (
            "range"
            if relation in {"toi", "sap toi", "tiep theo", "ke tiep"}
            else "date"
        )
        return request_kind, quantity

    before_match = _RELATIVE_BEFORE_PATTERN.search(value)
    if before_match:
        quantity = _parse_vietnamese_number(before_match.group("number"))
        if quantity is not None:
            return "date", quantity
    return None


def _parse_vietnamese_number(value: str) -> int | None:
    normalized = " ".join(value.split())
    if normalized.isdigit():
        return int(normalized)
    tokens = normalized.split()
    if len(tokens) == 1:
        if tokens[0] == "muoi":
            return 10
        return _VIETNAMESE_NUMBER_UNITS.get(tokens[0])
    if "muoi" not in tokens or tokens.count("muoi") != 1:
        return None
    marker = tokens.index("muoi")
    if marker == 0:
        tens = 1
    elif marker == 1:
        tens = _VIETNAMESE_NUMBER_UNITS.get(tokens[0], -1)
        if tens < 1:
            return None
    else:
        return None
    if len(tokens) == marker + 1:
        units = 0
    elif len(tokens) == marker + 2:
        units = _VIETNAMESE_NUMBER_UNITS.get(tokens[-1], -1)
        if units < 0:
            return None
    else:
        return None
    return tens * 10 + units


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
