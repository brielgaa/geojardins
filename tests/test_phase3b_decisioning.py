from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from shapely.geometry import Polygon

from geojardins.address_lookup import AddressLookupService, LookupFlag, LookupStatus
from geojardins.address_normalizer import QueryType, load_alias_rules, normalize_address
from geojardins.batch_lookup import BatchRunSummary, compare_batch_reports
from geojardins.build_street_dictionary import filter_pdf_labels
from geojardins.classification import GeographicClassifier
from geojardins.fiscais import FiscalResolver, NOT_CONFIGURED
from geojardins.geocoding.cache import CachedGeocoder, PersistentGeocodingCache
from geojardins.geocoding.fallback import FallbackGeocoder
from geojardins.geocoding.mock import MockGeocoder
from geojardins.geocoding.models import AddressQuery, GeocodeCandidate, GeocodingDiagnostics
from geojardins.settings import Settings
from geojardins.street_dictionary import StreetDictionary, StreetEntry


def candidate(
    *,
    road: str = "Rua Teste",
    number: str | None = "100",
    latitude: float = -23.595,
    longitude: float = -46.695,
    provider: str = "geoapify",
    low_precision: bool = False,
    display_name: str | None = None,
) -> GeocodeCandidate:
    return GeocodeCandidate(
        latitude=latitude,
        longitude=longitude,
        display_name=display_name or f"{road}, {number or ''}, São Paulo",
        house_number=number,
        road=road,
        city="São Paulo",
        state="SP",
        country="Brasil",
        country_code="br",
        provider=provider,
        raw_id=f"{provider}-{road}-{number}",
        match_code="exact" if provider == "locationiq" else None,
        match_type="full_match" if provider == "geoapify" else (None if low_precision else "point"),
        match_level="building" if provider == "locationiq" and not low_precision else None,
        result_type="building" if provider == "geoapify" else None,
        confidence=0.0 if low_precision and provider == "geoapify" else (1.0 if provider == "geoapify" else None),
        confidence_street_level=1.0 if provider == "geoapify" else None,
        confidence_building_level=0.0 if low_precision and provider == "geoapify" else (1.0 if provider == "geoapify" else None),
    )


def classifier() -> GeographicClassifier:
    area = Polygon([(-46.70, -23.60), (-46.68, -23.60), (-46.68, -23.59), (-46.70, -23.59)])
    areas = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"area": "Tomé_1"}, "geometry": area.__geo_interface__}]}
    boundary = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": area.__geo_interface__}]}
    return GeographicClassifier(areas, boundary)


def service(geocoder: object, *, fiscal: FiscalResolver | None = None, settings: Settings | None = None) -> AddressLookupService:
    return AddressLookupService(
        geocoder,  # type: ignore[arg-type]
        classifier(),
        fiscal or FiscalResolver({"Tomé_1": "PAULO"}),
        settings or Settings(),
        street_dictionary=StreetDictionary((
            StreetEntry("TESTE", "teste", "test"),
            StreetEntry("GUATEMALA", "guatemala", "test"),
            StreetEntry("AGOSTINHO RODRIGUES FILHO", "agostinho rodrigues filho", "test"),
        )),
    )


class Phase3BTests(unittest.TestCase):
    def test_fiscais_are_loaded_from_external_configuration(self) -> None:
        resolver = FiscalResolver.from_path(Path("config/fiscais.json"))
        self.assertEqual(resolver.for_area("Tomé_1"), "PAULO")
        self.assertEqual(resolver.for_area("Tomé_6"), "YAGO")
        self.assertEqual(resolver.for_area("Tomé_99"), NOT_CONFIGURED)

    def test_contextual_title_aliases_only_expand_after_street_type(self) -> None:
        rules = load_alias_rules()
        self.assertEqual(normalize_address("R. CAP Cavalcanti", rules=rules).normalized, "RUA CAPITAO CAVALCANTI")
        self.assertEqual(normalize_address("R. BRIG Luis Antonio", rules=rules).normalized, "RUA BRIGADEIRO LUIS ANTONIO")
        self.assertEqual(normalize_address("R. DESEM Aragao", rules=rules).normalized, "RUA DESEMBARGADOR ARAGAO")
        self.assertEqual(normalize_address("R. GAL Camisao", rules=rules).normalized, "RUA GENERAL CAMISAO")
        self.assertEqual(normalize_address("R. MAEST Cardim", rules=rules).normalized, "RUA MAESTRO CARDIM")
        self.assertEqual(normalize_address("R. PROF Picarolo", rules=rules).normalized, "RUA PROFESSOR PICAROLO")
        self.assertEqual(normalize_address("R. JORN Aloysio Biondi", rules=rules).normalized, "RUA JORNALISTA ALOYSIO BIONDI")
        self.assertEqual(normalize_address("CAP Cavalcanti", rules=rules).normalized, "CAP CAVALCANTI")
        self.assertEqual(normalize_address("VD. Pompeia", rules=rules).normalized, "VIADUTO POMPEIA")

    def test_local_dictionary_suggests_typo_but_ambiguous_candidate_stays_none(self) -> None:
        dictionary = StreetDictionary((StreetEntry("GUATEMALA", "guatemala", "pdf"),))
        suggestion = dictionary.suggest("RUA GUATELAMA", suggest_threshold=0.88, auto_threshold=0.97, min_margin=0.04)
        self.assertEqual((suggestion.decision, suggestion.canonical), ("SUGGEST", "RUA GUATEMALA"))
        ambiguous = StreetDictionary((StreetEntry("MARIA", "maria", "pdf"), StreetEntry("MARINA", "marina", "pdf")))
        self.assertEqual(ambiguous.suggest("RUA MARI", suggest_threshold=0.70, auto_threshold=0.97, min_margin=0.20).decision, "NONE")

    def test_dictionary_builder_filters_non_street_labels(self) -> None:
        entries, rejected = filter_pdf_labels(((1, "A"), (1, "SEM DENOMINACAO"), (1, "GUATEMALA")))
        self.assertEqual([entry.canonical for entry in entries], ["GUATEMALA"])
        self.assertEqual(len(rejected), 2)

    def test_low_precision_with_qualified_address_evidence_and_safe_distance_is_promoted(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        primary = MockGeocoder({
            query.provider_query: [replace(
                candidate(low_precision=True),
                result_type="amenity",
                match_type="inner_part",
                confidence=0.80,
                confidence_street_level=0.80,
                confidence_building_level=0.80,
            )]
        })
        secondary = MockGeocoder({query.provider_query: [candidate(provider="locationiq", low_precision=True, longitude=-46.69495)]})
        geocoder = FallbackGeocoder(primary, secondary)
        result = service(geocoder).lookup("Rua Teste", 100, force_fallback=True)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.confidence and result.confidence.level.value, "HIGH")
        self.assertIn(LookupFlag.CONFIANCA_COMPOSTA_ELEVADA, result.flags)
        self.assertEqual(geocoder.fallback_elevated_confidence, 1)

    def test_low_precision_near_boundary_is_not_promoted(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        primary = MockGeocoder({query.provider_query: [candidate(low_precision=True, latitude=-23.5901)]})
        secondary = MockGeocoder({query.provider_query: [candidate(provider="locationiq", low_precision=True, latitude=-23.5901, longitude=-46.69495)]})
        result = service(FallbackGeocoder(primary, secondary)).lookup("Rua Teste", 100, force_fallback=True)
        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertEqual(result.confidence and result.confidence.level.value, "MEDIUM")

    def test_incompatible_secondary_does_not_downgrade_strong_primary(self) -> None:
        query = AddressQuery("RUA MARIA JOSE", "51")
        primary = MockGeocoder({query.provider_query: [candidate(road="Rua Maria Jose", number="51")]})
        secondary = MockGeocoder({query.provider_query: [candidate(road="Rua José Maria Lisboa", number="51", provider="locationiq")]})
        geocoder = FallbackGeocoder(primary, secondary)
        result = service(geocoder).lookup("Rua Maria Jose", 51, force_fallback=True)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertIn(LookupFlag.SECONDARY_RESULT_REJECTED, result.flags)
        self.assertNotIn(LookupFlag.PROVIDERS_DIVERGENTES, result.flags)
        self.assertEqual(geocoder.fallback_result_rejected, 1)

    def test_fallback_metrics_record_resolved_not_found(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        geocoder = FallbackGeocoder(MockGeocoder({query.provider_query: []}), MockGeocoder({query.provider_query: [candidate(provider="locationiq", low_precision=True)]}))
        result = service(geocoder).lookup("Rua Teste", 100)
        self.assertEqual(result.candidate and result.candidate.provider, "locationiq")
        self.assertEqual(geocoder.fallback_resolved_not_found, 1)
        self.assertEqual(geocoder.fallback_changed_winning_provider, 1)

    def test_intersection_requires_both_streets_and_maps_link_ignores_number(self) -> None:
        query = AddressQuery("RUA KOKO ICHIKAWA X AVENIDA 23 DE MAIO", None, query_type=QueryType.INTERSECTION, street_1="RUA KOKO ICHIKAWA", street_2="AVENIDA 23 DE MAIO")
        both = candidate(road="Rua Koko Ichikawa", number=None, display_name="Rua Koko Ichikawa x Avenida 23 de Maio, São Paulo")
        complete = service(MockGeocoder({query.provider_query: [both]})).lookup("R. Koko Ichikawa x Av. 23 de Maio", 500)
        self.assertEqual(complete.status, LookupStatus.CONFERIR)
        self.assertTrue(complete.street_1_match)
        self.assertTrue(complete.street_2_match)
        partial = service(MockGeocoder({query.provider_query: [candidate(road="Rua Koko Ichikawa", number=None)]})).lookup("R. Koko Ichikawa x Av. 23 de Maio", 2011)
        self.assertIn(LookupFlag.CRUZAMENTO_PARCIAL, partial.flags)
        no_candidate = service(MockGeocoder()).lookup("R. Koko Ichikawa x Av. 23 de Maio", 500)
        self.assertNotIn("500", no_candidate.google_maps_url or "")

    def test_formatting_normalization_does_not_emit_noise_flag(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        result = service(MockGeocoder({query.provider_query: [candidate()]})).lookup("R. Teste", 100)
        self.assertNotIn(LookupFlag.ENDERECO_NORMALIZADO, result.flags)
        self.assertNotIn(LookupFlag.ALIAS_APLICADO, result.flags)

    def test_cache_is_deterministic_and_records_provider_mode_breakdown(self) -> None:
        class LocationDelegate:
            provider_name = "locationiq"

            def __init__(self) -> None:
                self.calls = 0
                self.last_diagnostics = GeocodingDiagnostics(structured_used=True, freeform_used=True)

            def geocode(self, _: AddressQuery) -> list[GeocodeCandidate]:
                self.calls += 1
                return [candidate(provider="locationiq", low_precision=True)]

        with tempfile.TemporaryDirectory() as temporary:
            query = AddressQuery("RUA TESTE", "100")
            cache = PersistentGeocodingCache(Path(temporary) / "cache.sqlite")
            cached = CachedGeocoder(LocationDelegate(), cache)
            first = cached.geocode(query)
            second = cached.geocode(query)
            self.assertEqual(first, second)
            self.assertEqual(cached.delegate.calls, 1)
            self.assertEqual(cache.stats.hits_by_provider_mode["locationiq:structured"], 1)
            self.assertEqual(cache.stats.misses_by_provider_mode["locationiq:freeform"], 1)

    def test_cache_reuses_compatible_phase3a_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cache.sqlite"
            cache = PersistentGeocodingCache(path)
            query = AddressQuery("RUA TESTE", "100")
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "INSERT INTO geocoding_cache(provider,query_key,schema_version,candidates_json,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
                    ("locationiq", query.cache_key.replace("3b-1|", "3a-1|", 1), "3a-1", json.dumps([asdict(candidate(provider="locationiq", low_precision=True))]), "{}", "2026-01-01T00:00:00Z"),
                )
                connection.commit()
            finally:
                connection.close()
            class Delegate:
                provider_name = "locationiq"
                calls = 0

                def geocode(self, _: AddressQuery) -> list[GeocodeCandidate]:
                    self.calls += 1
                    return []

            cached = CachedGeocoder(Delegate(), cache)
            self.assertEqual(len(cached.geocode(query)), 1)
            self.assertEqual(cached.delegate.calls, 0)

    def test_comparison_reports_transitions_flags_and_area_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            baseline = Path(temporary) / "before.json"
            baseline.write_text(json.dumps({"statuses": {"CONFERIR": 1}, "records": [{"key": "2|RUA TESTE|100", "status": "CONFERIR", "flags": ["BAIXA_PRECISAO"], "candidate_area": "Tomé_1"}]}), encoding="utf-8")
            after = BatchRunSummary(1, {}, {}, 0, 0, records=()).to_report()
            after["statuses"] = {"CONFIRMADO": 1}
            after["records"] = [{"key": "2|RUA TESTE|100", "status": "CONFIRMADO", "flags": ["CONFIANCA_COMPOSTA_ELEVADA"], "candidate_area": "Tomé_2"}]
            comparison = compare_batch_reports(baseline, after)
            self.assertEqual(comparison["status_transitions"], {"CONFERIR -> CONFIRMADO": 1})
            self.assertEqual(comparison["flags_added"], {"CONFIANCA_COMPOSTA_ELEVADA": 1})
            self.assertEqual(len(comparison["area_changes"]), 1)


if __name__ == "__main__":
    unittest.main()
