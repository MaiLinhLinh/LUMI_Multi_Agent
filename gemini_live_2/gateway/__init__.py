"""Domain-scoped capability gateway used by the Plan Agent."""

from .domain_gateway import (
    CapabilityDescriptor,
    CapabilityExecutionContext,
    DomainCapability,
    DomainGateway,
    GatewayConfigurationError,
    GatewayExecutionError,
    GatewayPermissionError,
)

__all__ = [
    "CapabilityDescriptor",
    "CapabilityExecutionContext",
    "DomainCapability",
    "DomainGateway",
    "GatewayConfigurationError",
    "GatewayExecutionError",
    "GatewayPermissionError",
]
