"""Generic, deterministic HTML rendering for visualization templates."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from rag_manager.visualization.paths import resolve_output_dir


_TEMPLATE_ENVIRONMENT = SandboxedEnvironment(
    autoescape=select_autoescape(
        enabled_extensions=("html", "htm", "xml"),
        default_for_string=True,
        default=True,
    ),
    undefined=ChainableUndefined,
)
_TEMPLATE_ENVIRONMENT.globals.clear()


def render_template(template_html: str, answer: str, data: dict[str, Any]) -> str:
    """Render trusted fillable HTML with generic loops, conditions, and escaping.

    Domain-specific layout and repeated markup belong to template assets. The
    renderer only evaluates the fillable template against structured data and
    relies on Jinja autoescaping for every dynamic value.
    """

    if not isinstance(template_html, str):
        raise TypeError("template_html must be a string.")
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")

    template = _TEMPLATE_ENVIRONMENT.from_string(template_html)
    return template.render(
        answer=answer if isinstance(answer, str) else "",
        data=data,
        # Compatibility for existing fillable components. New templates should
        # use data.source so all domain values share one explicit root.
        source=data.get("source", {}),
    )


def save_visualization_output(html: str, output_dir: str | Path | None = None) -> Path:
    """Persist rendered HTML and return its path."""

    resolved_output_dir = resolve_output_dir(output_dir)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output_path = resolved_output_dir / f"visualization_{timestamp}.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path
