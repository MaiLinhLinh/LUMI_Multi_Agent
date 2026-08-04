"""Weather-specific fact extraction and safe HTML target resolution."""

from __future__ import annotations

import re
from typing import Any

from gemini_live.presentation.base import DomainPresentationAdapter
from gemini_live.presentation.planner_runtime import fallback_presentation_plan
from gemini_live.presentation.planner_schemas import GroundedFact, PresentationPlan
from .prompt import WEATHER_PRESENTATION_SYSTEM

_TARGET_ID_RE = re.compile(r"^[a-z][a-z0-9._-]*$")


class WeatherPresentationAdapter(DomainPresentationAdapter):
    @property
    def domain_id(self) -> str:
        return "weather"

    def planner_guidance(self) -> str:
        return WEATHER_PRESENTATION_SYSTEM

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
                unit="%", focus="overview", effect_hint="reveal",
            ))
            facts.append(self._day_fact("period_rain_probability_peak", "rain_probability", "argmax", *max(rain, key=lambda item: item[2]), "rain_risk", "draw_circle", "%"))
            facts.append(self._day_fact("period_rain_probability_low", "rain_probability", "argmin", *min(rain, key=lambda item: item[2]), "rain_risk", "draw_circle", "%"))
        periods = self._condition_periods(days)
        if periods:
            facts.append(GroundedFact(
                id="period_condition_groups", metric="condition", operation="summary",
                value={"periods": periods, "total_days": len(days)}, focus="weekly_rain_pattern", effect_hint="highlight",
                visual_evidence={"kind": "day_groups", "groups": [
                    {"day_indices": list(range(item["start_index"], item["end_index"] + 1)), "label": item["condition"]}
                    for item in periods
                ]},
            ))
        if rain_amount:
            facts.append(self._day_fact("period_rain_amount_peak", "rain_amount", "argmax", *max(rain_amount, key=lambda item: item[2]), "day_summary", "draw_circle", "mm"))
        if maximums or minimums:
            value: dict[str, Any] = {"total_days": len(days)}
            if maximums:
                value["max_temperature_range_c"] = [min(x[2] for x in maximums), max(x[2] for x in maximums)]
            if minimums:
                value["min_temperature_range_c"] = [min(x[2] for x in minimums), max(x[2] for x in minimums)]
            facts.append(GroundedFact(
                id="period_temperature_range", metric="temperature_max", operation="summary", value=value,
                unit="°C", focus="temperature_trend", effect_hint="trace_line",
                visual_evidence={"kind": "temperature_range", "max_range_c": value.get("max_temperature_range_c"), "min_range_c": value.get("min_temperature_range_c")},
            ))
        if len(maximums) >= 2:
            first, last = maximums[0], maximums[-1]
            delta = round(last[2] - first[2], 1)
            facts.append(GroundedFact(
                id="period_temperature_trend", metric="temperature_max", operation="trend",
                value={"start_c": first[2], "end_c": last[2], "delta_c": delta, "direction": "increase" if delta >= .5 else "decrease" if delta <= -.5 else "stable", "start_date": first[1].get("date"), "end_date": last[1].get("date")},
                unit="°C", focus="temperature_trend", effect_hint="trace_line",
                visual_evidence={"kind": "chart_segment", "point_indices": [index for index, _, _ in maximums]},
            ))
            facts.append(self._day_fact("period_temperature_peak", "temperature_max", "argmax", *max(maximums, key=lambda item: item[2]), "temperature_peak", "draw_circle", "°C"))
        if minimums:
            facts.append(self._day_fact("period_temperature_low", "temperature_min", "argmin", *min(minimums, key=lambda item: item[2]), "temperature_peak", "draw_circle", "°C"))
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
                facts.append(GroundedFact(id="day_condition_overview", metric="condition", operation="summary", value={"date": day.get("date"), "condition": condition.strip()}, entity=entity if focus == "day_summary" else {}, focus=focus, effect_hint="highlight"))
        temperature = {key: value for key, value in {"min_c": day.get("min_c"), "max_c": day.get("max_c"), "feels_like_c": day.get("max_feels_c")}.items() if self._is_number(value)}
        if temperature:
            focus = self._supported_focus(("temperature", "day_summary", "overview"), capabilities)
            if focus:
                facts.append(GroundedFact(id="day_temperature_range", metric="temperature_max", operation="summary", value=temperature, unit="°C", entity=entity if focus != "overview" else {}, focus=focus, effect_hint="draw_circle"))
        rain = {key: value for key, value in {"max_probability_pct": day.get("rain_max_pct"), "total_mm": day.get("rain_total_mm")}.items() if self._is_number(value)}
        if rain:
            focus = self._supported_focus(("rain_risk", "day_summary", "overview"), capabilities)
            if focus:
                facts.append(GroundedFact(id="day_rain_summary", metric="rain_probability", operation="summary", value=rain, unit="%", entity=entity if focus != "overview" else {}, focus=focus, effect_hint="draw_circle"))
        for field, metric, focus, unit in (("wind_avg_ms", "wind", "wind", "m/s"), ("humidity_avg_pct", "humidity", "humidity", "%")):
            if self._is_number(day.get(field)) and focus in capabilities:
                facts.append(GroundedFact(id=f"day_{metric}", metric=metric, operation="summary", value=day[field], unit=unit, entity=entity, focus=focus, effect_hint="highlight"))
        intervals = self._intervals(compact_data)
        facts.extend(self._hourly_extrema(intervals, capabilities))
        phase = self._daily_phase_fact(day, intervals, capabilities)
        if phase:
            facts.append(phase)
        return facts

    def _hourly_extrema(self, intervals: list[dict[str, Any]], capabilities: dict[str, Any]) -> list[GroundedFact]:
        facts: list[GroundedFact] = []
        rain = [(i, self._percentage(item["rain_probability"])) for i, item in enumerate(intervals) if self._is_number(item.get("rain_probability"))]
        temp = [(i, float(item["temperature_celsius"])) for i, item in enumerate(intervals) if self._is_number(item.get("temperature_celsius"))]
        if rain and "hourly_rain_risk" in capabilities:
            i, value = max(rain, key=lambda pair: pair[1])
            facts.append(GroundedFact(id="hourly_rain_probability_peak", metric="rain_probability", operation="argmax", value=value, unit="%", entity={"day_index": 0, "interval_index": i, "time": intervals[i].get("time")}, focus="hourly_rain_risk", effect_hint="draw_circle"))
        if temp and "hourly_temperature" in capabilities:
            i, value = max(temp, key=lambda pair: pair[1])
            facts.append(GroundedFact(id="hourly_temperature_peak", metric="temperature_max", operation="argmax", value=value, unit="°C", entity={"day_index": 0, "interval_index": i, "time": intervals[i].get("time")}, focus="hourly_temperature", effect_hint="draw_circle"))
            i, value = min(temp, key=lambda pair: pair[1])
            facts.append(GroundedFact(id="hourly_temperature_low", metric="temperature_min", operation="argmin", value=value, unit="°C", entity={"day_index": 0, "interval_index": i, "time": intervals[i].get("time")}, focus="hourly_temperature", effect_hint="draw_circle"))
        return facts

    def _daily_phase_fact(self, day: dict[str, Any], intervals: list[dict[str, Any]], capabilities: dict[str, Any]) -> GroundedFact | None:
        focus = self._supported_focus(("temperature_trend",), capabilities)
        if not focus:
            return None
        phases = []
        for name, start, end in (("morning", 5, 10), ("noon", 11, 14), ("afternoon", 15, 18), ("evening", 19, 23)):
            selected = [item for item in intervals if (hour := self._hour(item.get("time"))) is not None and start <= hour <= end]
            temps = [float(item["temperature_celsius"]) for item in selected if self._is_number(item.get("temperature_celsius"))]
            rain = [self._percentage(item["rain_probability"]) for item in selected if self._is_number(item.get("rain_probability"))]
            if temps or rain:
                value = {"period": name, "start_hour": start, "end_hour": end}
                if temps: value["temperature_range_c"] = [min(temps), max(temps)]
                if rain: value["max_rain_probability_pct"] = max(rain)
                phases.append(value)
        return GroundedFact(id="day_time_phases", metric="condition", operation="summary", value={"date": day.get("date"), "phases": phases}, focus=focus, effect_hint="draw_arrow") if len(phases) >= 2 else None

    def fallback_plan(self, domain_data: dict[str, Any], capabilities: dict[str, Any], grounded_facts: list[GroundedFact]) -> PresentationPlan:
        return fallback_presentation_plan(capabilities=capabilities, grounded_facts=grounded_facts, fallback_narration="Dữ liệu thời tiết đã sẵn sàng. Tôi sẽ tóm tắt các chỉ số chính trên bảng.")

    def resolve_target(self, capability: dict[str, Any] | None, entity: dict[str, Any], compact_data: dict[str, Any]) -> str | None:
        if not capability: return None
        target_id = capability.get("target_id")
        if isinstance(target_id, str) and _TARGET_ID_RE.fullmatch(target_id): return target_id
        pattern, fields = capability.get("target_pattern"), capability.get("entity_fields")
        if not isinstance(pattern, str) or not isinstance(fields, list): return None
        placeholders = re.findall(r"\{([a-z_]+)\}", pattern)
        if fields != placeholders or any(field not in {"day_index", "interval_index"} for field in fields): return None
        values = {field: entity.get(field) for field in fields}
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()): return None
        fixed = capability.get("fixed_entity", {})
        if not isinstance(fixed, dict) or any(values.get(key) != value for key, value in fixed.items()): return None
        days = self._days(compact_data); day_index = values.get("day_index")
        if day_index is not None and day_index >= len(days): return None
        if "interval_index" in values:
            intervals = days[day_index].get("intervals", []) if day_index is not None and isinstance(days[day_index], dict) else []
            if not isinstance(intervals, list) or values["interval_index"] >= len(intervals): return None
        concrete = pattern
        for field, value in values.items(): concrete = concrete.replace(f"{{{field}}}", str(value))
        return concrete if _TARGET_ID_RE.fullmatch(concrete) else None

    @staticmethod
    def _day_fact(fid: str, metric: str, operation: str, index: int, day: dict[str, Any], value: float, focus: str, effect: str, unit: str) -> GroundedFact:
        return GroundedFact(id=fid, metric=metric, operation=operation, value=value, unit=unit, entity={"day_index": index, "date": day.get("date")}, focus=focus, effect_hint=effect)
    @staticmethod
    def _numeric_days(days: list[dict[str, Any]], field: str) -> list[tuple[int, dict[str, Any], float]]:
        return [(i, day, float(day[field])) for i, day in enumerate(days) if WeatherPresentationAdapter._is_number(day.get(field))]
    @staticmethod
    def _is_number(value: Any) -> bool: return isinstance(value, (int, float)) and not isinstance(value, bool)
    @staticmethod
    def _percentage(value: int | float) -> float: return round(float(value) * 100 if 0 <= value <= 1 else float(value), 1)
    @staticmethod
    def _hour(value: Any) -> int | None:
        try: hour = int(value[:2]) if isinstance(value, str) else -1
        except ValueError: return None
        return hour if 0 <= hour <= 23 else None
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
