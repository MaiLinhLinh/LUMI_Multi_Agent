"""Loaders for domain-owned resources exposed to the framework."""
from .assets import AssetCatalog, AssetCatalogError, AssetDescriptor, load_asset_catalog
from .domains import DomainManifest, DomainRegistry, DomainResources, ManifestError
from .layout_templates import (
    LayoutTemplate,
    LayoutTemplateError,
    LayoutTemplateMaterializer,
    TemplateBinding,
    TemplateExtractor,
)
from .templates import (
    TemplateCatalog,
    TemplateCatalogEntry,
    TemplateCatalogError,
    empty_template_catalog,
    load_template_catalog,
)

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
    "TemplateBinding",
    "TemplateExtractor",
    "TemplateCatalog",
    "TemplateCatalogEntry",
    "TemplateCatalogError",
    "empty_template_catalog",
    "load_asset_catalog",
    "load_template_catalog",
]
