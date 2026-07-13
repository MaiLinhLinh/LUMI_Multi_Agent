"""Path helpers for visualization assets and runtime outputs."""

from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[2]
ASSETS_DIR = PACKAGE_DIR / "assets"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "visualizations"


def resolve_asset_path(*parts: str) -> Path:
    """Return a path inside the packaged visualization assets directory."""

    return ASSETS_DIR.joinpath(*parts)


def resolve_output_dir(output_dir: str | Path | None = None) -> Path:
    """Return the visualization output directory without depending on cwd."""

    if output_dir is None:
        return DEFAULT_OUTPUT_DIR
    return Path(output_dir).expanduser().resolve()

