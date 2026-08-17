"""Regressões offline da Fase 3B.1 para decisão de endereços."""

from __future__ import annotations

import unittest

from shapely.geometry import Polygon

from geojardins.address_lookup import AddressLookupService, LookupFlag, LookupStatus
from geojardins.address_normalizer import normalize_address, street_tokens_compatible
from geojardins.batch_lookup import BatchProcessor, InputRows
from geojardins.classification import GeographicClassifier
from geojardins.fiscais import FiscalResolver
from geojardins.geocoding.fallback import FallbackGeocoder
from geojardins.geocoding.mock import MockGeocoder
from geojardins.geocoding.models import AddressQuery, GeocodeCandidate
from geojardins.settings import Settings
from geojardins.street_dictionary import StreetDictionary, StreetEntry


def candidate(
    *,
    road: str = "Rua Teste",
    number: str | None = "100",
    provider: str = "geoapify",
    latitude: float = -23.595,
    longitude: float = -46.695,
    street_centroid: bool = False,
) -> GeocodeCandidate:
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
        raw_id=f"{provider}-{road}-{number}",
        match_code="exact" if provider == "locationiq" else None,
        match_type="centroid" if street_centroid else ("full_match" if provider == "geoapify" else "point"),
        match_level="street" if street_centroid else ("building" if provider == "locationiq" else None),
        result_type="street" if street_centroid else ("building" if provider == "geoapify" else None),
        confidence=0.0 if provider == "geoapify" else None,
        confidence_street_level=1.0 if provider == "geoapify" else None,
        confidence_building_level=0.0 if provider == "geoapify" else None,
    )


def low_address_candidate(**kwargs: object) -> GeocodeCandidate:
    """Amenity com evidência de posição, mas abaixo do limiar de ALTA."""

    base = candidate(**kwargs)
    return GeocodeCandidate(
        **{
            **base.__dict__,
            "result_type": "amenity",
            "match_type": "inner_part",
            "confidence": 0.80,
            "confidence_street_level": 0.80,
            "confidence_building_level": 0.80,
        }
    )


def classifier() -> GeographicClassifier:
    area = Polygon([(-46.70, -23.60), (-46.68, -23.60), (-46.68, -23.59), (-46.70, -23.59)])
    collection = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"area": "Tomé_1"}, "geometry": area.__geo_interface__}],
    }
    boundary = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": area.__geo_interface__}]}
    return GeographicClassifier(collection, boundary)


def service(
    geocoder: object,
    *,
    settings: Settings | None = None,
    dictionary: StreetDictionary | None = None,
) -> AddressLookupService:
    return AddressLookupService(
        geocoder,  # type: ignore[arg-type]
        classifier(),
        FiscalResolver({"Tomé_1": "PAULO"}),
        settings or Settings(),
        street_dictionary=dictionary or StreetDictionary((
            StreetEntry("TESTE", "teste", "test"),
            StreetEntry("TESTE CORRIGIDO", "teste corrigido", "test"),
        )),
    )


class Phase3B1RegressionTests(unittest.TestCase):
    def test_semantic_abbreviations_are_compatible_for_input_and_provider(self) -> None:
        cases = (
            ("AL DOS JURUPIS", "ALAMEDA DOS JURUPIS"),
            ("ALM PEREIRA GUIMARAES", "ALMIRANTE PEREIRA GUIMARAES"),
            ("BR DE IJUI", "BARAO DE IJUI"),
            ("ENG EDGAR EGIDIO DE SOUSA", "ENGENHEIRO EDGAR EGIDIO DE SOUSA"),
            ("CEL MELO DE OLIVEIRA", "CORONEL MELO DE OLIVEIRA"),
            ("MIN FERREIRA ALVES", "MINISTRO FERREIRA ALVES"),
            ("SEN FELICIO DOS SANTOS", "SENADOR FELICIO DOS SANTOS"),
            ("TTE OTAVIO GOMES", "TENENTE OTAVIO GOMES"),
            ("PC MARREY JUNIOR", "PRACA MARREY JUNIOR"),
            ("TN NOITE ILUSTRADA", "TUNEL NOITE ILUSTRADA"),
        )
        for query, returned in cases:
            with self.subTest(query=query):
                self.assertTrue(street_tokens_compatible(query, returned))

    def test_extra_or_reordered_significant_tokens_are_not_compatible(self) -> None:
        for query, returned in (
            ("RUA PEDROSO", "RUA PEDROSO ALVARENGA"),
            ("RUA MARIA JOSE", "RUA JOSE MARIA LISBOA"),
            ("RUA SAO VICENTE", "RUA SAO VICENTE DE PAULO"),
        ):
            with self.subTest(query=query):
                self.assertFalse(street_tokens_compatible(query, returned))

    def test_pedroso_exact_candidate_wins_and_longer_name_is_rejected(self) -> None:
        query = AddressQuery("RUA PEDROSO", "474")
        exact = GeocodeCandidate(
            **{**candidate(road="Rua Pedroso", number="474").__dict__, "confidence": 1.0, "confidence_building_level": 1.0}
        )
        longer = candidate(road="Rua Pedroso Alvarenga", number="474")
        result = service(MockGeocoder({query.provider_query: [exact, longer]})).lookup("Rua Pedroso", 474)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate, exact)
        rejected = next(item for item in result.assessments if item.candidate == longer)
        self.assertFalse(rejected.compatibility.road_compatible)

    def test_maria_jose_keeps_geoapify_and_marks_incompatible_secondary(self) -> None:
        query = AddressQuery("RUA MARIA JOSE", "51")
        primary = MockGeocoder({query.provider_query: [candidate(road="Rua Maria José", number="51")]})
        secondary = MockGeocoder({query.provider_query: [candidate(road="Rua José Maria Lisboa", number="51", provider="locationiq")]})
        result = service(FallbackGeocoder(primary, secondary)).lookup("Rua Maria José", 51, force_fallback=True)
        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertEqual(result.candidate and result.candidate.provider, "geoapify")
        self.assertIn(LookupFlag.SECONDARY_RESULT_REJECTED, result.flags)
        self.assertIn(LookupFlag.SECONDARY_STREET_INCOMPATIBLE, result.flags)
        self.assertNotEqual(result.status, LookupStatus.ENDERECO_NAO_LOCALIZADO)

    def test_low_precision_real_address_can_promote_without_locationiq(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        result = service(MockGeocoder({query.provider_query: [low_address_candidate()]})).lookup("Rua Teste", 100)
        self.assertEqual(result.precision and result.precision.value, "BAIXA")
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertTrue(result.confidence_promoted)
        self.assertIn(LookupFlag.CONFIANCA_COMPOSTA_ELEVADA, result.flags)
        self.assertIn("Promovido por confiança composta", result.reason)

    def test_street_centroid_near_boundary_and_generic_coordinate_do_not_promote(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        centroid = service(MockGeocoder({query.provider_query: [candidate(street_centroid=True)]})).lookup("Rua Teste", 100)
        self.assertEqual(centroid.status, LookupStatus.CONFERIR)
        self.assertFalse(centroid.confidence_promoted)

        near = service(MockGeocoder({query.provider_query: [candidate(latitude=-23.5901)]})).lookup("Rua Teste", 100)
        self.assertEqual(near.status, LookupStatus.CONFERIR)
        self.assertFalse(near.confidence_promoted)

        promoted = service(MockGeocoder({query.provider_query: [low_address_candidate()]})).lookup("Rua Teste", 100)
        self.assertTrue(promoted.confidence_promoted)
        generic = promoted.with_flags(LookupFlag.COORDENADA_GENERICA, force_conferir=True)
        self.assertFalse(generic.confidence_promoted)
        self.assertNotIn(LookupFlag.CONFIANCA_COMPOSTA_ELEVADA, generic.flags)

    def test_auto_safe_fuzzy_retries_once_but_suggest_does_not(self) -> None:
        original = AddressQuery("RUA TESTE CORRIGIDOA", "100")
        corrected = AddressQuery("RUA TESTE CORRIGIDO", "100")
        auto_settings = Settings(street_fuzzy_suggest_threshold=0.80, street_fuzzy_auto_threshold=0.90)
        geocoder = MockGeocoder({original.provider_query: [], corrected.provider_query: [low_address_candidate(road="Rua Teste Corrigido")]})
        automatic = service(geocoder, settings=auto_settings).lookup("Rua Teste Corrigidoa", 100)
        self.assertEqual(geocoder.call_count, 2)
        self.assertEqual(automatic.status, LookupStatus.CONFIRMADO)
        self.assertEqual(automatic.original_street, "Rua Teste Corrigidoa")
        self.assertEqual(automatic.suggested_correction, "RUA TESTE CORRIGIDO")
        self.assertIn(LookupFlag.CORRECAO_APLICADA, automatic.flags)

        suggest_geocoder = MockGeocoder({original.provider_query: []})
        suggested = service(suggest_geocoder).lookup("Rua Teste Corrigidoa", 100)
        self.assertEqual(suggest_geocoder.call_count, 1)
        self.assertEqual(suggested.status, LookupStatus.ENDERECO_NAO_LOCALIZADO)
        self.assertIn(LookupFlag.CORRECAO_SUGERIDA, suggested.flags)
        self.assertNotIn(LookupFlag.CORRECAO_APLICADA, suggested.flags)

    def test_fallback_is_not_called_for_a_strong_primary_candidate(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        strong_primary = GeocodeCandidate(
            **{**candidate().__dict__, "confidence": 1.0, "confidence_building_level": 1.0}
        )
        primary = MockGeocoder({query.provider_query: [strong_primary]})
        secondary = MockGeocoder({query.provider_query: [candidate(provider="locationiq")]})
        result = service(FallbackGeocoder(primary, secondary)).lookup("Rua Teste", 100)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(secondary.call_count, 0)

    def test_composite_promotion_metric_counts_only_actual_status_changes(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        source = InputRows(("Logradouro", "Altura"), ({"Logradouro": "Rua Teste", "Altura": "100"},))
        _, promoted_summary = BatchProcessor(service(MockGeocoder({query.provider_query: [low_address_candidate()]})), progress=None).process(
            source, street_column="Logradouro", number_column="Altura"
        )
        self.assertEqual(promoted_summary.composite_promotions, 1)

        high_candidate = GeocodeCandidate(
            **{**candidate().__dict__, "confidence": 1.0, "confidence_building_level": 1.0}
        )
        _, confirmed_summary = BatchProcessor(service(MockGeocoder({query.provider_query: [high_candidate]})), progress=None).process(
            source, street_column="Logradouro", number_column="Altura"
        )
        self.assertEqual(confirmed_summary.composite_promotions, 0)

    def test_phase3b2_contextual_aliases_are_shared_with_provider_comparison(self) -> None:
        self.assertEqual(
            normalize_address("R. CON EUGENIO LEITE").normalized,
            "RUA CONEGO EUGENIO LEITE",
        )
        self.assertTrue(
            street_tokens_compatible(
                "RUA CON EUGENIO LEITE", "RUA CONEGO EUGENIO LEITE"
            )
        )
        self.assertEqual(
            normalize_address("R. DR CARLOS ALBERTO DO E. SANTO").normalized,
            "RUA DOUTOR CARLOS ALBERTO DO ESPIRITO SANTO",
        )
        self.assertTrue(
            street_tokens_compatible(
                "RUA DOUTOR CARLOS ALBERTO DO E SANTO",
                "RUA DOUTOR CARLOS ALBERTO DO ESPIRITO SANTO",
            )
        )

    def test_high_confidence_without_status_change_is_not_called_promotion(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        high = GeocodeCandidate(
            **{**candidate().__dict__, "confidence": 1.0, "confidence_building_level": 1.0}
        )
        result = service(MockGeocoder({query.provider_query: [high]})).lookup("Rua Teste", 100)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.confidence and result.confidence.level.value, "HIGH")
        self.assertFalse(result.confidence_promoted)
        self.assertNotIn("Promovido", result.confidence.decision_reason)  # type: ignore[union-attr]
        self.assertIn("Confiança alta", result.confidence.decision_reason)  # type: ignore[union-attr]

    def test_low_precision_blockers_are_reported_for_number_centroid_divider_and_generic(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        no_number = service(MockGeocoder({query.provider_query: [candidate(number=None)]})).lookup("Rua Teste", 100)
        self.assertIn("confidence_blocked_no_exact_number", no_number.confidence.promotion_blockers)  # type: ignore[union-attr]

        centroid = service(MockGeocoder({query.provider_query: [candidate(street_centroid=True)]})).lookup("Rua Teste", 100)
        self.assertIn("confidence_blocked_no_address_evidence", centroid.confidence.promotion_blockers)  # type: ignore[union-attr]

        near = service(MockGeocoder({query.provider_query: [low_address_candidate(latitude=-23.5901)]})).lookup("Rua Teste", 100)
        self.assertIn("confidence_blocked_near_divider", near.confidence.promotion_blockers)  # type: ignore[union-attr]

        generic = service(MockGeocoder({query.provider_query: [low_address_candidate()]})).lookup("Rua Teste", 100)
        generic = generic.with_flags(LookupFlag.COORDENADA_GENERICA, force_conferir=True)
        self.assertIn("confidence_blocked_generic_coordinate", generic.confidence.promotion_blockers)  # type: ignore[union-attr]

        source = InputRows(("Logradouro", "Altura"), ({"Logradouro": "Rua Teste", "Altura": "100"},))
        _, summary = BatchProcessor(service(MockGeocoder({query.provider_query: [candidate(number=None)]})), progress=None).process(
            source, street_column="Logradouro", number_column="Altura"
        )
        self.assertEqual(summary.confidence_promotion_blockers["confidence_blocked_no_exact_number"], 1)

    def test_fuzzy_audit_explains_suggest_and_rejects_ambiguous_auto_safe(self) -> None:
        dictionary = StreetDictionary((StreetEntry("ALBERTO FARIA", "alberto faria", "pdf"),))
        suggested = dictionary.suggest(
            "RUA ALBETO FARIA", suggest_threshold=0.88, auto_threshold=0.97, min_margin=0.04
        )
        self.assertEqual(suggested.decision, "SUGGEST")
        self.assertIn("AUTO_SAFE bloqueado", suggested.reason or "")

        ambiguous = StreetDictionary((
            StreetEntry("MARIA", "maria", "pdf"),
            StreetEntry("MARINA", "marina", "pdf"),
        )).suggest("RUA MARI", suggest_threshold=0.70, auto_threshold=0.97, min_margin=0.20)
        self.assertNotEqual(ambiguous.decision, "AUTO_SAFE")


if __name__ == "__main__":
    unittest.main()
