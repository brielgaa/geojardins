"""Regressões da auditoria semântica de conflito entre candidatos (Fase 3B.2.1)."""

from __future__ import annotations

import unittest

from shapely.geometry import Polygon

from geojardins.address_lookup import AddressLookupService, LookupFlag, LookupStatus
from geojardins.address_normalizer import AliasRules
from geojardins.classification import GeographicClassifier
from geojardins.fiscais import FiscalResolver
from geojardins.geocoding.fallback import FallbackGeocoder
from geojardins.geocoding.mock import MockGeocoder
from geojardins.geocoding.models import AddressQuery, GeocodeCandidate
from geojardins.settings import Settings
from geojardins.street_dictionary import StreetDictionary


def candidate(
    *,
    provider: str = "geoapify",
    road: str = "Rua Teste",
    number: str | None = "100",
    latitude: float = -23.595,
    longitude: float = -46.695,
    raw_id: str = "candidate",
    structured_address: bool = True,
) -> GeocodeCandidate:
    """Cria candidato mockado forte, salvo quando ``structured_address`` é falso."""

    if provider == "geoapify":
        return GeocodeCandidate(
            latitude=latitude,
            longitude=longitude,
            display_name=f"{road}, {number or ''}, São Paulo",
            house_number=number,
            road=road,
            city="São Paulo",
            state="SP",
            country="Brasil",
            country_code="br",
            provider=provider,
            raw_id=raw_id,
            match_type="full_match" if structured_address else "inner_part",
            result_type="building" if structured_address else "amenity",
            confidence=0.5 if structured_address else 0.0,
            confidence_street_level=1.0,
            confidence_building_level=0.0,
        )
    return GeocodeCandidate(
        latitude=latitude,
        longitude=longitude,
        display_name=f"{road}, {number or ''}, São Paulo",
        house_number=number,
        road=road,
        city="São Paulo",
        state="SP",
        country="Brasil",
        country_code="br",
        provider=provider,
        raw_id=raw_id,
        match_code="exact",
        match_type="point",
        match_level="building",
    )


def service(
    primary: list[GeocodeCandidate],
    secondary: list[GeocodeCandidate] | None = None,
    *,
    street: str = "Rua Teste",
    number: str = "100",
) -> AddressLookupService:
    area = Polygon([
        (-46.71, -23.61),
        (-46.67, -23.61),
        (-46.67, -23.58),
        (-46.71, -23.58),
    ])
    collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"area": "Tomé_1"},
            "geometry": area.__geo_interface__,
        }],
    }
    classifier = GeographicClassifier(collection, collection)
    query = AddressQuery(street.upper(), number)
    if secondary is None:
        geocoder: object = MockGeocoder({query.provider_query: primary})
    else:
        geocoder = FallbackGeocoder(
            MockGeocoder({query.provider_query: primary}),
            MockGeocoder({query.provider_query: secondary}),
        )
    return AddressLookupService(
        geocoder,  # type: ignore[arg-type]
        classifier,
        FiscalResolver({}),
        Settings(provider_divergence_m=50),
        alias_rules=AliasRules({}, {}, {}),
        street_dictionary=StreetDictionary(()),
    )


class ProviderConflictSemanticsTests(unittest.TestCase):
    def lookup(
        self,
        primary: list[GeocodeCandidate],
        secondary: list[GeocodeCandidate] | None = None,
        *,
        street: str = "Rua Teste",
        number: str = "100",
    ):
        return service(primary, secondary, street=street, number=number).lookup(
            street,
            number,
            force_fallback=secondary is not None,
        )

    def assert_no_provider_conflict(self, result: object) -> None:
        confidence = getattr(result, "confidence")
        self.assertIsNotNone(confidence)
        self.assertNotIn(
            "confidence_blocked_provider_conflict",
            confidence.promotion_blockers,
        )
        self.assertFalse(getattr(result, "providers_divergent"))

    def test_incompatible_rejected_secondary_is_not_an_active_conflict(self) -> None:
        result = self.lookup(
            [candidate(raw_id="primary")],
            [candidate(provider="locationiq", road="Rua Outro Logradouro", raw_id="secondary")],
        )

        self.assertIn(LookupFlag.SECONDARY_RESULT_REJECTED, result.flags)
        self.assertIn(LookupFlag.SECONDARY_STREET_INCOMPATIBLE, result.flags)
        self.assert_no_provider_conflict(result)

    def test_weak_rejected_secondary_is_not_an_active_conflict(self) -> None:
        result = self.lookup(
            [candidate(raw_id="primary")],
            [candidate(provider="locationiq", number=None, raw_id="street-centroid")],
        )

        self.assertIn(LookupFlag.SECONDARY_RESULT_REJECTED, result.flags)
        self.assertTrue(result.secondary_rejected)
        self.assert_no_provider_conflict(result)

    def test_strong_concordant_secondary_at_zero_distance_has_no_conflict(self) -> None:
        result = self.lookup(
            [candidate(raw_id="primary")],
            [candidate(provider="locationiq", raw_id="secondary")],
        )

        self.assertEqual(result.provider_distance_m, 0.0)
        self.assertFalse(result.secondary_rejected)
        self.assert_no_provider_conflict(result)

    def test_strong_divergent_secondary_remains_an_active_conflict(self) -> None:
        result = self.lookup(
            [candidate(raw_id="primary")],
            [candidate(provider="locationiq", longitude=-46.690, raw_id="secondary")],
        )

        self.assertIn(LookupFlag.PROVIDERS_DIVERGENTES, result.flags)
        self.assertIn(
            "confidence_blocked_provider_conflict",
            result.confidence.promotion_blockers,  # type: ignore[union-attr]
        )
        self.assertTrue(result.providers_divergent)

    def test_maria_jose_rejects_jose_maria_lisboa_without_conflict(self) -> None:
        result = self.lookup(
            [candidate(road="Rua Maria Jose", number="51", raw_id="primary")],
            [candidate(
                provider="locationiq",
                road="Rua Jose Maria Lisboa",
                number="51",
                raw_id="secondary",
            )],
            street="Rua Maria Jose",
            number="51",
        )

        self.assertIn(LookupFlag.SECONDARY_RESULT_REJECTED, result.flags)
        self.assertIn(LookupFlag.SECONDARY_STREET_INCOMPATIBLE, result.flags)
        self.assert_no_provider_conflict(result)

    def test_no_provider_conflict_can_still_be_conferir_for_other_blocker(self) -> None:
        result = self.lookup([candidate(structured_address=False)])

        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertIn(
            "confidence_blocked_no_address_evidence",
            result.confidence.promotion_blockers,  # type: ignore[union-attr]
        )
        self.assert_no_provider_conflict(result)

    def test_rejected_secondary_does_not_automatically_promote_primary(self) -> None:
        result = self.lookup(
            [candidate(structured_address=False, raw_id="primary")],
            [candidate(provider="locationiq", number=None, raw_id="secondary")],
        )

        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertTrue(result.secondary_rejected)
        self.assertFalse(result.confidence_promoted)
        self.assertIn(
            "confidence_blocked_no_address_evidence",
            result.confidence.promotion_blockers,  # type: ignore[union-attr]
        )
        self.assert_no_provider_conflict(result)

    def test_low_precision_promotes_only_when_all_existing_gates_are_satisfied(self) -> None:
        result = self.lookup([candidate(raw_id="structured-low")])

        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertTrue(result.confidence_promoted)
        self.assertEqual(result.confidence.promotion_blockers, ())  # type: ignore[union-attr]

    def test_pedroso_does_not_accept_pedroso_alvarenga_as_secondary(self) -> None:
        result = self.lookup(
            [candidate(road="Rua Pedroso", number="474", raw_id="primary")],
            [candidate(
                provider="locationiq",
                road="Rua Pedroso Alvarenga",
                number="474",
                raw_id="secondary",
            )],
            street="Rua Pedroso",
            number="474",
        )

        self.assertIn(LookupFlag.SECONDARY_RESULT_REJECTED, result.flags)
        self.assertIn(LookupFlag.SECONDARY_STREET_INCOMPATIBLE, result.flags)
        self.assert_no_provider_conflict(result)

    def test_zero_distance_rejected_locationiq_does_not_hide_real_primary_candidates(self) -> None:
        result = self.lookup(
            [
                candidate(raw_id="selected"),
                candidate(longitude=-46.690, raw_id="other-strong"),
            ],
            [candidate(provider="locationiq", number=None, raw_id="street-centroid")],
        )

        self.assertEqual(result.provider_distance_m, 0.0)
        self.assertTrue(result.secondary_rejected)
        self.assertIn(
            "confidence_blocked_provider_conflict",
            result.confidence.promotion_blockers,  # type: ignore[union-attr]
        )
        self.assertNotIn(LookupFlag.PROVIDERS_DIVERGENTES, result.flags)


if __name__ == "__main__":
    unittest.main()
