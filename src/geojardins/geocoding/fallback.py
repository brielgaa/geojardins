"""Estratégia leve de Geoapify principal com LocationIQ como fallback."""

from __future__ import annotations

import logging
from dataclasses import replace
from collections.abc import Callable

from ..candidate_compatibility import evaluate_candidate_compatibility
from .base import Geocoder, GeocodingTemporaryError
from .models import AddressQuery, GeocodeCandidate, GeocodingDiagnostics
from ..address_normalizer import normalize_text

LOGGER = logging.getLogger(__name__)

GeographicValidator = Callable[[GeocodeCandidate], bool]


def is_candidate_usable_for_query(
    candidate: GeocodeCandidate,
    query: AddressQuery,
    *,
    geographic_validator: GeographicValidator | None = None,
) -> bool:
    """Verifica a evidência mínima para decidir se o fallback é necessário.

    Isto não decide status ou precisão. Um candidato com building confidence 0
    ainda pode ser utilizável, desde que seja o endereço correto em posição
    operacional plausível; a classificação final continuará devolvendo
    ``CONFERIR``.
    """

    geographically_plausible = True
    if geographic_validator is not None:
        try:
            geographically_plausible = geographic_validator(candidate)
        except (TypeError, ValueError):
            return False
    compatibility = evaluate_candidate_compatibility(
        candidate,
        query,
        geographically_plausible=geographically_plausible,
    )
    if query.is_intersection:
        return (
            compatibility.compatible
            and compatibility.intersection_complete
            and compatibility.geographically_plausible
        )
    if not compatibility.usable:
        return False
    # Um centroide de rua/resultado genérico não encerra a estratégia: é
    # exatamente o tipo de resposta que motivou a Fase 3A.
    if normalize_text(candidate.match_type) in {"centroid", "approximate"}:
        return False
    if normalize_text(candidate.match_level) in {"street", "neighbourhood", "city"}:
        return False
    if candidate.provider == "geoapify" and (candidate.confidence_building_level or 0.0) <= 0.0:
        return False
    return True


class FallbackGeocoder:
    """Consulta Geoapify primeiro e usa LocationIQ somente quando necessário.

    A decisão é provider-agnostic: identifica somente se a resposta primária
    contém logradouro, número, país e cidade coerentes. A decisão GIS e o status
    continuam fora desta classe.
    """

    def __init__(
        self,
        primary: Geocoder,
        fallback: Geocoder | None = None,
        *,
        geographic_validator: GeographicValidator | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.geographic_validator = geographic_validator
        self.request_count = 0
        self.geoapify_request_count = 0
        self.locationiq_request_count = 0
        self.locationiq_fallback_count = 0
        self.geoapify_candidates_used = 0
        self.geoapify_candidates_returned = 0
        self.geoapify_usable_candidate_count = 0
        self.structured_request_count = 0
        self.freeform_request_count = 0
        self.fallback_resolved_not_found = 0
        self.fallback_changed_winning_provider = 0
        self.fallback_elevated_confidence = 0
        self.fallback_detected_divergence = 0
        self.fallback_result_rejected = 0
        self.fallback_no_effect = 0
        self.last_diagnostics = GeocodingDiagnostics()

    def _usable_primary_candidates(
        self,
        candidates: list[GeocodeCandidate],
        query: AddressQuery,
    ) -> list[GeocodeCandidate]:
        return [
            candidate
            for candidate in candidates
            if is_candidate_usable_for_query(
                candidate,
                query,
                geographic_validator=self.geographic_validator,
            )
        ]

    @staticmethod
    def _tag(candidates: list[GeocodeCandidate], provider: str) -> list[GeocodeCandidate]:
        return [replace(candidate, query_mode=f"{provider}:{candidate.query_mode}") for candidate in candidates]

    @staticmethod
    def _delta(provider: object, name: str, before: int) -> int:
        return int(getattr(provider, name, 0)) - before

    def record_selected_candidate(self, candidate: GeocodeCandidate) -> None:
        if candidate.provider == "geoapify":
            self.geoapify_candidates_used += 1

    def record_fallback_outcome(
        self,
        *,
        selected_provider: str | None,
        confidence_elevated: bool,
        divergent: bool,
        secondary_rejected: bool,
    ) -> None:
        """Registra o ROI do fallback após a seleção territorial final."""

        diagnostics = self.last_diagnostics
        if not diagnostics.locationiq_fallback_used:
            return
        changed_provider = selected_provider == "locationiq"
        resolved_not_found = diagnostics.geoapify_candidate_count == 0 and changed_provider
        if resolved_not_found:
            self.fallback_resolved_not_found += 1
        if changed_provider:
            self.fallback_changed_winning_provider += 1
        if confidence_elevated:
            self.fallback_elevated_confidence += 1
        if divergent:
            self.fallback_detected_divergence += 1
        if secondary_rejected:
            self.fallback_result_rejected += 1
        if not any((resolved_not_found, changed_provider, confidence_elevated, divergent, secondary_rejected)):
            self.fallback_no_effect += 1

    def geocode(self, query: AddressQuery, *, force_fallback: bool = False) -> list[GeocodeCandidate]:
        primary_before = int(getattr(self.primary, "request_count", 0))
        fallback_before = int(getattr(self.fallback, "request_count", 0)) if self.fallback else 0
        structured_before = int(getattr(self.fallback, "structured_request_count", 0)) if self.fallback else 0
        freeform_before = int(getattr(self.fallback, "freeform_request_count", 0)) if self.fallback else 0
        primary_candidates: list[GeocodeCandidate] = []
        primary_failure: GeocodingTemporaryError | None = None
        try:
            primary_candidates = self.primary.geocode(query)
        except GeocodingTemporaryError as exc:
            primary_failure = exc
            LOGGER.warning("Geoapify indisponível após retries; avaliando fallback LocationIQ.")

        usable_primary = self._usable_primary_candidates(primary_candidates, query)
        if force_fallback:
            fallback_needed = True
            fallback_reason = "fallback forçado por coordenada genérica detectada no lote"
        elif primary_failure is not None:
            fallback_needed = True
            fallback_reason = "falha transitória definitiva da Geoapify"
        elif not primary_candidates:
            fallback_needed = True
            fallback_reason = "nenhum candidato Geoapify retornado"
        elif not usable_primary:
            fallback_needed = True
            fallback_reason = "nenhum candidato Geoapify utilizável"
        elif len(usable_primary) > 1:
            fallback_needed = True
            fallback_reason = "mais de um candidato Geoapify utilizável"
        else:
            fallback_needed = False
            fallback_reason = None
        fallback_used = bool(self.fallback) and fallback_needed
        fallback_candidates: list[GeocodeCandidate] = []
        if fallback_used and self.fallback:
            self.locationiq_fallback_count += 1
            try:
                fallback_candidates = self.fallback.geocode(query)
            except GeocodingTemporaryError:
                if primary_failure is not None:
                    raise primary_failure
                LOGGER.warning("LocationIQ indisponível após fallback; mantendo candidatos Geoapify disponíveis.")

        geoapify_requests = self._delta(self.primary, "request_count", primary_before)
        locationiq_requests = self._delta(self.fallback, "request_count", fallback_before) if self.fallback else 0
        self.geoapify_request_count += geoapify_requests
        self.geoapify_candidates_returned += len(primary_candidates)
        self.geoapify_usable_candidate_count += len(usable_primary)
        self.locationiq_request_count += locationiq_requests
        if self.fallback:
            self.structured_request_count += self._delta(
                self.fallback, "structured_request_count", structured_before
            )
            self.freeform_request_count += self._delta(
                self.fallback, "freeform_request_count", freeform_before
            )
        self.request_count += geoapify_requests + locationiq_requests
        self.last_diagnostics = GeocodingDiagnostics(
            geoapify_used=True,
            geoapify_candidate_count=len(primary_candidates),
            geoapify_usable_candidate_count=len(usable_primary),
            geoapify_requests=geoapify_requests,
            locationiq_fallback_needed=fallback_needed,
            locationiq_fallback_used=fallback_used,
            locationiq_fallback_reason=fallback_reason,
            locationiq_candidate_count=len(fallback_candidates),
            locationiq_requests=locationiq_requests,
            forced_fallback=force_fallback,
        )
        return self._tag(primary_candidates, "geoapify") + self._tag(fallback_candidates, "locationiq")
