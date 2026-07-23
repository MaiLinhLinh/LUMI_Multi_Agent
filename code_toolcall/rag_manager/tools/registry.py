"""Single source of truth for native Gemini FunctionDeclaration JSON schemas."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

_SOURCE = Path(__file__).with_name("function_declarations.json")
def declarations(domain: str) -> list[dict[str, Any]]:
    data = json.loads(_SOURCE.read_text(encoding="utf-8"))
    values = data.get(domain, [])
    if not isinstance(values, list): raise ValueError(f"Invalid tool schema domain: {domain}")
    return values
