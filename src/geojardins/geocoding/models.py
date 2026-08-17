"""Modelos independentes do provider de geocodificação."""

from __future__ import annotations

from dataclasses import dataclass

from ..address_normalizer import NORMALIZER_VERSION, QueryType, normalize_text


@dataclass(frozen=True)
class AddressQuery:
    """Consulta de endereço já validada, preservando valores para exibição."""

    street: str
    house_number: str | None
    city: str = "São Paulo"
    state: str = "SP"
    country: str = "Brasil"
    query_type: QueryType = QueryType.ADDRESS
    street_1: str | None = None
    street_2: str | None = None

    @property
    def display_query(self) -> str:
        return f"{self.street}, {self.house_number}" if self.house_number else self.street

    @property
    def provider_query(self) -> str:
        return f"{self.display_query}, {self.city} - {self.state}, {self.country}"

    @property
    def is_intersection(self) -> bool:
        return self.query_type is QueryType.INTERSECTION

    @property
    def cache_key(self) -> str:
        streets = "|".join(filter(None, (self.street_1 or self.street, self.street_2)))
        return f"{NORMALIZER_VERSION}|{self.query_type.value}|{normalize_text(streets)}|{self.house_number or '-'}"


@dataclass(frozen=True)
class GeocodeCandidate:
    """Resposta limpa do geocoder; JSON do provider não vaza para a aplicação."""

    latitude: float
    longitude: float
    display_name: str
    house_number: str | None = None
    road: str | None = None
    neighbourhood: str | None = None
    suburb: str | None = None
    city: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None
    country_code: str | None = None
    state_code: str | None = None
    county_code: str | None = None
    provider: str = "unknown"
    original_rank: int = 0
    match_code: str | None = None
    match_type: str | None = None
    match_level: str | None = None
    importance: float | None = None
    raw_id: str | None = None
    query_mode: str = "unknown"
    provider_place_id: str | None = None
    result_type: str | None = None
    confidence: float | None = None
    confidence_street_level: float | None = None
    confidence_building_level: float | None = None


@dataclass(frozen=True)
class GeocodingDiagnostics:
    """Resumo da estratégia usada na última consulta, sem JSON do provider."""

    structured_used: bool = False
    structured_candidate_count: int = 0
    freeform_used: bool = False
    freeform_candidate_count: int = 0
    structured_requests: int = 0
    freeform_requests: int = 0
    geoapify_used: bool = False
    geoapify_candidate_count: int = 0
    geoapify_usable_candidate_count: int = 0
    geoapify_requests: int = 0
    locationiq_fallback_needed: bool = False
    locationiq_fallback_used: bool = False
    locationiq_fallback_reason: str | None = None
    locationiq_candidate_count: int = 0
    locationiq_requests: int = 0
    forced_fallback: bool = False

    @property
    def mode_label(self) -> str:
        if self.geoapify_used and self.locationiq_fallback_used:
            return "GEOAPIFY + LOCATIONIQ FALLBACK"
        if self.geoapify_used:
            return "GEOAPIFY"
        if self.structured_used and self.freeform_used:
            return "STRUCTURED + FREEFORM FALLBACK"
        if self.structured_used:
            return "STRUCTURED"
        if self.freeform_used:
            return "FREEFORM"
        return "NÃO EXECUTADO"
