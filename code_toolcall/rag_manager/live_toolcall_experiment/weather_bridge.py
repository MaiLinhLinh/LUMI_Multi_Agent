"""Safe WeatherTools-to-Gemini-Live bridge for the experiment."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from rag_manager.llm.function_calling_runtime import GeminiFunctionCallingRuntime
from rag_manager.presentation.capabilities import load_template_metadata, presentation_capabilities
from rag_manager.presentation.domains.weather import WeatherPresentationAdapter
from rag_manager.presentation.schemas import CompiledPresentationPlan, GroundedFact
from rag_manager.tools.visual_tools import VisualTools
from rag_manager.tools.weather_tools import WeatherTools

from .prompts.weather import WEATHER_PRESENTATION_GUIDANCE
from .schemas import ActiveAnimationCapabilities, ActivePresentationScenes, SceneTrigger

logger = logging.getLogger("lumi.live_toolcall_experiment")

_PRESENT_ID = re.compile(r'data-present-id\s*=\s*["\']([^"\']+)["\']')


@dataclass
class WeatherLiveResult:
    tool_response: dict[str, Any]
    weather_context: dict[str, Any]
    panel: dict[str, Any] | None = None
    capabilities: ActiveAnimationCapabilities | None = None
    scenes: ActivePresentationScenes | None = None


class WeatherLiveBridge:
    """Reuse the existing deterministic Weather and visual implementations."""

    def __init__(
        self,
        weather: WeatherTools,
        visual: VisualTools,
        planner_runtime: GeminiFunctionCallingRuntime | None = None,
    ) -> None:
        self._weather = weather
        self._visual = visual
        self._adapter = WeatherPresentationAdapter()
        self._planner_runtime = planner_runtime

    def get_weather(
        self,
        args: dict[str, Any],
        weather_context: dict[str, Any],
        *,
        query: str,
        history: list[dict[str, Any]] | None = None,
    ) -> WeatherLiveResult:
        result = self._weather.get_weather(args, weather_context=weather_context)
        status = str(result.get("status") or "error")
        if status != "completed":
            return WeatherLiveResult(
                tool_response=self._compact_failure(result),
                weather_context=dict(weather_context),
            )

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        compact_data = self._visual.compact_weather_data(data)
        template_id = self._visual.select_weather_template(data)
        rendered = self._visual.render_visualization({"template_id": template_id}, compact_data)
        panel = rendered.get("data") if rendered.get("status") == "completed" else None
        if not isinstance(panel, dict) or not isinstance(panel.get("html"), str):
            return WeatherLiveResult(
                tool_response={"status": "error", "message": "Weather panel could not be rendered."},
                weather_context=dict(weather_context),
            )

        metadata = self._adapter.load_template_metadata(template_id)
        declared_capabilities = presentation_capabilities(metadata)
        capabilities = self._capabilities_from_declared(
            template_id,
            panel["html"],
            declared_capabilities,
        )
        domain_data = result.get("_llm_response", {}).get("weather_facts", {})
        domain_data = domain_data if isinstance(domain_data, dict) else {}
        grounded_facts = self._adapter.build_candidate_facts(
            domain_data,
            compact_data=compact_data,
            presentation_capabilities=declared_capabilities,
        )
        compiled_plan = self._build_compiled_plan(
            query=query,
            history=history,
            domain_data=domain_data,
            template_id=template_id,
            metadata=metadata,
            capabilities=declared_capabilities,
            compact_data=compact_data,
            grounded_facts=grounded_facts,
        )
        scenes = self._active_scenes(template_id, compiled_plan)
        logger.info(
            "[LIVE_EXPERIMENT:PLAN_READY] template=%s scenes=%s details=%s",
            template_id,
            len(scenes.scenes),
            [
                {
                    "scene_id": scene_id,
                    "target_id": scene["target_id"],
                    "effect": scene["effect"],
                    "narration_chars": len(str(scene["narration"])),
                }
                for scene_id, scene in scenes.scenes.items()
            ],
        )
        response = {
            "status": "completed",
            "domain_id": "weather",
            "request": {
                "location": data.get("location"),
                "date": data.get("requested_date"),
                "days": data.get("requested_days"),
                "request_type": data.get("request_type"),
            },
            "facts": self._live_fact_pack(
                grounded_facts,
                declared_capabilities=declared_capabilities,
                compact_data=compact_data,
                capabilities=capabilities,
            ),
            "presentation": {
                "template_id": template_id,
                "presentation_guidance": WEATHER_PRESENTATION_GUIDANCE,
                "presentation_plan": {
                    "schema_version": "lumi.live_scene_plan.v1",
                    "scene_count": len(scenes.scenes),
                    "current_scene": self.scene_instruction(scenes, 0),
                },
            },
        }
        return WeatherLiveResult(
            tool_response=response,
            weather_context=self._next_weather_context(result, weather_context),
            panel=panel,
            capabilities=capabilities,
            scenes=scenes,
        )

    @staticmethod
    def trigger_scene(
        args: dict[str, Any], scenes: ActivePresentationScenes | None, *, expected_scene_id: str | None
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if scenes is None:
            return {"status": "rejected", "reason": "no_active_presentation_plan"}, None
        try:
            trigger = SceneTrigger.model_validate(args)
        except Exception as exc:
            return {"status": "rejected", "reason": f"invalid_scene_trigger: {exc}"}, None
        scene = scenes.resolve(trigger.scene_id)
        if scene is None:
            return {"status": "rejected", "reason": "scene_not_in_active_plan"}, None
        if trigger.scene_id != expected_scene_id:
            logger.warning(
                "[LIVE_EXPERIMENT:SCENE_REJECTED] requested=%s expected=%s",
                trigger.scene_id,
                expected_scene_id,
            )
            return {"status": "rejected", "reason": "scene_not_next"}, None
        logger.info(
            "[LIVE_EXPERIMENT:SCENE_ACCEPTED] scene=%s target=%s effect=%s",
            trigger.scene_id,
            scene["target_id"],
            scene["effect"],
        )
        return {"status": "completed", "scene_id": trigger.scene_id}, scene

    def _build_compiled_plan(
        self,
        *,
        query: str,
        history: list[dict[str, Any]] | None,
        domain_data: dict[str, Any],
        template_id: str,
        metadata: dict[str, Any],
        capabilities: dict[str, Any],
        compact_data: dict[str, Any],
        grounded_facts: list[GroundedFact],
    ) -> CompiledPresentationPlan:
        if self._planner_runtime is None:
            raise RuntimeError("Live presentation planner runtime is unavailable.")
        planned = self._adapter.plan(
            self._planner_runtime,
            query=query,
            history=history,
            domain_data=domain_data,
            template_id=template_id,
            capabilities=capabilities,
            grounded_facts=grounded_facts,
        )
        return self._adapter.compile(
            planned["plan"],
            template_metadata=metadata,
            compact_data=compact_data,
            grounded_facts=grounded_facts,
        )

    @staticmethod
    def _active_scenes(
        template_id: str, compiled_plan: CompiledPresentationPlan
    ) -> ActivePresentationScenes:
        scenes: dict[str, dict[str, Any]] = {}
        for index, step in enumerate(compiled_plan.steps):
            scene_id = f"weather-scene-{index + 1}"
            scenes[scene_id] = {
                "scene_id": scene_id,
                "narration": step.narration,
                "spoken_text": step.spoken_text,
                "target_id": step.target_id,
                "effect": step.effect,
                "gesture": step.gesture,
                "actions": [action.model_dump(mode="json") for action in step.actions],
            }
        return ActivePresentationScenes(template_id=template_id, scenes=scenes)

    @staticmethod
    def scene_instruction(
        scenes: ActivePresentationScenes,
        index: int,
    ) -> dict[str, Any] | None:
        """Return one exact narration unit; the Live model never sees future scenes."""
        ordered = list(scenes.scenes.values())
        if index < 0 or index >= len(ordered):
            return None
        scene = ordered[index]
        return {
            "scene_id": scene["scene_id"],
            # Gemini Live must receive the deterministic speech form rather
            # than display narration, so it never has to guess how 05/08,
            # percentages, or weather units should be pronounced.
            "narration": scene.get("spoken_text") or scene["narration"],
        }

    @staticmethod
    def _compact_failure(result: dict[str, Any]) -> dict[str, Any]:
        clarification = result.get("clarification") if isinstance(result.get("clarification"), dict) else {}
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        return {
            "status": result.get("status", "error"),
            "clarification": clarification.get("question"),
            "error": error.get("message"),
        }

    @staticmethod
    def _next_weather_context(result: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if not data.get("location_id"):
            return dict(previous)
        next_context = {
            "last_location_id": data["location_id"],
            "last_location_name": data.get("location") or previous.get("last_location_name", ""),
            "last_request_type": data.get("request_type", "forecast"),
            "last_start_date": data.get("requested_date", ""),
            "last_days": data.get("requested_days", 1),
        }
        snapshot = result.get("_session_snapshot")
        if isinstance(snapshot, dict):
            next_context["session_snapshot"] = snapshot
        return next_context

    @staticmethod
    def _capabilities_from_declared(
        template_id: str,
        html: str,
        declared: dict[str, dict[str, Any]],
    ) -> ActiveAnimationCapabilities:
        target_ids = sorted({match.group(1) for match in _PRESENT_ID.finditer(html)})
        allowed: dict[str, list[str]] = {}
        for target_id in target_ids:
            effects: set[str] = set()
            for capability in declared.values():
                if not isinstance(capability, dict):
                    continue
                if target_id == capability.get("target_id") or WeatherLiveBridge._matches_pattern(
                    target_id, capability.get("target_pattern")
                ):
                    effects.update(
                        effect for effect in capability.get("allowed_effects", []) if isinstance(effect, str)
                    )
            if effects:
                allowed[target_id] = sorted(effects)
        return ActiveAnimationCapabilities(template_id=template_id, allowed=allowed)

    def _live_fact_pack(
        self,
        facts: list[GroundedFact],
        *,
        declared_capabilities: dict[str, dict[str, Any]],
        compact_data: dict[str, Any],
        capabilities: ActiveAnimationCapabilities,
    ) -> list[dict[str, Any]]:
        """Return trusted values plus only the cue that proves each fact on screen."""
        packed: list[dict[str, Any]] = []
        for fact in facts:
            item = fact.model_dump(mode="json", exclude_none=True)
            target_id = self._adapter.resolve_target(
                declared_capabilities.get(fact.focus),
                fact.entity,
                compact_data,
            )
            allowed_effects = capabilities.allowed.get(target_id or "", [])
            if target_id and allowed_effects:
                item["visual_cue"] = {
                    "target_id": target_id,
                    "allowed_effects": allowed_effects,
                }
            packed.append(item)
        return packed

    @staticmethod
    def _capabilities(template_id: str, html: str) -> ActiveAnimationCapabilities:
        """Compatibility helper retained for the isolated unit tests."""
        metadata = load_template_metadata("weather", template_id)
        return WeatherLiveBridge._capabilities_from_declared(
            template_id,
            html,
            presentation_capabilities(metadata),
        )

    @staticmethod
    def _matches_pattern(target_id: str, pattern: Any) -> bool:
        if not isinstance(pattern, str) or not pattern:
            return False
        escaped = re.escape(pattern)
        escaped = re.sub(r"\\\{[a-z_]+\\\}", r"[0-9]+", escaped)
        return re.fullmatch(escaped, target_id) is not None
