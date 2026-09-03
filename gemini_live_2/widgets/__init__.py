"""Domain-neutral widget registry used by planning and compilation."""

from .registry import (
    AnchorPolicy,
    WidgetAnchor,
    WidgetAssetReferenceDefinition,
    WidgetDefinition,
    WidgetInteractionDefinition,
    WidgetPropDefinition,
    WidgetPropsError,
    WidgetRegistry,
    WidgetStateDefinition,
    StageMapPolicy,
    StageMapTextSource,
    StageMapView,
    build_default_widget_registry,
)

__all__ = [
    "AnchorPolicy",
    "WidgetAnchor",
    "WidgetAssetReferenceDefinition",
    "WidgetDefinition",
    "WidgetInteractionDefinition",
    "WidgetPropDefinition",
    "WidgetPropsError",
    "WidgetRegistry",
    "WidgetStateDefinition",
    "StageMapPolicy",
    "StageMapTextSource",
    "StageMapView",
    "build_default_widget_registry",
]
