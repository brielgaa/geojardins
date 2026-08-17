"""Motor individual: endereço → geocoder → GIS → status operacional."""

from __future__ import annotations

import argparse
import logging
import math
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .address_normalizer import (
    AddressValidationError,
    AliasRules,
    NormalizedAddress,
    QueryType,
    address_cache_key,
    load_alias_rules,
    normalize_address,
    normalize_house_number,
    normalize_optional_house_number,
    normalize_text,
    street_similarity,
    validate_street,
)
from .classification import (
    GeographicClassification,
    GeographicClassifier,
    geoapify_rect_from_boundary,
    locationiq_viewbox_from_boundary,
)
from .candidate_compatibility import CandidateCompatibility, evaluate_candidate_compatibility
from .confidence import ConfidenceAssessment, ConfidenceLevel, assess_composite_confidence
from .fiscais import FiscalResolver
from .geoapify_precision import BUILDING_HIGH_CONFIDENCE_THRESHOLD, decide_geoapify_precision
from .geocoding.base import Geocoder, GeocodingError, RateLimiter
from .geocoding.cache import CachedGeocoder, PersistentGeocodingCache
from .geocoding.fallback import FallbackGeocoder
from .geocoding.geoapify import GeoapifyGeocoder
from .geocoding.locationiq import LocationIQGeocoder
from .geocoding.models import AddressQuery, GeocodeCandidate, GeocodingDiagnostics
from .maps_links import google_maps_link, google_maps_search_link
from .runtime import geocoding_cache_path, resource_path
from .settings import Settings, load_settings
from .street_dictionary import StreetDictionary
from pyproj import Geod

LOGGER = logging.getLogger(__name__)
GEOD = Geod(ellps="GRS80")


class LookupStatus(str, Enum):
    CONFIRMADO = "CONFIRMADO"
    PROXIMO_DA_DIVISA = "PROXIMO_DA_DIVISA"
    CONFERIR = "CONFERIR"
    FORA_DA_UGR = "FORA_DA_UGR"
    ENDERECO_NAO_LOCALIZADO = "ENDERECO_NAO_LOCALIZADO"
    ERRO_GEOCODIFICACAO = "ERRO_GEOCODIFICACAO"
    DADOS_INVALIDOS = "DADOS_INVALIDOS"


class GeocodingPrecision(str, Enum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAIXA = "BAIXA"


class LookupFlag(str, Enum):
    BAIXA_PRECISAO = "BAIXA_PRECISAO"
    PROXIMO_DA_DIVISA = "PROXIMO_DA_DIVISA"
    NUMERO_INCOMPATIVEL = "NUMERO_INCOMPATIVEL"
    PROVIDERS_DIVERGENTES = "PROVIDERS_DIVERGENTES"
    COORDENADA_GENERICA = "COORDENADA_GENERICA"
    ENDERECO_NORMALIZADO = "ENDERECO_NORMALIZADO"  # legado; não é mais emitida para formatação trivial
    ALIAS_APLICADO = "ALIAS_APLICADO"
    CORRECAO_SUGERIDA = "CORRECAO_SUGERIDA"
    CORRECAO_APLICADA = "CORRECAO_APLICADA"
    LOGRADOURO_INCOMPATIVEL = "LOGRADOURO_INCOMPATIVEL"
    PRIMARY_STREET_INCOMPATIBLE = "PRIMARY_STREET_INCOMPATIBLE"
    SECONDARY_STREET_INCOMPATIBLE = "SECONDARY_STREET_INCOMPATIBLE"
    SECONDARY_RESULT_REJECTED = "SECONDARY_RESULT_REJECTED"
    HOMONIMO_FORA_UGR = "HOMONIMO_FORA_UGR"
    CONFIANCA_COMPOSTA_ELEVADA = "CONFIANCA_COMPOSTA_ELEVADA"
    CRUZAMENTO = "CRUZAMENTO"
    CRUZAMENTO_PARCIAL = "CRUZAMENTO_PARCIAL"
    SOBREPOSICAO_AREAS = "SOBREPOSICAO_AREAS"
    FORA_DA_UGR = "FORA_DA_UGR"
    FALLBACK_UTILIZADO = "FALLBACK_UTILIZADO"


@dataclass(frozen=True)
class CandidateAssessment:
    candidate: GeocodeCandidate
    plausible: bool
    quality_score: float
    road_similarity: float
    number_matches: bool
    number_available: bool
    precision: GeocodingPrecision
    geography: GeographicClassification | None
    reasons: tuple[str, ...]
    compatibility: CandidateCompatibility
    precision_evidence: str | None = None


@dataclass(frozen=True)
class AddressLookupResult:
    original_street: str
    original_house_number: Any
    normalized_house_number: str | None
    status: LookupStatus
    reason: str
    normalized_street: str | None = None
    query_type: QueryType = QueryType.ADDRESS
    suggested_correction: str | None = None
    suggestion_type: str | None = None
    suggestion_confidence: str | None = None
    candidate: GeocodeCandidate | None = None
    geography: GeographicClassification | None = None
    fiscal: str = "NÃO CONFIGURADO"
    precision: GeocodingPrecision | None = None
    assessments: tuple[CandidateAssessment, ...] = ()
    geocoding_diagnostics: GeocodingDiagnostics | None = None
    selection_reason: str | None = None
    flags: tuple[LookupFlag, ...] = ()
    provider_principal: str | None = None
    provider_distance_m: float | None = None
    providers_divergent: bool = False
    number_returned: str | None = None
    number_compatible: bool | None = None
    street_1_match: bool | None = None
    street_2_match: bool | None = None
    confidence: ConfidenceAssessment | None = None
    secondary_rejected: bool = False
    confidence_promoted: bool = False

    @property
    def latitude(self) -> float | None:
        return self.candidate.latitude if self.candidate else None

    @property
    def longitude(self) -> float | None:
        return self.candidate.longitude if self.candidate else None

    @property
    def formatted_address(self) -> str | None:
        return self.candidate.display_name if self.candidate else None

    @property
    def area(self) -> str | None:
        return self.geography.area if self.geography else None

    @property
    def candidate_area(self) -> str | None:
        return self.geography.area if self.geography else None

    @property
    def confirmed_area(self) -> str | None:
        return self.area if self.status in {LookupStatus.CONFIRMADO, LookupStatus.PROXIMO_DA_DIVISA} else None

    @property
    def confirmed_fiscal(self) -> str | None:
        return self.fiscal if self.confirmed_area else None

    @property
    def distance_to_boundary_m(self) -> float | None:
        return self.geography.distance_to_boundary_m if self.geography else None

    @property
    def google_maps_url(self) -> str | None:
        if self.candidate:
            return google_maps_link(self.latitude, self.longitude)
        street = self.normalized_street or self.original_street
        number = "" if self.query_type is QueryType.INTERSECTION else (self.normalized_house_number or str(self.original_house_number or "").strip())
        text = ", ".join(part for part in (street, number, "São Paulo - SP") if part)
        return google_maps_search_link(text) if text else None

    @property
    def areas(self) -> tuple[str, ...]:
        return self.geography.areas if self.geography else ()

    @property
    def fallback_used(self) -> bool:
        return bool(self.geocoding_diagnostics and self.geocoding_diagnostics.locationiq_fallback_used)

    def with_flags(
        self,
        *flags: LookupFlag,
        force_conferir: bool = False,
        reason: str | None = None,
    ) -> "AddressLookupResult":
        merged_flags = set(self.flags) | set(flags)
        confidence = self.confidence
        confidence_promoted = self.confidence_promoted
        if LookupFlag.COORDENADA_GENERICA in flags and confidence is not None:
            # Coordenada repetida por alturas distintas invalida qualquer
            # promoção automática já registrada antes da análise em lote.
            merged_flags.discard(LookupFlag.CONFIANCA_COMPOSTA_ELEVADA)
            confidence_promoted = False
            confidence = ConfidenceAssessment(
                ConfidenceLevel.LOW,
                confidence.score - 7,
                confidence.positive_evidence,
                confidence.negative_evidence + ("coordenada genérica detectada no lote",),
                "coordenada genérica requer evidência independente ou conferência",
                False,
                tuple(sorted(set(confidence.promotion_blockers) | {"confidence_blocked_generic_coordinate"})),
            )
        return replace(
            self,
            flags=tuple(sorted(merged_flags, key=lambda flag: flag.value)),
            status=LookupStatus.CONFERIR if force_conferir and self.status is not LookupStatus.FORA_DA_UGR else self.status,
            reason=reason or self.reason,
            confidence=confidence,
            confidence_promoted=confidence_promoted,
        )

    def export_values(self) -> dict[str, Any]:
        candidate = self.candidate
        return {
            "Logradouro Original": self.original_street,
            "Logradouro Normalizado": self.normalized_street,
            "Tipo Consulta": self.query_type.value,
            "Latitude": self.latitude,
            "Longitude": self.longitude,
            "Endereço Geocodificado": self.formatted_address,
            "Área": self.confirmed_area,
            "Área Candidata": self.candidate_area,
            "Áreas Candidatas": "; ".join(self.areas),
            "Fiscal": self.confirmed_fiscal,
            "Fiscal Candidato": self.fiscal if self.candidate_area else None,
            "Distância Divisa (m)": self.distance_to_boundary_m,
            "Match Code": candidate.match_code if candidate else None,
            "Match Type": candidate.match_type if candidate else None,
            "Match Level": candidate.match_level if candidate else None,
            "Precisão Geocodificação": self.precision.value if self.precision else None,
            "Status": self.status.value,
            "Flags": "; ".join(flag.value for flag in self.flags),
            "Motivo Status": self.reason,
            "Link Google Maps": self.google_maps_url,
            "Place ID": candidate.raw_id if candidate else None,
            "Provider Principal": self.provider_principal,
            "Provider Utilizado": candidate.provider if candidate else None,
            "Fallback Utilizado": "SIM" if self.fallback_used else "NÃO",
            "Distância Providers (m)": self.provider_distance_m,
            "Número Solicitado": self.normalized_house_number,
            "Número Retornado": self.number_returned,
            "Número Compatível": (
                "SIM" if self.number_compatible else "NÃO" if self.number_compatible is not None else None
            ),
            "Sugestão de Correção": self.suggested_correction,
            "Tipo da Sugestão": self.suggestion_type,
            "Confiança da Sugestão": self.suggestion_confidence,
            "Confiança Final": self.confidence.level.value if self.confidence else None,
            "Motivo Decisão": self.confidence.decision_reason if self.confidence else self.selection_reason,
            "Cruzamento Via 1 Compatível": self.street_1_match,
            "Cruzamento Via 2 Compatível": self.street_2_match,
        }


def classify_precision(
    candidate: GeocodeCandidate,
    number_matches: bool,
    *,
    road_similarity: float = 0.0,
    country_compatible: bool = False,
    city_compatible: bool = False,
    inside_ugr: bool = False,
) -> GeocodingPrecision:
    if candidate.provider == "geoapify":
        return classify_geoapify_precision(
            candidate,
            number_matches,
            road_similarity=road_similarity,
            country_compatible=country_compatible,
            city_compatible=city_compatible,
            inside_ugr=inside_ugr,
        )
    code = normalize_text(candidate.match_code)
    kind = normalize_text(candidate.match_type)
    level = normalize_text(candidate.match_level)
    high_level = level in {"building", "venue"}
    precise_type = kind in {"point", "interpolated"}
    if code == "exact" and high_level and precise_type and number_matches:
        return GeocodingPrecision.ALTA
    if (
        code in {"exact", "fallback"}
        and (high_level or level == "street")
        and (precise_type or kind == "centroid")
    ):
        return GeocodingPrecision.MEDIA
    return GeocodingPrecision.BAIXA


def _legacy_classify_geoapify_precision(
    candidate: GeocodeCandidate,
    number_matches: bool,
) -> GeocodingPrecision:
    """Mapeia os campos de confiança Geoapify de forma conservadora.

    ``0`` em building confidence significa ausência de evidência para a posição
    do imóvel. Para ALTA exigimos número, building, full_match e confiança geral,
    de rua e de building de pelo menos 0,5 (metade da escala normalizada 0..1),
    além das validações de via/localidade feitas por ``assess_candidate``.
    """

    result_type = normalize_text(candidate.result_type)
    match_type = normalize_text(candidate.match_type)
    overall = candidate.confidence or 0.0
    street = candidate.confidence_street_level or 0.0
    building = candidate.confidence_building_level or 0.0
    if (
        number_matches
        and result_type == "building"
        and match_type == "full_match"
        and overall >= 0.5
        and street >= 0.5
        and building >= 0.5
    ):
        return GeocodingPrecision.ALTA
    if number_matches and result_type == "building" and building > 0:
        return GeocodingPrecision.MEDIA
    return GeocodingPrecision.BAIXA


def classify_geoapify_precision(
    candidate: GeocodeCandidate,
    number_matches: bool,
    *,
    road_similarity: float = 0.0,
    country_compatible: bool = False,
    city_compatible: bool = False,
    inside_ugr: bool = False,
) -> GeocodingPrecision:
    """Mantem a API publica e delega a decisao isolada de precisao."""

    decision = decide_geoapify_precision(
        candidate,
        number_matches,
        road_similarity=road_similarity,
        country_compatible=country_compatible,
        city_compatible=city_compatible,
        inside_ugr=inside_ugr,
    )
    return GeocodingPrecision(decision.level)


def assess_candidate(
    query: AddressQuery,
    candidate: GeocodeCandidate,
    classifier: GeographicClassifier,
) -> CandidateAssessment:
    preliminary = evaluate_candidate_compatibility(candidate, query)
    geography = (
        classifier.classify(candidate.latitude, candidate.longitude)
        if preliminary.compatible
        else None
    )
    compatibility = evaluate_candidate_compatibility(
        candidate,
        query,
        geographically_plausible=geography.inside_ugr if geography is not None else True,
    )
    similarity = compatibility.road_similarity
    number_available = compatibility.number_available
    number_matches = compatibility.number_matches
    reasons: list[str] = []
    if not compatibility.country_compatible:
        reasons.append("País do candidato não é Brasil.")
    if not compatibility.city_compatible:
        reasons.append("Cidade/UF do candidato não é São Paulo/SP.")
    if not compatibility.road_compatible:
        reasons.append("Via retornada não é suficientemente compatível com o logradouro consultado.")
    if number_available and not number_matches:
        reasons.append("Número retornado não corresponde à altura solicitada.")
    if not number_available:
        reasons.append("Provider não retornou número do imóvel.")

    code = normalize_text(candidate.match_code)
    kind = normalize_text(candidate.match_type)
    level = normalize_text(candidate.match_level)
    score = 0.0
    score += 10.0 * similarity
    score += 6.0 if compatibility.country_compatible else -12.0
    score += 6.0 if compatibility.city_compatible else -12.0
    score += {"exact": 8.0, "fallback": 4.0, "approximate": 0.0}.get(code, 0.0)
    score += {"point": 4.0, "interpolated": 3.0, "centroid": 0.0}.get(kind, 0.0)
    score += {"venue": 4.0, "building": 4.0, "street": 1.0}.get(level, 0.0)
    score += 8.0 if number_matches else (-4.0 if number_available else 0.0)
    if candidate.provider == "geoapify":
        score += {"building": 5.0, "amenity": 3.0, "street": 1.0}.get(
            normalize_text(candidate.result_type), 0.0
        )
        score += {"full_match": 4.0, "match_by_building": 2.0}.get(
            normalize_text(candidate.match_type), 0.0
        )
        score += 4.0 * min(1.0, max(0.0, candidate.confidence or 0.0))
        score += 2.0 * min(1.0, max(0.0, candidate.confidence_street_level or 0.0))
        score += 6.0 * min(1.0, max(0.0, candidate.confidence_building_level or 0.0))
    if candidate.importance is not None:
        score += min(1.0, max(0.0, candidate.importance))
    plausible = compatibility.compatible
    precision = classify_precision(
        candidate,
        number_matches,
        road_similarity=similarity,
        country_compatible=compatibility.country_compatible,
        city_compatible=compatibility.city_compatible,
        inside_ugr=bool(geography and geography.inside_ugr),
    )
    precision_evidence = None
    if candidate.provider == "geoapify":
        precision_evidence = decide_geoapify_precision(
            candidate,
            number_matches,
            road_similarity=similarity,
            country_compatible=compatibility.country_compatible,
            city_compatible=compatibility.city_compatible,
            inside_ugr=bool(geography and geography.inside_ugr),
        ).evidence
    return CandidateAssessment(
        candidate=candidate,
        plausible=plausible,
        quality_score=score,
        road_similarity=similarity,
        number_matches=number_matches,
        number_available=number_available,
        precision=precision,
        geography=geography,
        reasons=tuple(reasons),
        compatibility=compatibility,
        precision_evidence=precision_evidence,
    )


class AddressLookupService:
    """Orquestra geocoder, seleção plausível, GIS, status e fiscal."""

    def __init__(
        self,
        geocoder: Geocoder,
        classifier: GeographicClassifier,
        fiscal_resolver: FiscalResolver,
        settings: Settings,
        alias_rules: AliasRules | None = None,
        cache: PersistentGeocodingCache | None = None,
        street_dictionary: StreetDictionary | None = None,
    ) -> None:
        self.geocoder = geocoder
        self.classifier = classifier
        self.fiscal_resolver = fiscal_resolver
        self.settings = settings
        self.alias_rules = alias_rules if alias_rules is not None else load_alias_rules()
        self.cache = cache
        self.street_dictionary = street_dictionary if street_dictionary is not None else StreetDictionary.from_path()

    def _invalid(self, street: Any, number: Any, reason: str) -> AddressLookupResult:
        return AddressLookupResult(
            original_street=str(street or ""),
            original_house_number=number,
            normalized_house_number=None,
            status=LookupStatus.DADOS_INVALIDOS,
            reason=reason,
        )

    def prepare_query(self, street: Any, house_number: Any) -> tuple[NormalizedAddress, AddressQuery]:
        normalized = normalize_address(street, rules=self.alias_rules)
        normalized = self._add_local_suggestion(normalized)
        if normalized.query_type is QueryType.INTERSECTION:
            # Altura em uma linha de cruzamento é meramente informativa; não a
            # usamos como requisito de correspondência nem como parte da chave.
            number = None
        else:
            number = normalize_house_number(house_number)
        return normalized, AddressQuery(
            street=normalized.normalized,
            house_number=number,
            query_type=normalized.query_type,
            street_1=normalized.street_1,
            street_2=normalized.street_2,
        )

    def _add_local_suggestion(self, normalized: NormalizedAddress) -> NormalizedAddress:
        """Completa sugestões configuradas com fuzzy exclusivamente local."""

        if normalized.suggested_correction:
            return normalized
        suggestions = [
            self.street_dictionary.suggest(
                part,
                suggest_threshold=self.settings.street_fuzzy_suggest_threshold,
                auto_threshold=self.settings.street_fuzzy_auto_threshold,
                min_margin=self.settings.street_fuzzy_min_margin,
            )
            for part in (normalized.street_1, normalized.street_2)
            if part
        ]
        suggestion = next((item for item in suggestions if item.decision != "NONE" and item.canonical), None)
        if suggestion is None:
            return normalized
        return replace(
            normalized,
            suggested_correction=suggestion.canonical,
            suggestion_type="FUZZY_LOCAL",
            suggestion_confidence=suggestion.confidence,
            suggestion_decision=suggestion.decision,
        )

    def _corrected_query_for_retry(
        self,
        normalized: NormalizedAddress,
        query: AddressQuery,
    ) -> AddressQuery | None:
        """Monta uma segunda consulta apenas para correção fuzzy inequívoca."""

        if (
            normalized.query_type is not QueryType.ADDRESS
            or normalized.suggestion_type != "FUZZY_LOCAL"
            or normalized.suggestion_decision != "AUTO_SAFE"
            or not normalized.suggested_correction
        ):
            return None
        corrected = normalize_address(normalized.suggested_correction, rules=self.alias_rules)
        if corrected.query_type is not QueryType.ADDRESS:
            return None
        return AddressQuery(corrected.normalized, query.house_number, street_1=corrected.street_1)

    def _geocode(self, query: AddressQuery, *, force_fallback: bool) -> list[GeocodeCandidate]:
        if force_fallback and isinstance(self.geocoder, FallbackGeocoder):
            return self.geocoder.geocode(query, force_fallback=True)
        return self.geocoder.geocode(query)

    def query_key(self, street: Any, house_number: Any) -> str:
        """Chave de deduplicação compartilhada com o lote, antes de chamar API."""

        _, query = self.prepare_query(street, house_number)
        return query.cache_key

    @staticmethod
    def _best_by_provider(assessments: list[CandidateAssessment]) -> dict[str, CandidateAssessment]:
        best: dict[str, CandidateAssessment] = {}
        for assessment in assessments:
            if not assessment.plausible:
                continue
            existing = best.get(assessment.candidate.provider)
            if existing is None or assessment.quality_score > existing.quality_score:
                best[assessment.candidate.provider] = assessment
        return best

    @classmethod
    def _provider_distance(cls, assessments: list[CandidateAssessment]) -> float | None:
        best = cls._best_by_provider(assessments)
        if len(best) < 2:
            return None
        first, second = list(best.values())[:2]
        _, _, distance = GEOD.inv(
            first.candidate.longitude, first.candidate.latitude,
            second.candidate.longitude, second.candidate.latitude,
        )
        return abs(float(distance))

    @staticmethod
    def _strong_provider_result(assessment: CandidateAssessment) -> bool:
        geography = assessment.geography
        return bool(
            assessment.compatibility.road_compatible
            and assessment.number_matches
            and geography
            and geography.inside_ugr
            and geography.area
            and not geography.geometric_ambiguity
        )

    def _provider_evidence(
        self,
        selected: CandidateAssessment,
        assessments: list[CandidateAssessment],
    ) -> tuple[float | None, str | None, bool, bool]:
        """Classifica a relação entre providers após validar cada um isoladamente."""

        best = self._best_by_provider(assessments)
        other = [assessment for provider, assessment in best.items() if provider != selected.candidate.provider]
        if not other:
            # Um resultado secundário incompatível permanece auditável, mas não
            # derruba uma resposta primária forte apenas pela distância bruta.
            rejected = bool(
                selected.candidate.provider == "geoapify"
                and any(item.candidate.provider == "locationiq" for item in assessments)
            )
            return None, None, False, rejected
        secondary = max(other, key=lambda assessment: assessment.quality_score)
        _, _, distance = GEOD.inv(
            selected.candidate.longitude, selected.candidate.latitude,
            secondary.candidate.longitude, secondary.candidate.latitude,
        )
        distance_m = abs(float(distance))
        both_strong = self._strong_provider_result(selected) and self._strong_provider_result(secondary)
        if not both_strong:
            return distance_m, None, False, selected.candidate.provider == "geoapify"
        if distance_m <= self.settings.provider_agreement_strong_m:
            return distance_m, "STRONG", False, False
        if distance_m <= self.settings.provider_agreement_acceptable_m:
            return distance_m, "ACCEPTABLE", False, False
        divergent = distance_m > self.settings.provider_divergence_m
        return distance_m, None, divergent, False

    def _has_conflicting_strong_candidate(
        self,
        selected: CandidateAssessment,
        assessments: list[CandidateAssessment],
    ) -> bool:
        """Detecta outro resultado forte e distante sem criar novo limiar."""

        for assessment in assessments:
            if assessment is selected or not self._strong_provider_result(assessment):
                continue
            _, _, distance = GEOD.inv(
                selected.candidate.longitude, selected.candidate.latitude,
                assessment.candidate.longitude, assessment.candidate.latitude,
            )
            if abs(float(distance)) > self.settings.provider_divergence_m:
                return True
        return False

    @staticmethod
    def _initial_flags(normalized: NormalizedAddress) -> set[LookupFlag]:
        flags: set[LookupFlag] = set()
        operational_aliases = tuple(
            alias for alias in normalized.applied_aliases
            if alias not in {"token:R", "token:AV", "token:TV"}
        )
        if operational_aliases:
            flags.add(LookupFlag.ALIAS_APLICADO)
        if normalized.query_type is QueryType.INTERSECTION:
            flags.add(LookupFlag.CRUZAMENTO)
        if normalized.suggested_correction:
            flags.add(LookupFlag.CORRECAO_SUGERIDA)
        return flags

    @staticmethod
    def _select(assessments: list[CandidateAssessment]) -> CandidateAssessment | None:
        plausible = [assessment for assessment in assessments if assessment.plausible]
        if not plausible:
            return None
        precision_rank = {
            GeocodingPrecision.ALTA: 3,
            GeocodingPrecision.MEDIA: 2,
            GeocodingPrecision.BAIXA: 1,
        }
        # A prioridade territorial é aplicada somente após a filtragem de
        # plausibilidade (via, cidade e país). Assim, estar dentro da UGR não
        # torna aceitável um candidato incompatível.
        return max(
            plausible,
            key=lambda assessment: (
                int(bool(assessment.geography and assessment.geography.inside_ugr)),
                int(assessment.number_matches),
                precision_rank[assessment.precision],
                assessment.road_similarity,
                assessment.quality_score,
                -assessment.candidate.original_rank,
            ),
        )

    @staticmethod
    def _selection_reason(selected: CandidateAssessment) -> str:
        geography = selected.geography
        factors = [
            "candidato plausível",
            "dentro da UGR" if geography and geography.inside_ugr else "fora da UGR",
            "número compatível" if selected.number_matches else "número ausente/incompatível",
            f"precisão {selected.precision.value}",
            f"via compatível ({selected.road_similarity:.2f})",
            f"modo {selected.candidate.query_mode}",
            f"rank {selected.candidate.original_rank}",
        ]
        return "; ".join(factors)

    def lookup(self, street: Any, house_number: Any, *, force_fallback: bool = False) -> AddressLookupResult:
        try:
            normalized, query = self.prepare_query(street, house_number)
        except AddressValidationError as exc:
            return self._invalid(street, house_number, str(exc))
        flags = self._initial_flags(normalized)
        try:
            candidates = self._geocode(query, force_fallback=force_fallback)
        except GeocodingError as exc:
            LOGGER.warning("Geocodificação falhou para a consulta normalizada: %s", exc)
            return AddressLookupResult(
                original_street=normalized.original,
                original_house_number=house_number,
                normalized_house_number=query.house_number,
                normalized_street=normalized.normalized,
                query_type=normalized.query_type,
                suggested_correction=normalized.suggested_correction,
                suggestion_type=normalized.suggestion_type,
                suggestion_confidence=normalized.suggestion_confidence,
                status=LookupStatus.ERRO_GEOCODIFICACAO,
                reason=str(exc),
                flags=tuple(sorted(flags, key=lambda flag: flag.value)),
            )
        except Exception:  # defesa para que uma falha isolada nunca interrompa um lote
            LOGGER.error("Falha inesperada no provider de geocodificação.")
            return AddressLookupResult(
                original_street=normalized.original,
                original_house_number=house_number,
                normalized_house_number=query.house_number,
                normalized_street=normalized.normalized,
                query_type=normalized.query_type,
                suggested_correction=normalized.suggested_correction,
                suggestion_type=normalized.suggestion_type,
                suggestion_confidence=normalized.suggestion_confidence,
                status=LookupStatus.ERRO_GEOCODIFICACAO,
                reason="Falha inesperada no provider de geocodificação.",
                flags=tuple(sorted(flags, key=lambda flag: flag.value)),
            )
        diagnostics = getattr(self.geocoder, "last_diagnostics", None)
        if not isinstance(diagnostics, GeocodingDiagnostics):
            diagnostics = None
        if diagnostics and diagnostics.locationiq_fallback_used:
            flags.add(LookupFlag.FALLBACK_UTILIZADO)
        assessments = [assess_candidate(query, candidate, self.classifier) for candidate in candidates]
        selected = self._select(assessments)
        corrected_query = self._corrected_query_for_retry(normalized, query)
        if selected is None and corrected_query is not None:
            # A primeira consulta é sempre preservada. Só um candidato local
            # AUTO_SAFE, único e com margem suficiente recebe uma tentativa
            # adicional; sugestões SUGGEST continuam apenas auditáveis.
            try:
                retry_candidates = self._geocode(corrected_query, force_fallback=force_fallback)
            except GeocodingError:
                retry_candidates = []
            except Exception:
                LOGGER.warning("Falha inesperada no retry de correção fuzzy; mantendo a consulta original.")
                retry_candidates = []
            if retry_candidates:
                retry_assessments = [
                    assess_candidate(corrected_query, candidate, self.classifier)
                    for candidate in retry_candidates
                ]
                retry_selected = self._select(retry_assessments)
                if retry_selected is not None:
                    query = corrected_query
                    candidates = retry_candidates
                    assessments = retry_assessments
                    selected = retry_selected
                    diagnostics = getattr(self.geocoder, "last_diagnostics", None)
                    if not isinstance(diagnostics, GeocodingDiagnostics):
                        diagnostics = None
                    if diagnostics and diagnostics.locationiq_fallback_used:
                        flags.add(LookupFlag.FALLBACK_UTILIZADO)
                    flags.add(LookupFlag.CORRECAO_APLICADA)
        principal = "geoapify" if diagnostics and diagnostics.geoapify_used else None
        if selected is None:
            incompatible = [assessment for assessment in assessments if not assessment.compatibility.road_compatible]
            if incompatible:
                flags.add(LookupFlag.LOGRADOURO_INCOMPATIVEL)
                if any(item.candidate.provider == "geoapify" for item in incompatible):
                    flags.add(LookupFlag.PRIMARY_STREET_INCOMPATIBLE)
                if any(item.candidate.provider == "locationiq" for item in incompatible):
                    flags.add(LookupFlag.SECONDARY_STREET_INCOMPATIBLE)
            reason = "Os providers não retornaram candidatos." if not candidates else "Nenhum candidato foi compatível com logradouro, Brasil e São Paulo/SP."
            record_fallback_outcome = getattr(self.geocoder, "record_fallback_outcome", None)
            if callable(record_fallback_outcome):
                record_fallback_outcome(
                    selected_provider=None,
                    confidence_elevated=False,
                    divergent=False,
                    secondary_rejected=False,
                )
            return AddressLookupResult(
                original_street=normalized.original,
                original_house_number=house_number,
                normalized_house_number=query.house_number,
                normalized_street=normalized.normalized,
                query_type=normalized.query_type,
                suggested_correction=normalized.suggested_correction,
                suggestion_type=normalized.suggestion_type,
                suggestion_confidence=normalized.suggestion_confidence,
                status=LookupStatus.ENDERECO_NAO_LOCALIZADO,
                reason=reason,
                assessments=tuple(assessments),
                geocoding_diagnostics=diagnostics,
                flags=tuple(sorted(flags, key=lambda flag: flag.value)),
                provider_principal=principal,
            )

        geography = selected.geography
        assert geography is not None
        provider_distance, agreement, divergent, secondary_rejected = self._provider_evidence(selected, assessments)
        primary_street_incompatible = bool(
            selected.candidate.provider != "geoapify"
            and any(
                assessment.candidate.provider == "geoapify"
                and not assessment.compatibility.road_compatible
                for assessment in assessments
            )
        )
        secondary_street_incompatible = bool(
            selected.candidate.provider == "geoapify"
            and any(
                assessment.candidate.provider == "locationiq"
                and not assessment.compatibility.road_compatible
                for assessment in assessments
            )
        )
        conflicting_strong_candidate = self._has_conflicting_strong_candidate(selected, assessments)
        homonym_outside = bool(
            geography.inside_ugr
            and selected.candidate.provider != "geoapify"
            and any(
                assessment.candidate.provider == "geoapify"
                and assessment.compatibility.road_compatible
                and assessment.number_matches
                and assessment.geography is not None
                and not assessment.geography.inside_ugr
                for assessment in assessments
            )
        )
        if divergent:
            flags.add(LookupFlag.PROVIDERS_DIVERGENTES)
        if secondary_rejected:
            flags.add(LookupFlag.SECONDARY_RESULT_REJECTED)
        if primary_street_incompatible:
            flags.add(LookupFlag.PRIMARY_STREET_INCOMPATIBLE)
        if secondary_street_incompatible:
            flags.add(LookupFlag.SECONDARY_STREET_INCOMPATIBLE)
        if homonym_outside:
            flags.add(LookupFlag.HOMONIMO_FORA_UGR)
        if not geography.inside_ugr:
            flags.add(LookupFlag.FORA_DA_UGR)
        if len(geography.areas) >= 2:
            flags.add(LookupFlag.SOBREPOSICAO_AREAS)
        if (
            geography.distance_to_boundary_m is not None
            and geography.distance_to_boundary_m <= self.settings.boundary_warning_m
        ):
            flags.add(LookupFlag.PROXIMO_DA_DIVISA)
        if normalized.query_type is QueryType.ADDRESS and not selected.number_matches:
            flags.add(LookupFlag.NUMERO_INCOMPATIVEL)
        if normalized.query_type is QueryType.INTERSECTION and selected.compatibility.intersection_partial:
            flags.add(LookupFlag.CRUZAMENTO_PARCIAL)
        if selected.precision is not GeocodingPrecision.ALTA:
            flags.add(LookupFlag.BAIXA_PRECISAO)

        fiscal = self.fiscal_resolver.for_area(geography.area)
        distance_to_boundary = geography.distance_to_boundary_m
        near_boundary = distance_to_boundary is not None and distance_to_boundary <= self.settings.boundary_warning_m
        safe_from_boundary = distance_to_boundary is not None and distance_to_boundary >= self.settings.safe_distance_from_divider_m
        result_type = normalize_text(selected.candidate.result_type)
        match_type = normalize_text(selected.candidate.match_type)
        match_level = normalize_text(selected.candidate.match_level)
        returned_number = selected.compatibility.returned_number
        normalized_road = normalize_text(selected.candidate.road)
        formatted_tail = (
            normalize_text(selected.candidate.display_name)[len(normalized_road):]
            .lstrip(" ,")
            if normalized_road and normalize_text(selected.candidate.display_name).startswith(normalized_road)
            else ""
        )
        formatted_road_and_number = bool(
            returned_number
            and formatted_tail.startswith(returned_number)
            and (
                len(formatted_tail) == len(returned_number)
                or not formatted_tail[len(returned_number)].isdigit()
            )
        )
        # Um ``building/full_match`` com altura exata e endereço formatado que
        # repete "via + número" é evidência estruturada mesmo se o campo de
        # building confidence vier zerado. O caminho continua exigindo as
        # confianças geral/de rua já usadas pelo provider; centroides de rua não
        # possuem esse conjunto completo e permanecem bloqueados.
        geoapify_structured_address = (
            selected.candidate.provider == "geoapify"
            and selected.number_matches
            and formatted_road_and_number
            and result_type in {"building", "amenity"}
        )
        provider_building_evidence = (
            geoapify_structured_address
            and (
                (selected.candidate.confidence_building_level or 0.0) > 0.0
                or (
                    result_type == "building"
                    and match_type in {"full_match", "match_by_building"}
                    and (selected.candidate.confidence or 0.0) >= BUILDING_HIGH_CONFIDENCE_THRESHOLD
                    and (selected.candidate.confidence_street_level or 0.0) >= BUILDING_HIGH_CONFIDENCE_THRESHOLD
                )
            )
        ) or (
            selected.candidate.provider != "geoapify"
            and match_level in {"building", "venue"}
        )
        dictionary_match = self.street_dictionary.contains(query.street_1 or query.street)
        if query.street_2:
            dictionary_match = dictionary_match and self.street_dictionary.contains(query.street_2)
        confidence = assess_composite_confidence(
            street_compatible=selected.compatibility.road_compatible,
            number_compatible=selected.number_matches,
            provider_building_evidence=provider_building_evidence,
            secondary_agreement=agreement,
            secondary_rejected=secondary_rejected,
            generic_coordinate=False,
            provider_divergent=divergent,
            near_boundary=near_boundary,
            safe_from_boundary=safe_from_boundary,
            inside_exclusive_area=bool(geography.inside_ugr and geography.area and not geography.geometric_ambiguity and len(geography.areas) == 1),
            street_dictionary_match=dictionary_match,
            conflicting_strong_candidate=conflicting_strong_candidate,
            boundary_distance_m=distance_to_boundary,
            homonym_risk=homonym_outside,
            area_overlap=len(geography.areas) >= 2,
        )
        if not geography.inside_ugr:
            status, reason = LookupStatus.FORA_DA_UGR, "Endereço compatível localizado, mas sua coordenada está fora da UGR Jardins."
        elif len(geography.areas) >= 2:
            status, reason = LookupStatus.CONFERIR, "Coordenada em sobreposição de áreas; nenhuma Tomé foi escolhida silenciosamente."
        elif geography.geometric_ambiguity or geography.area is None:
            status, reason = LookupStatus.CONFERIR, "Coordenada interna em divisa geométrica, gap ou área não exclusiva."
        elif divergent:
            status, reason = LookupStatus.CONFERIR, f"Providers divergem {provider_distance:.1f} m (limiar configurado: {self.settings.provider_divergence_m:.1f} m)."
        elif homonym_outside:
            status, reason = LookupStatus.CONFERIR, "Provider principal encontrou homônimo compatível fora da UGR; conferir antes de confirmar o candidato alternativo."
        elif normalized.query_type is QueryType.INTERSECTION:
            if selected.compatibility.intersection_complete:
                status, reason = LookupStatus.CONFERIR, "Cruzamento com as duas vias reconhecidas; conferir visualmente antes de confirmar uma área operacional."
            else:
                status, reason = LookupStatus.CONFERIR, "Cruzamento parcial: o provider não comprovou as duas vias."
        elif not selected.number_matches:
            status, reason = LookupStatus.CONFERIR, "Número do imóvel ausente ou incompatível; classificação territorial requer conferência."
        elif selected.precision is GeocodingPrecision.BAIXA and confidence.promotion_eligible:
            flags.add(LookupFlag.CONFIANCA_COMPOSTA_ELEVADA)
            reason = "Promovido por confiança composta: " + confidence.decision_reason.removeprefix("Confiança alta: ")
            confidence = replace(confidence, decision_reason=reason)
            status = LookupStatus.CONFIRMADO
        elif selected.precision is not GeocodingPrecision.ALTA:
            status, reason = LookupStatus.CONFERIR, "Precisão de geocodificação insuficiente para confirmar o endereço com segurança."
        elif (geography.distance_to_boundary_m or 0.0) <= self.settings.boundary_warning_m:
            status = LookupStatus.PROXIMO_DA_DIVISA
            reason = f"Endereço confirmado, porém a {geography.distance_to_boundary_m:.1f} m da divisa (margem configurada: {self.settings.boundary_warning_m:.1f} m)."
        else:
            status, reason = LookupStatus.CONFIRMADO, "Endereço, número, precisão e classificação territorial confirmados."
        LOGGER.info("Consulta=%s provider=%s status=%s área=%s precisão=%s", query.display_query, selected.candidate.provider, status.value, geography.area, selected.precision.value)
        record_selection = getattr(self.geocoder, "record_selected_candidate", None)
        if callable(record_selection):
            record_selection(selected.candidate)
        record_fallback_outcome = getattr(self.geocoder, "record_fallback_outcome", None)
        if callable(record_fallback_outcome):
            record_fallback_outcome(
                selected_provider=selected.candidate.provider,
                confidence_elevated=LookupFlag.CONFIANCA_COMPOSTA_ELEVADA in flags,
                divergent=divergent,
                secondary_rejected=secondary_rejected,
            )
        return AddressLookupResult(
            original_street=normalized.original,
            original_house_number=house_number,
            normalized_house_number=query.house_number,
            normalized_street=normalized.normalized,
            query_type=normalized.query_type,
            suggested_correction=normalized.suggested_correction,
            suggestion_type=normalized.suggestion_type,
            suggestion_confidence=normalized.suggestion_confidence,
            status=status,
            reason=reason,
            candidate=selected.candidate,
            geography=geography,
            fiscal=fiscal,
            precision=selected.precision,
            assessments=tuple(assessments),
            geocoding_diagnostics=diagnostics,
            selection_reason=self._selection_reason(selected),
            flags=tuple(sorted(flags, key=lambda flag: flag.value)),
            provider_principal=principal,
            provider_distance_m=provider_distance,
            providers_divergent=divergent,
            number_returned=selected.compatibility.returned_number,
            number_compatible=selected.number_matches if normalized.query_type is QueryType.ADDRESS else None,
            street_1_match=selected.compatibility.street_1_matches,
            street_2_match=selected.compatibility.street_2_matches,
            confidence=confidence,
            secondary_rejected=secondary_rejected,
            confidence_promoted=(
                selected.precision is GeocodingPrecision.BAIXA
                and status is LookupStatus.CONFIRMADO
                and confidence.promotion_eligible
            ),
        )


def build_geocoding_service(
    *,
    settings: Settings | None = None,
    areas_path: Path | None = None,
    boundary_path: Path | None = None,
    fiscais_path: Path | None = None,
    fiscal_assignments: dict[str, str] | None = None,
    geoapify_api_key: str | None = None,
    locationiq_token: str | None = None,
    cache_path: Path | None = None,
    use_cache: bool = True,
) -> AddressLookupService:
    resolved_settings = settings or load_settings()
    classifier = GeographicClassifier.from_paths(areas_path, boundary_path)
    geoapify_rect = geoapify_rect_from_boundary(
        classifier.boundary_collection,
        resolved_settings.geoapify_bbox_margin_m,
    )
    effective_geoapify_key = geoapify_api_key if geoapify_api_key is not None else os.environ.get("GEOJARDINS_GEOAPIFY_API_KEY", "")
    geoapify: Geocoder = GeoapifyGeocoder(effective_geoapify_key,
        timeout_s=resolved_settings.geoapify_timeout_s,
        max_retries=resolved_settings.geoapify_max_retries,
        retry_base_s=resolved_settings.geocoding_retry_base_s,
        result_limit=resolved_settings.geoapify_result_limit,
        filter_rect=geoapify_rect,
        rate_limiter=RateLimiter(resolved_settings.geoapify_requests_per_second),
    )
    locationiq: Geocoder | None = None
    effective_locationiq_token = locationiq_token if locationiq_token is not None else os.environ.get("GEOJARDINS_LOCATIONIQ_TOKEN", "")
    if effective_locationiq_token.strip():
        viewbox = locationiq_viewbox_from_boundary(
            classifier.boundary_collection,
            resolved_settings.locationiq_viewbox_margin_deg,
        )
        locationiq = LocationIQGeocoder(effective_locationiq_token,
            timeout_s=resolved_settings.geocoding_timeout_s,
            max_retries=resolved_settings.geocoding_max_retries,
            retry_base_s=resolved_settings.geocoding_retry_base_s,
            result_limit=resolved_settings.locationiq_result_limit,
            viewbox=viewbox,
            bounded=resolved_settings.locationiq_bounded,
            rate_limiter=RateLimiter(resolved_settings.locationiq_requests_per_second),
        )
    cache = PersistentGeocodingCache(cache_path or geocoding_cache_path()) if use_cache else None
    if cache is not None:
        geoapify = CachedGeocoder(geoapify, cache)
        if locationiq is not None:
            locationiq = CachedGeocoder(locationiq, cache)
    # Reutiliza o lookup canônico apenas como evidência mínima de utilidade para
    # decidir se vale gastar o fallback; a classificação final continua abaixo.
    geocoder = FallbackGeocoder(
        geoapify,
        locationiq,
        geographic_validator=lambda candidate: classifier.classify(
            candidate.latitude,
            candidate.longitude,
        ).inside_ugr,
    )
    return AddressLookupService(
        geocoder,
        classifier,
        FiscalResolver(fiscal_assignments)
        if fiscal_assignments is not None
        else (FiscalResolver.from_path(fiscais_path) if fiscais_path else FiscalResolver.from_path()),
        resolved_settings,
        cache=cache,
    )


def build_locationiq_service(
    *,
    settings: Settings | None = None,
    areas_path: Path | None = None,
    boundary_path: Path | None = None,
    fiscais_path: Path | None = None,
    fiscal_assignments: dict[str, str] | None = None,
    geoapify_api_key: str | None = None,
    locationiq_token: str | None = None,
    cache_path: Path | None = None,
    use_cache: bool = True,
) -> AddressLookupService:
    """Compatibilidade: a CLI agora usa Geoapify principal + LocationIQ opcional."""

    return build_geocoding_service(
        settings=settings,
        areas_path=areas_path,
        boundary_path=boundary_path,
        fiscais_path=fiscais_path,
        fiscal_assignments=fiscal_assignments,
        geoapify_api_key=geoapify_api_key,
        locationiq_token=locationiq_token,
        cache_path=cache_path,
        use_cache=use_cache,
    )


def lookup_address(
    street: str,
    house_number: Any,
    *,
    service: AddressLookupService | None = None,
    **service_options: Any,
) -> AddressLookupResult:
    """API reutilizável usada pela GUI e pela CLI, sem parse de stdout."""

    return (service or build_geocoding_service(**service_options)).lookup(street, house_number)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Geocodifica e classifica um endereço na UGR Jardins.")
    parser.add_argument("logradouro")
    parser.add_argument("altura")
    parser.add_argument("--areas", type=Path)
    parser.add_argument("--boundary", type=Path)
    parser.add_argument("--fiscais", type=Path)
    parser.add_argument("--settings", type=Path, default=resource_path("config", "settings.json"))
    parser.add_argument("--verbose", action="store_true", help="Exibe diagnóstico resumido da geocodificação.")
    return parser


def _print_result(result: AddressLookupResult, *, verbose: bool = False) -> None:
    candidate = result.candidate
    print("GeoJardins")
    print()
    print(f"Consulta: {result.original_street}, {result.normalized_house_number or result.original_house_number}")
    print(f"Endereço localizado: {result.formatted_address or '-'}")
    print(f"Latitude: {result.latitude if result.latitude is not None else '-'}")
    print(f"Longitude: {result.longitude if result.longitude is not None else '-'}")
    print(f"UGR Jardins: {'SIM' if result.geography and result.geography.inside_ugr else 'NÃO'}")
    print(f"Área: {result.area or '-'}")
    print(f"Fiscal: {result.fiscal}")
    print(
        "Distância da divisa: "
        + (f"{result.distance_to_boundary_m:.1f} m" if result.distance_to_boundary_m is not None else "-")
    )
    print(f"Match code: {candidate.match_code if candidate else '-'}")
    print(f"Match type: {candidate.match_type if candidate else '-'}")
    print(f"Match level: {candidate.match_level if candidate else '-'}")
    print(f"Provider selecionado: {candidate.provider.upper() if candidate else '-'}")
    print(f"Result type: {candidate.result_type if candidate else '-'}")
    print(f"House number retornado: {candidate.house_number if candidate else '-'}")
    print(f"Confidence: {candidate.confidence if candidate and candidate.confidence is not None else '-'}")
    print(
        "Street confidence: "
        + (str(candidate.confidence_street_level) if candidate and candidate.confidence_street_level is not None else "-")
    )
    print(
        "Building confidence: "
        + (str(candidate.confidence_building_level) if candidate and candidate.confidence_building_level is not None else "-")
    )
    print(f"Precisão: {result.precision.value if result.precision else '-'}")
    print(
        "Modo de geocodificação: "
        + (result.geocoding_diagnostics.mode_label if result.geocoding_diagnostics else "-")
    )
    print(f"Status: {result.status.value}")
    print(f"Google Maps: {result.google_maps_url or '-'}")
    print(f"Motivo: {result.reason}")
    if verbose and result.geocoding_diagnostics:
        diagnostics = result.geocoding_diagnostics
        if diagnostics.geoapify_used:
            print(f"Geoapify: {diagnostics.geoapify_candidate_count} candidato(s)")
            print(f"Candidatos Geoapify utilizáveis: {diagnostics.geoapify_usable_candidate_count}")
            if diagnostics.locationiq_fallback_reason:
                print(f"Motivo para fallback: {diagnostics.locationiq_fallback_reason}")
            print(f"LocationIQ fallback: {'SIM' if diagnostics.locationiq_fallback_used else 'NÃO'}")
            if diagnostics.locationiq_fallback_needed and not diagnostics.locationiq_fallback_used:
                print("LocationIQ: não configurada para executar o fallback necessário")
            if diagnostics.locationiq_fallback_used:
                print(f"LocationIQ: {diagnostics.locationiq_candidate_count} candidato(s)")
        else:
            print(f"Structured: {diagnostics.structured_candidate_count} candidato(s)")
            print(f"Free-form: {diagnostics.freeform_candidate_count} candidato(s)")
        if candidate:
            print(f"Selecionado: {candidate.query_mode} rank {candidate.original_rank}")
            selected_assessment = next(
                (assessment for assessment in result.assessments if assessment.candidate is candidate),
                None,
            )
            if selected_assessment and selected_assessment.precision_evidence:
                print(f"Evidencia de precisao: {selected_assessment.precision_evidence}")
        print(f"Motivo da seleção: {result.selection_reason or '-'}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        service = build_geocoding_service(
            settings=load_settings(args.settings),
            areas_path=args.areas,
            boundary_path=args.boundary,
            fiscais_path=args.fiscais,
        )
    except (GeocodingError, ValueError) as exc:
        print(f"Consulta não executada: {exc}")
        return 2
    result = lookup_address(args.logradouro, args.altura, service=service)
    _print_result(result, verbose=args.verbose)
    return 0 if result.status is not LookupStatus.ERRO_GEOCODIFICACAO else 2


if __name__ == "__main__":
    raise SystemExit(main())
