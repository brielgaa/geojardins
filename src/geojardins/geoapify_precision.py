"""Classificacao isolada da precisao dos resultados Geoapify."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .address_normalizer import normalize_text

if TYPE_CHECKING:
    from .geocoding.models import GeocodeCandidate


# Limiar operacional do GeoJardins para a excecao estrita de amenity/POI.
# A Geoapify usa 0.95 como exemplo de ACCEPT_LEVEL. Este valor e uma escolha
# conservadora do produto e pode virar configuracao no futuro.
AMENITY_HIGH_CONFIDENCE_THRESHOLD = 0.95
BUILDING_HIGH_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class GeoapifyPrecisionDecision:
    level: str
    evidence: str


def is_high_confidence_amenity_address(
    candidate: GeocodeCandidate,
    *,
    number_matches: bool,
    road_similarity: float,
    country_compatible: bool,
    city_compatible: bool,
    inside_ugr: bool,
) -> bool:
    """Avalia o unico caminho especial de ALTA para estabelecimento/POI.

    ``amenity`` e ``inner_part`` nunca bastam isoladamente. O match type fica
    apenas como evidencia descritiva, pois a Geoapify o define como o nivel do
    match, e nao como a qualidade global de todos os componentes.
    """

    threshold = AMENITY_HIGH_CONFIDENCE_THRESHOLD
    return (
        normalize_text(candidate.result_type) == "amenity"
        and number_matches
        and road_similarity >= threshold
        and country_compatible
        and city_compatible
        and inside_ugr
        and (candidate.confidence or 0.0) >= threshold
        and (candidate.confidence_street_level or 0.0) >= threshold
        and (candidate.confidence_building_level or 0.0) >= threshold
    )


def decide_geoapify_precision(
    candidate: GeocodeCandidate,
    number_matches: bool,
    *,
    road_similarity: float = 0.0,
    country_compatible: bool = False,
    city_compatible: bool = False,
    inside_ugr: bool = False,
) -> GeoapifyPrecisionDecision:
    """Converte evidencias Geoapify em ALTA, MEDIA ou BAIXA."""

    result_type = normalize_text(candidate.result_type)
    match_type = normalize_text(candidate.match_type)
    overall = candidate.confidence or 0.0
    street = candidate.confidence_street_level or 0.0
    building = candidate.confidence_building_level or 0.0
    if (
        number_matches
        and result_type == "building"
        and match_type == "full_match"
        and overall >= BUILDING_HIGH_CONFIDENCE_THRESHOLD
        and street >= BUILDING_HIGH_CONFIDENCE_THRESHOLD
        and building >= BUILDING_HIGH_CONFIDENCE_THRESHOLD
    ):
        return GeoapifyPrecisionDecision(
            "ALTA", "building/full_match com numero e confiancas compativeis."
        )
    if is_high_confidence_amenity_address(
        candidate,
        number_matches=number_matches,
        road_similarity=road_similarity,
        country_compatible=country_compatible,
        city_compatible=city_compatible,
        inside_ugr=inside_ugr,
    ):
        return GeoapifyPrecisionDecision(
            "ALTA",
            "amenity no numero solicitado; "
            f"confidence={overall:.2f}; street={street:.2f}; "
            f"building={building:.2f}; via={road_similarity:.2f}.",
        )
    if number_matches and result_type == "building" and building > 0:
        return GeoapifyPrecisionDecision("MEDIA", "building com evidencia parcial de posicao.")
    return GeoapifyPrecisionDecision("BAIXA", _low_precision_evidence(
        number_matches=number_matches,
        road_similarity=road_similarity,
        country_compatible=country_compatible,
        city_compatible=city_compatible,
        inside_ugr=inside_ugr,
    ))


def _low_precision_evidence(
    *,
    number_matches: bool,
    road_similarity: float,
    country_compatible: bool,
    city_compatible: bool,
    inside_ugr: bool,
) -> str:
    if not number_matches:
        return "numero ausente ou incompativel."
    if road_similarity < AMENITY_HIGH_CONFIDENCE_THRESHOLD:
        return "via sem compatibilidade forte para o caminho amenity."
    if not (country_compatible and city_compatible):
        return "contexto de pais/cidade incompativel."
    if not inside_ugr:
        return "coordenada fora da UGR para o caminho de alta precisao."
    return "evidencia Geoapify insuficiente para alta precisao."
