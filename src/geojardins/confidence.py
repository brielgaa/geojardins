"""Avaliação composta, explicável e independente de providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class ConfidenceAssessment:
    """Evidências que explicam a decisão operacional, sem score oculto."""

    level: ConfidenceLevel
    score: int
    positive_evidence: tuple[str, ...]
    negative_evidence: tuple[str, ...]
    decision_reason: str
    promotion_eligible: bool = False
    promotion_blockers: tuple[str, ...] = ()


def assess_composite_confidence(
    *,
    street_compatible: bool,
    number_compatible: bool,
    provider_building_evidence: bool,
    secondary_agreement: str | None,
    secondary_rejected: bool,
    generic_coordinate: bool,
    provider_divergent: bool,
    near_boundary: bool,
    safe_from_boundary: bool,
    inside_exclusive_area: bool,
    street_dictionary_match: bool,
    conflicting_strong_candidate: bool = False,
    boundary_distance_m: float | None = None,
    homonym_risk: bool = False,
    area_overlap: bool = False,
) -> ConfidenceAssessment:
    """Combina evidências em regras legíveis, não em um limiar secreto.

    O score é apenas uma soma de auditoria. A elevação de BAIXA_PRECISAO exige
    todos os gates explícitos em ``promotion_eligible``; portanto, uma soma alta
    nunca mascara rua/número incompatíveis, divisa ou divergência forte.
    """

    positive: list[str] = []
    negative: list[str] = []
    score = 0

    def plus(points: int, text: str) -> None:
        nonlocal score
        score += points
        positive.append(text)

    def minus(points: int, text: str) -> None:
        nonlocal score
        score -= points
        negative.append(text)

    if street_compatible:
        plus(3, "logradouro compatível")
    else:
        minus(8, "logradouro incompatível")
    if number_compatible:
        plus(3, "número compatível")
    else:
        minus(5, "número ausente ou incompatível")
    if provider_building_evidence:
        plus(2, "provider principal retornou evidência de imóvel/match")
    if secondary_agreement == "STRONG":
        plus(3, "provider secundário concorda fortemente")
    elif secondary_agreement == "ACCEPTABLE":
        plus(2, "provider secundário concorda dentro da tolerância operacional")
    elif secondary_rejected:
        plus(1, "resultado secundário incompatível foi rejeitado")
    if street_dictionary_match:
        plus(1, "logradouro consta no dicionário local da UGR")
    if inside_exclusive_area:
        plus(2, "coordenada em área exclusiva da UGR")
    else:
        minus(6, "área geográfica não exclusiva")
    if generic_coordinate:
        minus(7, "coordenada genérica detectada")
    else:
        plus(1, "sem coordenada genérica")
    if provider_divergent:
        minus(7, "providers fortes divergem espacialmente")
    if conflicting_strong_candidate:
        minus(7, "outro candidato forte contradiz o resultado selecionado")
    if near_boundary:
        minus(4, "próximo da divisa operacional")
    elif safe_from_boundary:
        plus(2, "distância segura da divisa")

    blockers: list[str] = []
    if not street_compatible:
        blockers.append("confidence_blocked_street_incompatible")
    if not number_compatible:
        blockers.append("confidence_blocked_no_exact_number")
    if not provider_building_evidence:
        blockers.append("confidence_blocked_no_address_evidence")
    if generic_coordinate:
        blockers.append("confidence_blocked_generic_coordinate")
    if near_boundary:
        blockers.append("confidence_blocked_near_divider")
    elif not safe_from_boundary:
        blockers.append("confidence_blocked_not_safely_inside")
    if area_overlap:
        blockers.append("confidence_blocked_overlap")
    elif not inside_exclusive_area:
        blockers.append("confidence_blocked_ambiguity")
    if homonym_risk:
        blockers.append("confidence_blocked_homonym")
    if provider_divergent or conflicting_strong_candidate:
        blockers.append("confidence_blocked_provider_conflict")

    promotion_eligible = not blockers
    if promotion_eligible:
        level = ConfidenceLevel.HIGH
        distance = (
            f"distância de {boundary_distance_m:.1f} m da divisa"
            if boundary_distance_m is not None
            else "distância segura da divisa"
        )
        reason = (
            "Confiança alta: logradouro compatível; número compatível; evidência "
            "de imóvel/endereço; área exclusiva; "
            f"{distance}; sem coordenada genérica ou candidato forte contraditório."
        )
    elif street_compatible and number_compatible and inside_exclusive_area and not provider_divergent:
        level = ConfidenceLevel.MEDIUM
        reason = "Confiança intermediária; promoção bloqueada por: " + ", ".join(blockers)
    else:
        level = ConfidenceLevel.LOW
        reason = "Confiança baixa; promoção bloqueada por: " + ", ".join(blockers)
    return ConfidenceAssessment(
        level,
        score,
        tuple(positive),
        tuple(negative),
        reason,
        promotion_eligible,
        tuple(blockers),
    )
