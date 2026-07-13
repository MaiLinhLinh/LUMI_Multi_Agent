"""Prompt asset loading helpers for Template Agent LLM calls."""

from __future__ import annotations

import json
import re
from typing import Any

from rag_manager.visualization.paths import resolve_asset_path


class PromptAssetError(ValueError):
    """Raised when a prompt asset cannot be loaded or rendered."""


PROMPT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")
VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


def load_template_agent_prompt(name: str) -> str:
    """Load a Template Agent prompt by asset name."""

    if not PROMPT_NAME_PATTERN.match(name):
        raise PromptAssetError(f"Invalid template agent prompt name: {name}")
    path = resolve_asset_path("prompts", "template_agent", f"{name}.txt")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptAssetError(f"Missing template agent prompt: {name}") from exc


def render_prompt(template: str, variables: dict[str, Any]) -> str:
    """Render a prompt template using explicit variables."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise PromptAssetError(f"Missing prompt variable: {key}")
        value = variables[key]
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    return VARIABLE_PATTERN.sub(replace, template)

