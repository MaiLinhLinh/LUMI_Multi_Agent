"""Weather-specific verified-fact extraction."""

from __future__ import annotations

from typing import Any

from gemini_live.presentation.base import DomainPresentationAdapter
from gemini_live.presentation.planner_schemas import GroundedFact
from .prompt import WEATHER_PRESENTATION_INSTRUCTION

class WeatherPresentationAdapter(DomainPresentationAdapter):
    @property
    def domain_id(self) -> str:
        return "weather"

    def live_presentation_instruction(self) -> str:
        return WEATHER_PRESENTATION_INSTRUCTION

    def build_candidate_facts(
        self,
        domain_data: dict[str, Any],
        *,
        compact_data: dict[str, Any],
        presentation_capabilities: dict[str, Any],
    ) -> list[GroundedFact]:
        days = [day for day in domain_data.get("days", []) if isinstance(day, dict)]
        if len(days) == 1:
            return self._daily_candidates(days[0], compact_data, presentation_capabilities)
        if len(days) >= 2:
            return self._multi_day_candidates(days)
        return []

    def _multi_day_candidates(self, days: list[dict[str, Any]]) -> list[GroundedFact]:
        """Create a bounded fact pack usable for 2, 3, or 7+ forecast days."""
        facts: list[GroundedFact] = []
        rain = self._numeric_days(days, "rain_max_pct")
        rain_amount = self._numeric_days(days, "rain_total_mm")
        maximums = self._numeric_days(days, "max_c")
        minimums = self._numeric_days(days, "min_c")
        if rain:
            facts.append(GroundedFact(
                id="period_rain_coverage", metric="rain_probability", operation="summary",
                value={"days_at_or_above_pct": sum(value >= 50 for _, _, value in rain), "total_days": len(days), "threshold_pct": 50},
                unit="%", focus="overview",
            ))
            facts.append(self._day_fact("period_rain_probability_peak", "rain_probability", "argmax", *max(rain, key=lambda item: item[2]), "rain_risk", "%"))
            if len(rain) >= 2:
                facts.append(self._day_fact("period_rain_probability_low", "rain_probability", "argmin", *min(rain, key=lambda item: item[2]), "rain_risk", "%"))
        periods = self._condition_periods(days)
        if periods:
            facts.append(GroundedFact(
                id="period_condition_groups", metric="condition", operation="summary",
                value={"periods": periods, "total_days": len(days)}, focus="weekly_rain_pattern",
            ))
        if rain_amount:
            facts.append(self._day_fact("period_rain_amount_peak", "rain_amount", "argmax", *max(rain_amount, key=lambda item: item[2]), "day_summary", "mm"))
            if len(rain_amount) >= 2:
                facts.append(self._day_fact("period_rain_amount_low", "rain_amount", "argmin", *min(rain_amount, key=lambda item: item[2]), "day_summary", "mm"))
        if maximums or minimums:
            value: dict[str, Any] = {"total_days": len(days)}
            if maximums:
                value["max_temperature_range_c"] = [min(x[2] for x in maximums), max(x[2] for x in maximums)]
            if minimums:
                value["min_temperature_range_c"] = [min(x[2] for x in minimums), max(x[2] for x in minimums)]
            facts.append(GroundedFact(
                id="period_temperature_range", metric="temperature_max", operation="summary", value=value,
                unit="°C", focus="temperature_trend",
            ))
        if maximums:
            facts.append(self._day_fact("period_temperature_peak", "temperature_max", "argmax", *max(maximums, key=lambda item: item[2]), "temperature_peak", "°C"))
        if minimums:
            facts.append(self._day_fact("period_temperature_low", "temperature_min", "argmin", *min(minimums, key=lambda item: item[2]), "temperature_peak", "°C"))
        return facts

    def _daily_candidates(
        self, day: dict[str, Any], compact_data: dict[str, Any], capabilities: dict[str, Any]
    ) -> list[GroundedFact]:
        facts: list[GroundedFact] = []
        entity = {"day_index": 0, "date": day.get("date")}
        condition = day.get("condition")
        if isinstance(condition, str) and condition.strip():
            focus = self._supported_focus(("day_summary", "overview"), capabilities)
            if focus:
                facts.append(GroundedFact(id="day_condition_overview", metric="condition", operation="summary", value={"date": day.get("date"), "condition": condition.strip()}, entity=entity if focus == "day_summary" else {}, focus=focus))
        temperature = {key: value for key, value in {"min_c": day.get("min_c"), "max_c": day.get("max_c"), "feels_like_c": day.get("max_feels_c")}.items() if self._is_number(value)}
        if temperature:
            focus = self._supported_focus(("temperature", "day_summary", "overview"), capabilities)
            if focus:
                facts.append(GroundedFact(id="day_temperature_range", metric="temperature_max", operation="summary", value=temperature, unit="°C", entity=entity if focus != "overview" else {}, focus=focus))
        rain = {key: value for key, value in {"max_probability_pct": day.get("rain_max_pct"), "total_mm": day.get("rain_total_mm")}.items() if self._is_number(value)}
        if rain:
            focus = self._supported_focus(("rain_risk", "day_summary", "overview"), capabilities)
            if focus:
                facts.append(GroundedFact(id="day_rain_summary", metric="rain_probability", operation="summary", value=rain, unit="%", entity=entity if focus != "overview" else {}, focus=focus))
        for fact_id, metric, field, unit, preferred_focus in (
            ("day_humidity", "humidity", "humidity_avg_pct", "%", ("humidity", "day_summary", "overview")),
            ("day_wind", "wind_speed", "wind_avg_ms", "m/s", ("wind", "day_summary", "overview")),
            ("day_pressure", "pressure", "pressure_avg_hpa", "hPa", ("pressure", "day_summary", "overview")),
        ):
            value = day.get(field)
            focus = self._supported_focus(preferred_focus, capabilities)
            if self._is_number(value) and focus:
                facts.append(GroundedFact(
                    id=fact_id, metric=metric, operation="lookup", value=value, unit=unit,
                    entity=entity if focus != "overview" else {}, focus=focus,
                ))
        intervals = self._intervals(compact_data)
        facts.extend(self._hourly_rain_peak(intervals, capabilities))
        return facts

    def _hourly_rain_peak(self, intervals: list[dict[str, Any]], capabilities: dict[str, Any]) -> list[GroundedFact]:
        facts: list[GroundedFact] = []
        rain = [(i, self._percentage(item["rain_probability"])) for i, item in enumerate(intervals) if self._is_number(item.get("rain_probability"))]
        if rain and "hourly_rain_risk" in capabilities:
            i, value = max(rain, key=lambda pair: pair[1])
            facts.append(GroundedFact(id="hourly_rain_probability_peak", metric="rain_probability", operation="argmax", value=value, unit="%", entity={"day_index": 0, "interval_index": i, "time": intervals[i].get("time")}, focus="hourly_rain_risk"))
        return facts

    @staticmethod
    def _day_fact(fid: str, metric: str, operation: str, index: int, day: dict[str, Any], value: float, focus: str, unit: str) -> GroundedFact:
        return GroundedFact(id=fid, metric=metric, operation=operation, value=value, unit=unit, entity={"day_index": index, "date": day.get("date")}, focus=focus)

    @staticmethod
    def _numeric_days(days: list[dict[str, Any]], field: str) -> list[tuple[int, dict[str, Any], float]]:
        return [(i, day, float(day[field])) for i, day in enumerate(days) if WeatherPresentationAdapter._is_number(day.get(field))]
    @staticmethod
    def _is_number(value: Any) -> bool: return isinstance(value, (int, float)) and not isinstance(value, bool)
    @staticmethod
    def _percentage(value: int | float) -> float: return round(float(value) * 100 if 0 <= value <= 1 else float(value), 1)
    @staticmethod
    def _supported_focus(preferred: tuple[str, ...], capabilities: dict[str, Any]) -> str | None:
        return next((focus for focus in preferred if isinstance(capabilities.get(focus), dict)), None)

    @staticmethod
    def _days(compact_data: dict[str, Any]) -> list[Any]:
        raw = compact_data.get("weather", compact_data) if isinstance(compact_data, dict) else {}
        forecast = raw.get("forecast", raw) if isinstance(raw, dict) else {}
        days = forecast.get("days", []) if isinstance(forecast, dict) else []
        return days if isinstance(days, list) else []

    @classmethod
    def _intervals(cls, compact_data: dict[str, Any]) -> list[dict[str, Any]]:
        days = cls._days(compact_data)
        intervals = days[0].get("intervals", []) if days and isinstance(days[0], dict) else []
        return [item for item in intervals if isinstance(item, dict)] if isinstance(intervals, list) else []
    @staticmethod
    def _condition_periods(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
        periods: list[dict[str, Any]] = []
        for index, day in enumerate(days):
            condition = day.get("condition")
            if not isinstance(condition, str) or not condition.strip(): continue
            normalized = condition.strip().casefold()
            if periods and periods[-1]["_normalized"] == normalized and periods[-1]["end_index"] == index - 1:
                periods[-1].update(end_index=index, end_date=day.get("date"), day_count=periods[-1]["day_count"] + 1)
            else:
                periods.append({"_normalized": normalized, "condition": condition.strip(), "start_index": index, "end_index": index, "start_date": day.get("date"), "end_date": day.get("date"), "day_count": 1})
        for period in periods: period.pop("_normalized", None)
        return periods
