"""Enforce domain capability boundaries before a Plan Agent can access data.

This module deliberately contains no database or API implementation. A domain
registers a small handler, its manifest grants the handler's capability id, and
the gateway is the only common entry point that may execute it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import importlib.util
from typing import Any

from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
from gemini_live_2.panel.contracts import DataBundle


class GatewayConfigurationError(ValueError):
    """Raised for inconsistent registered capabilities and manifests."""


class GatewayPermissionError(ValueError):
    """Raised when a caller requests a capability outside its routed domain."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GatewayConfigurationError(f"{field} must be a non-empty string.")
    return value.strip()


def _arguments(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GatewayPermissionError("capability arguments must be an object.")
    return dict(value)


CapabilityHandler = Callable[[Mapping[str, Any]], DataBundle]


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """Safe public description passed to a future Plan Agent tool loop."""

    id: str
    description: str
    input_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "capability.id"))
        object.__setattr__(self, "description", _text(self.description, "capability.description"))
        if not isinstance(self.input_schema, Mapping):
            raise GatewayConfigurationError("capability.input_schema must be an object.")
        object.__setattr__(self, "input_schema", dict(self.input_schema))

    def for_plan_agent(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


@dataclass(frozen=True, slots=True)
class DomainCapability:
    """A backend-only executable capability registered for exactly one domain."""

    domain_id: str
    descriptor: CapabilityDescriptor
    handler: CapabilityHandler

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _text(self.domain_id, "capability.domain_id"))
        if not isinstance(self.descriptor, CapabilityDescriptor):
            raise GatewayConfigurationError("capability.descriptor must be a CapabilityDescriptor.")
        if not callable(self.handler):
            raise GatewayConfigurationError("capability.handler must be callable.")


class DomainGateway:
    """Expose only manifest-granted capabilities for one routed domain."""

    def __init__(self, domain_registry: DomainRegistry) -> None:
        self._domain_registry = domain_registry
        self._capabilities: dict[tuple[str, str], DomainCapability] = {}
        self._loaded_domain_tools: set[str] = set()

    def register(self, capability: DomainCapability) -> None:
        if not isinstance(capability, DomainCapability):
            raise GatewayConfigurationError("capability must be a DomainCapability.")
        key = (capability.domain_id, capability.descriptor.id)
        if key in self._capabilities:
            raise GatewayConfigurationError(
                f"capability '{capability.descriptor.id}' is already registered for '{capability.domain_id}'."
            )
        self._capabilities[key] = capability

    def capability_catalog(self, domain_id: str) -> tuple[CapabilityDescriptor, ...]:
        """Return only executable capabilities explicitly granted by the manifest."""

        resources = self._load_resources(domain_id)
        self._load_domain_tools(resources.manifest.domain_id)
        descriptors: list[CapabilityDescriptor] = []
        for capability_id in resources.manifest.tool_capabilities:
            capability = self._capabilities.get((resources.manifest.domain_id, capability_id))
            if capability is None:
                raise GatewayConfigurationError(
                    f"manifest grants '{capability_id}' for '{resources.manifest.domain_id}', "
                    "but no handler is registered."
                )
            descriptors.append(capability.descriptor)
        return tuple(descriptors)

    def empty_bundle(self, domain_id: str) -> DataBundle:
        """Create the explicit no-external-data bundle used by asset-only plans."""

        resources = self._load_resources(domain_id)
        self._load_domain_tools(resources.manifest.domain_id)
        return DataBundle(domain_id=resources.manifest.domain_id, data={})

    def execute(
        self,
        *,
        domain_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> DataBundle:
        """Run one manifest-granted handler and enforce its domain on the result."""

        resources = self._load_resources(domain_id)
        safe_capability_id = _text(capability_id, "capability_id")
        if safe_capability_id not in resources.manifest.tool_capabilities:
            raise GatewayPermissionError(
                f"capability '{safe_capability_id}' is not granted for domain '{resources.manifest.domain_id}'."
            )
        capability = self._capabilities.get((resources.manifest.domain_id, safe_capability_id))
        if capability is None:
            raise GatewayConfigurationError(
                f"manifest grants '{safe_capability_id}' for '{resources.manifest.domain_id}', "
                "but no handler is registered."
            )
        bundle = capability.handler(_arguments(arguments))
        if not isinstance(bundle, DataBundle):
            raise GatewayConfigurationError("capability handler must return a DataBundle.")
        if bundle.domain_id != resources.manifest.domain_id:
            raise GatewayConfigurationError("capability handler returned a DataBundle for another domain.")
        return bundle

    def _load_resources(self, domain_id: str):
        try:
            return self._domain_registry.load(domain_id)
        except ManifestError as exc:
            raise GatewayPermissionError(str(exc)) from exc

    def _load_domain_tools(self, domain_id: str) -> None:
        """Load a domain-owned ``tools.py`` once, when that domain is used.

        A future domain declares its executable capabilities in its own
        ``tools.py`` as ``CAPABILITIES``.  The manifest remains the permission
        boundary: importing a module never makes an undeclared tool callable.
        Existing explicit ``register()`` calls remain supported for tests and
        for embedding code that registers tools during application startup.
        """

        if domain_id in self._loaded_domain_tools:
            return
        resources = self._load_resources(domain_id)
        module_path = resources.domain_root / "tools.py"
        if not module_path.is_file():
            self._loaded_domain_tools.add(domain_id)
            return
        module_name = f"gemini_live_2_domain_tools_{domain_id}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise GatewayConfigurationError(f"cannot load tools.py for domain '{domain_id}'.")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise GatewayConfigurationError(
                f"cannot execute tools.py for domain '{domain_id}': {exc}"
            ) from exc
        declared = getattr(module, "CAPABILITIES", ())
        if not isinstance(declared, (tuple, list)):
            raise GatewayConfigurationError(
                f"domains/{domain_id}/tools.py must define CAPABILITIES as a tuple or list."
            )
        for capability in declared:
            if not isinstance(capability, DomainCapability):
                raise GatewayConfigurationError(
                    f"domains/{domain_id}/tools.py CAPABILITIES must contain DomainCapability values."
                )
            if capability.domain_id != domain_id:
                raise GatewayConfigurationError(
                    f"domains/{domain_id}/tools.py cannot register a capability for another domain."
                )
            self.register(capability)
        self._loaded_domain_tools.add(domain_id)
