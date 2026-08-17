from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from openpyxl import load_workbook
from shapely.geometry import Polygon
from shapely.ops import unary_union

from geojardins.address_lookup import (
    AddressLookupService,
    LookupFlag,
    LookupStatus,
)
from geojardins.address_normalizer import (
    QueryType,
    compare_house_numbers,
    normalize_address,
)
from geojardins.batch_lookup import BatchProcessor, InputRows, main as batch_main, write_batch_report, write_output
from geojardins.classification import GeographicClassifier
from geojardins.fiscais import FiscalResolver
from geojardins.geocoding.cache import CachedGeocoder, PersistentGeocodingCache
from geojardins.geocoding.fallback import FallbackGeocoder
from geojardins.geocoding.mock import MockGeocoder
from geojardins.geocoding.models import AddressQuery, GeocodeCandidate
from geojardins.settings import Settings


def candidate(
    *,
    latitude: float = -23.595,
    longitude: float = -46.695,
    road: str = "Rua Teste",
    number: str | None = "100",
    provider: str = "mock",
    place_id: str = "place-1",
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
        raw_id=place_id,
        match_code="exact",
        match_type="point",
        match_level="building",
        result_type="building" if provider == "geoapify" else None,
        confidence=1.0 if provider == "geoapify" else None,
        confidence_street_level=1.0 if provider == "geoapify" else None,
        confidence_building_level=1.0 if provider == "geoapify" else None,
    )


def classifier(*, overlap: bool = False) -> GeographicClassifier:
    first = Polygon([(-46.70, -23.60), (-46.68, -23.60), (-46.68, -23.59), (-46.70, -23.59)])
    second = (
        Polygon([(-46.697, -23.598), (-46.685, -23.598), (-46.685, -23.592), (-46.697, -23.592)])
        if overlap
        else Polygon([(-46.68, -23.60), (-46.66, -23.60), (-46.66, -23.59), (-46.68, -23.59)])
    )
    areas = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"area": "Tomé_1"}, "geometry": first.__geo_interface__},
            {"type": "Feature", "properties": {"area": "Tomé_2"}, "geometry": second.__geo_interface__},
        ],
    }
    boundary = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": unary_union([first, second]).__geo_interface__}],
    }
    return GeographicClassifier(areas, boundary)


def service(geocoder: object, *, overlap: bool = False, divergence_m: float = 75) -> AddressLookupService:
    return AddressLookupService(
        geocoder,  # type: ignore[arg-type]
        classifier(overlap=overlap),
        FiscalResolver({}),
        Settings(boundary_warning_m=30, provider_divergence_m=divergence_m, generic_coordinate_tolerance_m=5),
    )


class NormalizerAndQueryTests(unittest.TestCase):
    def test_safe_aliases_preserve_original_and_do_not_apply_suggestions(self) -> None:
        result = normalize_address("AV. DR ARNALDO")
        self.assertEqual(result.original, "AV. DR ARNALDO")
        self.assertEqual(result.normalized, "AVENIDA DOUTOR ARNALDO")
        suggestion = normalize_address("Rodrigigues")
        self.assertEqual(suggestion.normalized, "RODRIGIGUES")
        self.assertEqual(suggestion.suggested_correction, "RODRIGUES")

    def test_intersection_ignores_height_and_keeps_two_streets(self) -> None:
        result = normalize_address("R. Koko Ichikawa X Av. 23 de maio")
        self.assertEqual(result.query_type, QueryType.INTERSECTION)
        self.assertEqual(result.street_1, "RUA KOKO ICHIKAWA")
        self.assertEqual(result.street_2, "AVENIDA 23 DE MAIO")
        self.assertEqual(result.normalized, "RUA KOKO ICHIKAWA X AVENIDA 23 DE MAIO")

    def test_number_range_is_compatible_but_unrelated_number_is_not(self) -> None:
        self.assertTrue(compare_house_numbers("453", "440-460").compatible)
        self.assertFalse(compare_house_numbers("453", "97").compatible)


class RobustnessIntegrationTests(unittest.TestCase):
    def test_intersection_returns_flags_and_google_text_link_without_coordinates(self) -> None:
        result = service(MockGeocoder()).lookup("R. Aspásia esquina R. Iguatemi", "")
        self.assertEqual(result.status, LookupStatus.ENDERECO_NAO_LOCALIZADO)
        self.assertIn(LookupFlag.CRUZAMENTO, result.flags)
        self.assertTrue((result.google_maps_url or "").startswith("https://www.google.com/maps/search/?api=1&query="))

    def test_two_provider_divergence_is_never_silent(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        primary = MockGeocoder({query.provider_query: [candidate(provider="geoapify", place_id="geo")]})
        secondary = MockGeocoder({query.provider_query: [candidate(provider="locationiq", longitude=-46.690, place_id="liq")]})
        result = service(FallbackGeocoder(primary, secondary), divergence_m=50).lookup("Rua Teste", 100, force_fallback=True)
        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertIn(LookupFlag.PROVIDERS_DIVERGENTES, result.flags)
        self.assertGreater(result.provider_distance_m or 0, 50)

    def test_overlap_sets_candidates_and_never_chooses_one_area(self) -> None:
        query = AddressQuery("RUA TESTE", "100")
        result = service(MockGeocoder({query.provider_query: [candidate()]}), overlap=True).lookup("Rua Teste", 100)
        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertIn(LookupFlag.SOBREPOSICAO_AREAS, result.flags)
        self.assertEqual(set(result.areas), {"Tomé_1", "Tomé_2"})
        self.assertIsNone(result.area)

    def test_batch_detects_generic_coordinate_for_distinct_heights(self) -> None:
        responses = {
            AddressQuery("RUA TESTE", "100").provider_query: [candidate(number="100", place_id="same")],
            AddressQuery("RUA TESTE", "200").provider_query: [candidate(number="200", place_id="same")],
        }
        results, summary = BatchProcessor(service(MockGeocoder(responses)), progress=None).process(
            InputRows(("LOGRADOURO", "ALTURA"), (
                {"LOGRADOURO": "Rua Teste", "ALTURA": 100},
                {"LOGRADOURO": "Rua Teste", "ALTURA": 200},
                {"LOGRADOURO": "R. Teste", "ALTURA": 100},
            )),
            street_column="LOGRADOURO", number_column="ALTURA",
        )
        self.assertEqual(summary.unique_addresses, 2)
        self.assertEqual(summary.queries_avoided, 1)
        self.assertEqual(summary.generic_coordinates, 3)
        self.assertTrue(all(LookupFlag.COORDENADA_GENERICA in item.result.flags for item in results))
        self.assertTrue(all(item.result.status is LookupStatus.CONFERIR for item in results))

    def test_sqlite_cache_prevents_second_provider_call_and_report_is_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            query = AddressQuery("RUA TESTE", "100")
            delegate = MockGeocoder({query.provider_query: [candidate()]})
            cache = PersistentGeocodingCache(root / "cache.sqlite")
            cached = CachedGeocoder(delegate, cache)
            self.assertEqual(len(cached.geocode(query)), 1)
            self.assertEqual(len(cached.geocode(query)), 1)
            self.assertEqual(delegate.call_count, 1)
            self.assertEqual((cache.stats.hits, cache.stats.misses), (1, 1))
            report_path = root / "batch_report.json"
            write_batch_report(
                BatchProcessor(service(MockGeocoder()), progress=None).process(
                    InputRows(("LOGRADOURO", "ALTURA"), ()), street_column="LOGRADOURO", number_column="ALTURA"
                )[1],
                report_path,
            )
            self.assertTrue(report_path.is_file())

    def test_clear_cache_cli_does_not_require_an_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with redirect_stdout(output):
                status = batch_main(["--clear-cache", "--cache-path", str(Path(temporary) / "cache.sqlite")])
            self.assertEqual(status, 0)
            self.assertIn("Cache limpo", output.getvalue())

    def test_export_preserves_source_columns_and_new_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "input.csv"
            source_path.write_text("Processo;Logradouro;Altura\n1;Rua Teste;100\n", encoding="utf-8")
            source = InputRows(("Processo", "Logradouro", "Altura"), ({"Processo": "1", "Logradouro": "Rua Teste", "Altura": "100"},))
            lookup = service(MockGeocoder({AddressQuery("RUA TESTE", "100").provider_query: [candidate()]})).lookup("Rua Teste", 100)
            output = root / "output.xlsx"
            write_output(source_path, source, ((type("Row", (), {"source": source.rows[0], "result": lookup})()),), output)
            workbook = load_workbook(output)
            headers = [cell.value for cell in workbook.active[1]]
            self.assertEqual(headers[:3], ["Processo", "Logradouro", "Altura"])
            for expected in ("Logradouro Normalizado", "Flags", "Áreas Candidatas", "Link Google Maps", "Número Compatível"):
                self.assertIn(expected, headers)
            workbook.close()


if __name__ == "__main__":
    unittest.main()
