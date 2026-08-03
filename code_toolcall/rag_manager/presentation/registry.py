"""Allow-listed registry for presentation domain adapters."""

from __future__ import annotations

from .domain_adapter import PresentationDomainAdapter
from .domains.weather import WeatherPresentationAdapter


class PresentationRegistry:
    def __init__(self, adapters: list[PresentationDomainAdapter] | None = None) -> None:
        self._adapters = {adapter.domain_id: adapter for adapter in adapters or []}

    @classmethod
    def with_weather(cls) -> "PresentationRegistry":
        return cls([WeatherPresentationAdapter()])

    def get(self, domain_id: str | None) -> PresentationDomainAdapter | None:
        return self._adapters.get(domain_id) if isinstance(domain_id, str) else None
