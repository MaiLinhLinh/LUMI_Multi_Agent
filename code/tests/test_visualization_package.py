from pathlib import Path

from rag_manager import visualization
from rag_manager.visualization import paths


def test_visualization_package_imports() -> None:
    assert visualization.__doc__


def test_visualization_paths_resolve_from_package() -> None:
    assert paths.PACKAGE_DIR.name == "visualization"
    assert paths.ASSETS_DIR == paths.PACKAGE_DIR / "assets"
    assert paths.resolve_asset_path("schemas") == paths.ASSETS_DIR / "schemas"


def test_visualization_default_output_dir_is_project_scoped() -> None:
    output_dir = paths.resolve_output_dir()

    assert output_dir == paths.PROJECT_ROOT / "outputs" / "visualizations"
    assert output_dir.is_absolute()


def test_visualization_custom_output_dir_resolves_absolute(tmp_path: Path) -> None:
    assert paths.resolve_output_dir(tmp_path) == tmp_path.resolve()

