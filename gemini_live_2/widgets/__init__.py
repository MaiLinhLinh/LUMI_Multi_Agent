"""Domain-neutral widget registry used by planning and compilation."""

from .registry import (
    AnchorPolicy,
    WidgetAnchor,
    WidgetDefinition,
    WidgetPropDefinition,
    WidgetPropsError,
    WidgetRegistry,
    build_default_widget_registry,
)

__all__ = [
    "AnchorPolicy",
    "WidgetAnchor",
    "WidgetDefinition",
    "WidgetPropDefinition",
    "WidgetPropsError",
    "WidgetRegistry",
    "build_default_widget_registry",
]
