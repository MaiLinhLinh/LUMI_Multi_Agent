"""Domain-scoped capability gateway used by the Plan Agent."""

from .domain_gateway import (
    CapabilityDescriptor,
    DomainCapability,
    DomainGateway,
    GatewayConfigurationError,
    GatewayPermissionError,
)

__all__ = [
    "CapabilityDescriptor",
    "DomainCapability",
    "DomainGateway",
    "GatewayConfigurationError",
    "GatewayPermissionError",
]
