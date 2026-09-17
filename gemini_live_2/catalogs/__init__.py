"""Loaders for domain prompts and shared resources exposed to the framework."""
from .assets import AssetCatalog, AssetCatalogError, AssetDescriptor, load_asset_catalog
from .domains import DomainManifest, DomainRegistry, DomainResources, ManifestError
from .layout_templates import (
    LayoutTemplate,
    LayoutTemplateError,
    LayoutTemplateMaterializer,
    TemplateComponentContract,
    TemplateBinding,
    TemplateExtractor,
    TemplateSpec,
)
from .templates import (
    TemplateCatalog,
    TemplateCatalogEntry,
    TemplateCatalogError,
    load_template_catalog,
)
from .resources import SharedResourceError, SharedResourceRegistry, SharedResources

__all__ = [
    "AssetCatalog",
    "AssetCatalogError",
    "AssetDescriptor",
    "DomainManifest",
    "DomainRegistry",
    "DomainResources",
    "ManifestError",
    "LayoutTemplate",
    "LayoutTemplateError",
    "LayoutTemplateMaterializer",
    "TemplateComponentContract",
    "TemplateBinding",
    "TemplateExtractor",
    "TemplateSpec",
    "TemplateCatalog",
    "TemplateCatalogEntry",
    "TemplateCatalogError",
    "load_asset_catalog",
    "load_template_catalog",
    "SharedResourceError",
    "SharedResourceRegistry",
    "SharedResources",
]
