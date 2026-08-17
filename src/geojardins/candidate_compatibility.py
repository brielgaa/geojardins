"""Compatibilidade mínima compartilhada entre fallback e classificação final."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .address_normalizer import (
    compare_house_numbers,
    normalize_text,
    street_similarity,
    street_tokens_compatible,
)

if TYPE_CHECKING:
    from .geocoding.models import AddressQuery, GeocodeCandidate


@dataclass(frozen=True)
class CandidateCompatibility:
    coordinates_valid: bool
    country_compatible: bool
    city_compatible: bool
    road_compatible: bool
    road_similarity: float
    number_available: bool
    number_matches: bool
    street_1_matches: bool | None = None
    street_2_matches: bool | None = None
    requested_number: str | None = None
    returned_number: str | None = None
    geographically_plausible: bool = True

    @property
    def compatible(self) -> bool:
        """Indica se o candidato pode chegar à classificação territorial final."""

        return (
            self.coordinates_valid
            and self.country_compatible
            and self.city_compatible
            and (self.road_compatible or self.intersection_partial)
        )

    @property
    def usable(self) -> bool:
        """Indica se há evidência para dispensar outro provider, sem exigir ALTA."""

        return self.compatible and self.number_matches and self.geographically_plausible

    @property
    def intersection_complete(self) -> bool:
        return self.street_1_matches is True and self.street_2_matches is True

    @property
    def intersection_partial(self) -> bool:
        return bool(self.street_1_matches) != bool(self.street_2_matches)

    @property
    def reason(self) -> str:
        failed: list[str] = []
        if not self.coordinates_valid:
            failed.append("coordenadas inválidas")
        if not self.country_compatible:
            failed.append("país incompatível")
        if not self.city_compatible:
            failed.append("cidade/contexto SP incompatível")
        if not self.road_compatible:
            failed.append("via incompatível" if not self.intersection_partial else "apenas uma via do cruzamento foi reconhecida")
        if not self.geographically_plausible:
            failed.append("fora da UGR")
        if self.compatible and not self.number_matches:
            failed.append("número ausente ou incompatível")
        return "; ".join(failed) if failed else "compatível"


def evaluate_candidate_compatibility(
    candidate: GeocodeCandidate,
    query: AddressQuery,
    *,
    geographically_plausible: bool = True,
) -> CandidateCompatibility:
    """Aplica uma única regra básica, independente do provider.

    O contexto de São Paulo aceita cidade explícita ou, quando a cidade vier
    ausente, o código administrativo `SP` em state/state_code/county_code.
    """

    coordinates_valid = math.isfinite(candidate.latitude) and math.isfinite(candidate.longitude)
    country = normalize_text(candidate.country_code)
    country_compatible = country == "br" or normalize_text(candidate.country) in {"brasil", "brazil"}
    city = normalize_text(candidate.city)
    administrative_codes = {
        normalize_text(candidate.state),
        normalize_text(candidate.state_code),
        normalize_text(candidate.county_code),
    }
    city_compatible = city == "sao paulo" or (
        not city and bool({"sp", "sao paulo"} & administrative_codes)
    )
    if query.is_intersection:
        # Alguns providers deixam somente uma via em ``road``, mas trazem as
        # duas no display_name. Avaliamos as duas evidências separadamente e
        # nunca chamamos a interseção de completa se apenas uma casar.
        def intersection_similarity(street: str) -> float:
            return max(
                street_similarity(street, candidate.road),
                street_similarity(street, candidate.display_name, allow_containment=True),
            )

        first_similarity = intersection_similarity(query.street_1 or query.street)
        second_similarity = intersection_similarity(query.street_2 or "")
        first_matches = first_similarity >= 0.5
        second_matches = second_similarity >= 0.5
        similarity = min(first_similarity, second_similarity) if first_matches and second_matches else max(first_similarity, second_similarity)
        road_compatible = first_matches and second_matches
    else:
        similarity = street_similarity(query.street, candidate.road)
        road_compatible = street_tokens_compatible(query.street, candidate.road)
        first_matches = None
        second_matches = None
    number = compare_house_numbers(query.house_number, candidate.house_number)
    return CandidateCompatibility(
        coordinates_valid=coordinates_valid,
        country_compatible=country_compatible,
        city_compatible=city_compatible,
        road_compatible=road_compatible,
        road_similarity=similarity,
        number_available=number.available,
        number_matches=number.compatible,
        street_1_matches=first_matches,
        street_2_matches=second_matches,
        requested_number=number.requested,
        returned_number=number.returned,
        geographically_plausible=geographically_plausible,
    )
