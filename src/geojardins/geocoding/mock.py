"""Provider determinístico para testes offline, sem token ou internet."""

from __future__ import annotations

from collections.abc import Mapping

from .base import GeocodingError
from .models import AddressQuery, GeocodeCandidate
from ..address_normalizer import normalize_text


class MockGeocoder:
    def __init__(
        self,
        responses: Mapping[str, list[GeocodeCandidate] | GeocodingError] | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.call_count = 0
        self.queries: list[AddressQuery] = []

    def geocode(self, query: AddressQuery) -> list[GeocodeCandidate]:
        self.call_count += 1
        self.queries.append(query)
        response = self.responses.get(query.provider_query)
        if response is None:
            wanted = normalize_text(query.provider_query)
            response = next(
                (value for key, value in self.responses.items() if normalize_text(key) == wanted),
                [],
            )
        if isinstance(response, GeocodingError):
            raise response
        return list(response)
