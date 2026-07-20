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
_NORMALIZED_TIME_PATTERN = re.compile(
    r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$"
)
_NUMERIC_CLOCK_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2})\s*"
    r"(?:"
    r":\s*(?P<colon_minute>\d{1,2})"
    r"|(?:h|gio)(?:\s*(?P<marked_minute>\d{1,2}))?"
    r"|(?=(?:sang|trua|chieu|toi|dem|am|pm)\b)"
    r")"
)
_HALF_HOUR_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>\d{1,2})\s*(?:(?:h|gio)\s*)?ruoi\b"
)
_WORD_CLOCK_PATTERN = re.compile(
    rf"\b(?P<hour>{_NUMBER_TOKEN}(?:\s+{_NUMBER_TOKEN}){{0,2}})\s+gio\b"
    rf"(?:\s+(?P<minute>ruoi|{_NUMBER_TOKEN}(?:\s+{_NUMBER_TOKEN}){{0,2}}))?"
)
_CLOCK_PERIOD_PATTERN = re.compile(r"\b(sang|trua|chieu|toi|dem|am|pm)\b")


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
        normalized_time: str | None = None,
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
        raw_normalized_time = (
            normalized_time.strip()
            if isinstance(normalized_time, str)
            else ""
        )
        reference = self._reference_datetime(reference_datetime)
        reference_date = reference.date()
        normalized = _normalize_text(raw_text)
        candidate = _normalized_candidate(request_type_candidate)

        time_of_day, time_issue = _validate_time_of_day(
            raw_time_of_day,
            raw_normalized_time,
        )
        if time_issue is not None:
            return _time_issue(time_issue["code"], time_issue["details"])

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
            "unrecognized_date",
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


def _validate_time_of_day(
    raw_text: str,
    normalized_time: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not raw_text:
        if normalized_time:
            return None, {
                "code": "unexpected_normalized_time",
                "details": {"normalized_time": normalized_time},
            }
        return None, None
    if not normalized_time:
        return None, {
            "code": "missing_normalized_time",
            "details": {"requested_text": raw_text},
        }

    normalized_match = _NORMALIZED_TIME_PATTERN.fullmatch(normalized_time)
    if normalized_match is None:
        return None, {
            "code": "invalid_normalized_time",
            "details": {
                "requested_text": raw_text,
                "normalized_time": normalized_time,
                "required_format": "HH:MM",
            },
        }
    normalized_hour = int(normalized_match.group("hour"))
    normalized_minute = int(normalized_match.group("minute"))

    evidence = _extract_clock_evidence(raw_text)
    if evidence is None:
        return None, {
            "code": "normalized_time_without_explicit_clock",
            "details": {
                "requested_text": raw_text,
                "normalized_time": normalized_time,
            },
        }
    if not evidence["valid"]:
        return None, {
            "code": "invalid_time_of_day",
            "details": {
                "requested_text": raw_text,
                "normalized_time": normalized_time,
            },
        }

    evidence_time = f"{evidence['hour']:02d}:{evidence['minute']:02d}"
    if (normalized_hour, normalized_minute) != (
        evidence["hour"],
        evidence["minute"],
    ):
        return None, {
            "code": "normalized_time_conflict",
            "details": {
                "requested_text": raw_text,
                "normalized_time": normalized_time,
                "time_supported_by_text": evidence_time,
            },
        }

    return {
        "time_of_day_text": raw_text,
        "normalized_time": normalized_time,
        "requested_time_of_day": normalized_time,
        "forecast_interval_start_time": f"{normalized_hour:02d}:00",
        "requested_hour": normalized_hour,
        "requested_minute": normalized_minute,
        "forecast_interval_minutes": 60,
    }, None


def _extract_clock_evidence(value: str) -> dict[str, Any] | None:
    normalized = _normalize_text(value).strip(" .,!?")
    periods = set(_CLOCK_PERIOD_PATTERN.findall(normalized))
    if len(periods) > 1:
        return {"valid": False}
    period = next(iter(periods), None)

    half_match = _HALF_HOUR_PATTERN.search(normalized)
    if half_match:
        return _canonical_clock(int(half_match.group("hour")), 30, period)

    numeric_match = _NUMERIC_CLOCK_PATTERN.search(normalized)
    if numeric_match:
        minute_text = (
            numeric_match.group("colon_minute")
            or numeric_match.group("marked_minute")
        )
        return _canonical_clock(
            int(numeric_match.group("hour")),
            int(minute_text) if minute_text is not None else 0,
            period,
        )

    word_match = _WORD_CLOCK_PATTERN.search(normalized)
    if not word_match:
        return None
    hour = _parse_vietnamese_number(word_match.group("hour"))
    minute_text = word_match.group("minute")
    minute = (
        30
        if minute_text == "ruoi"
        else _parse_vietnamese_number(minute_text)
        if minute_text
        else 0
    )
    if hour is None or minute is None:
        return {"valid": False}
    return _canonical_clock(hour, minute, period)


def _canonical_clock(
    hour: int,
    minute: int,
    period: str | None,
) -> dict[str, Any]:
    if not 0 <= minute <= 59:
        return {"valid": False}
    if period in {"am", "sang"}:
        if not 1 <= hour <= 12:
            return {"valid": False}
        hour = 0 if hour == 12 else hour
    elif period in {"pm", "trua", "chieu", "toi"}:
        if not 1 <= hour <= 23:
            return {"valid": False}
        if 1 <= hour <= 11:
            hour += 12
    elif period == "dem":
        if not 1 <= hour <= 23:
            return {"valid": False}
        if hour == 12:
            hour = 0
        elif 6 <= hour <= 11:
            hour += 12
    elif not 0 <= hour <= 23:
        return {"valid": False}
    return {"valid": True, "hour": hour, "minute": minute}


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
    time_of_day_codes = {
        "invalid_time_of_day",
        "invalid_normalized_time",
        "missing_normalized_time",
        "normalized_time_conflict",
        "normalized_time_without_explicit_clock",
        "unexpected_normalized_time",
    }
    invalid_field = "time_of_day_text" if code in time_of_day_codes else "date_text"
    return {
        "status": "needs_clarification",
        "stage": "time",
        "code": code,
        "details": {"field": invalid_field, **details},
    }
