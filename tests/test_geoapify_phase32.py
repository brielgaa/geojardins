from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlparse

from shapely.geometry import Polygon

from geojardins.address_lookup import (
    AddressLookupService,
    GeocodingPrecision,
    LookupStatus,
    classify_geoapify_precision,
)
from geojardins.classification import GeographicClassifier, geoapify_rect_from_boundary
from geojardins.fiscais import FiscalResolver
from geojardins.geocoding.base import (
    GeocodingAuthenticationError,
    GeocodingTemporaryError,
)
from geojardins.geocoding.fallback import FallbackGeocoder
from geojardins.geocoding.geoapify import GeoapifyGeocoder, HttpResponse
from geojardins.geocoding.models import AddressQuery, GeocodeCandidate
from geojardins.georef import build_boundary_collection
from geojardins.settings import Settings


def response(status: int, payload: object, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(status, headers or {}, json.dumps(payload).encode("utf-8"))


def layers() -> tuple[dict, dict]:
    areas: dict = {"type": "FeatureCollection", "features": []}
    for index in range(6):
        polygon = Polygon(
            [
                (-46.70 + index * 0.01, -23.60),
                (-46.69 + index * 0.01, -23.60),
                (-46.69 + index * 0.01, -23.59),
                (-46.70 + index * 0.01, -23.59),
                (-46.70 + index * 0.01, -23.60),
            ]
        )
        areas["features"].append(
            {
                "type": "Feature",
                "properties": {"area": f"Tomé_{index + 1}"},
                "geometry": polygon.__geo_interface__,
            }
        )
    return areas, build_boundary_collection(areas)


def geo_result(
    street: str,
    number: str | None,
    *,
    city: str = "São Paulo",
    latitude: float = -23.595,
    longitude: float = -46.695,
    building_confidence: float = 0.75,
    confidence: float = 0.75,
    street_confidence: float = 1.0,
    result_type: str = "building",
    match_type: str = "full_match",
) -> dict[str, object]:
    result: dict[str, object] = {
        "place_id": f"{street}-{city}-{number}",
        "lat": latitude,
        "lon": longitude,
        "formatted": f"{street}, {number or ''}, {city}",
        "street": street,
        "city": city,
        "state": "São Paulo",
        "country": "Brazil",
        "country_code": "br",
        "result_type": result_type,
        "rank": {
            "confidence": confidence,
            "confidence_street_level": street_confidence,
            "confidence_building_level": building_confidence,
            "match_type": match_type,
        },
    }
    if number is not None:
        result["housenumber"] = number
    return result


def locationiq_candidate(street: str, number: str) -> GeocodeCandidate:
    return GeocodeCandidate(
        latitude=-23.595,
        longitude=-46.695,
        display_name=f"{street}, {number}, São Paulo",
        house_number=number,
        road=street,
        city="São Paulo",
        state="SP",
        country="Brasil",
        country_code="br",
        provider="locationiq",
        match_code="exact",
        match_type="point",
        match_level="building",
    )


class StubGeocoder:
    def __init__(self, response_value: list[GeocodeCandidate] | Exception) -> None:
        self.response_value = response_value
        self.request_count = 0

    def geocode(self, _query: AddressQuery) -> list[GeocodeCandidate]:
        self.request_count += 1
        if isinstance(self.response_value, Exception):
            raise self.response_value
        return list(self.response_value)


class GeoapifyProviderTests(unittest.TestCase):
    def test_structured_request_and_response_fields(self) -> None:
        urls: list[str] = []

        def fake_get(url: str, _timeout: float) -> HttpResponse:
            urls.append(url)
            return response(200, {"results": [geo_result("Rua Oscar Freire", "1250")]})

        geocoder = GeoapifyGeocoder("key-de-teste", filter_rect="-46.7,-23.6,-46.6,-23.5", http_get=fake_get)
        candidates = geocoder.geocode(AddressQuery("Rua Oscar Freire", "1250"))
        params = parse_qs(urlparse(urls[0]).query)
        self.assertEqual(params["housenumber"], ["1250"])
        self.assertEqual(params["street"], ["Rua Oscar Freire"])
        self.assertEqual(params["city"], ["São Paulo"])
        self.assertEqual(params["state"], ["São Paulo"])
        self.assertEqual(params["country"], ["Brazil"])
        self.assertEqual(params["filter"], ["rect:-46.7,-23.6,-46.6,-23.5|countrycode:br"])
        self.assertEqual(candidates[0].result_type, "building")
        self.assertEqual(candidates[0].match_type, "full_match")
        self.assertEqual(candidates[0].confidence_building_level, 0.75)
        self.assertEqual(candidates[0].provider_place_id, candidates[0].raw_id)

    def test_retry_auth_and_missing_key(self) -> None:
        replies = [response(429, {}, {"Retry-After": "2"}), response(200, {"results": []})]
        waits: list[float] = []
        geocoder = GeoapifyGeocoder("test", http_get=lambda *_: replies.pop(0), sleeper=waits.append)
        self.assertEqual(geocoder.geocode(AddressQuery("Rua A", "1")), [])
        self.assertEqual(waits, [2.0])
        with self.assertRaises(GeocodingAuthenticationError):
            GeoapifyGeocoder("test", http_get=lambda *_: response(401, {})).geocode(AddressQuery("Rua A", "1"))
        with self.assertRaisesRegex(Exception, "GEOJARDINS_GEOAPIFY_API_KEY"):
            GeoapifyGeocoder("")

    def test_500_timeout_and_invalid_payload_are_transient_errors(self) -> None:
        query = AddressQuery("Rua A", "1")
        for fake_get in (
            lambda *_: response(500, {}),
            lambda *_: HttpResponse(200, {}, b"not-json"),
            lambda *_: (_ for _ in ()).throw(GeocodingTemporaryError("timeout")),
        ):
            with self.subTest(fake_get=fake_get):
                with self.assertRaises(GeocodingTemporaryError):
                    GeoapifyGeocoder("test", max_retries=0, http_get=fake_get).geocode(query)

    def test_metric_boundary_margin_is_derived_without_hardcoded_coordinates(self) -> None:
        _areas, boundary = layers()
        rect = geoapify_rect_from_boundary(boundary, 1000)
        values = [float(value) for value in rect.split(",")]
        self.assertEqual(len(values), 4)
        self.assertLess(values[0], -46.70)
        self.assertGreater(values[2], -46.64)


class GeoapifyStrategyRegressionTests(unittest.TestCase):
    def _service(self, geo_payload: list[dict[str, object]], fallback: StubGeocoder | None = None) -> AddressLookupService:
        primary = GeoapifyGeocoder(
            "test",
            max_retries=0,
            http_get=lambda *_: response(200, {"results": geo_payload}),
        )
        areas, boundary = layers()
        classifier = GeographicClassifier(areas, boundary)
        return AddressLookupService(
            FallbackGeocoder(
                primary,
                fallback,
                geographic_validator=lambda candidate: classifier.classify(
                    candidate.latitude, candidate.longitude
                ).inside_ugr,
            ),
            classifier,
            FiscalResolver({}),
            Settings(boundary_warning_m=30),
        )

    def test_oscar_freire_prefers_geoapify_building_without_locationiq_fallback(self) -> None:
        fallback = StubGeocoder([])
        result = self._service([geo_result("Rua Oscar Freire", "1250")], fallback).lookup("Rua Oscar Freire", 1250)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.provider, "geoapify")  # type: ignore[union-attr]
        self.assertEqual(result.precision, GeocodingPrecision.ALTA)
        self.assertFalse(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(fallback.request_count, 0)

    def test_dona_julia_selects_sao_paulo_candidate_not_first_homonym(self) -> None:
        result = self._service(
            [
                geo_result("Rua Dona Julia", "14", city="Itapeva", latitude=-23.7, longitude=-46.8),
                geo_result("Rua Dona Julia", "14", city="São Paulo"),
                geo_result("Rua Dona Julia", "14", city="Peruíbe", latitude=-24.3, longitude=-47.0),
            ]
        ).lookup("Rua Dona Julia", 14)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.city, "São Paulo")  # type: ignore[union-attr]
        self.assertEqual(result.candidate.original_rank, 2)  # type: ignore[union-attr]

    def test_openaddresses_administrative_context_reaches_final_selection(self) -> None:
        candidate = geo_result("Rua Lourenco de Almeida", "743")
        candidate["state"] = "Southeast"
        candidate["county_code"] = "SP"
        fallback = StubGeocoder([])
        result = self._service([candidate], fallback).lookup("Rua Lourenco de Almeida", 743)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.provider, "geoapify")  # type: ignore[union-attr]
        self.assertEqual(result.geocoding_diagnostics.geoapify_usable_candidate_count, 1)  # type: ignore[union-attr]
        self.assertFalse(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(fallback.request_count, 0)

    def test_usable_count_and_final_compatibility_cannot_disagree(self) -> None:
        candidate = geo_result("Rua Mexico", "425")
        candidate["state"] = "Southeast"
        candidate["county_code"] = "SP"
        result = self._service([candidate], StubGeocoder([])).lookup("Rua Mexico", 425)
        usable_assessments = sum(
            assessment.compatibility.usable
            for assessment in result.assessments
            if assessment.candidate.provider == "geoapify"
        )
        self.assertEqual(
            result.geocoding_diagnostics.geoapify_usable_candidate_count,  # type: ignore[union-attr]
            usable_assessments,
        )
        self.assertNotEqual(result.status, LookupStatus.ENDERECO_NAO_LOCALIZADO)

    def test_palestra_italia_structured_exact_address_can_promote_with_zero_building_confidence(self) -> None:
        result = self._service(
            [geo_result("Rua Palestra Itália", "14", building_confidence=0, confidence=0.75)]
        ).lookup("Rua Palestra Italia", 14)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.precision, GeocodingPrecision.BAIXA)
        # Baixa evidência de building agora solicita fallback; este cenário não
        # configurou token/provider secundário, então a intenção fica explícita.
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_needed)  # type: ignore[union-attr]
        self.assertFalse(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(result.geocoding_diagnostics.geoapify_usable_candidate_count, 0)  # type: ignore[union-attr]

    def test_empty_or_temporary_geoapify_uses_locationiq_fallback(self) -> None:
        fallback_candidate = GeocodeCandidate(
            latitude=-23.595,
            longitude=-46.695,
            display_name="Rua Exemplo, 10, São Paulo",
            house_number="10",
            road="Rua Exemplo",
            city="São Paulo",
            state="SP",
            country="Brasil",
            country_code="br",
            provider="locationiq",
            original_rank=1,
            match_code="exact",
            match_type="point",
            match_level="building",
        )
        fallback = StubGeocoder([fallback_candidate])
        result = self._service([], fallback).lookup("Rua Exemplo", 10)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.provider, "locationiq")  # type: ignore[union-attr]
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(
            result.geocoding_diagnostics.locationiq_fallback_reason,  # type: ignore[union-attr]
            "nenhum candidato Geoapify retornado",
        )
        self.assertEqual(fallback.request_count, 1)

    def test_temporary_geoapify_failure_uses_locationiq_fallback(self) -> None:
        fallback_candidate = GeocodeCandidate(
            latitude=-23.595,
            longitude=-46.695,
            display_name="Rua Exemplo, 10, São Paulo",
            house_number="10",
            road="Rua Exemplo",
            city="São Paulo",
            state="SP",
            country="Brasil",
            country_code="br",
            provider="locationiq",
            match_code="exact",
            match_type="point",
            match_level="building",
        )
        primary = GeoapifyGeocoder("test", max_retries=0, http_get=lambda *_: response(500, {}))
        fallback = StubGeocoder([fallback_candidate])
        areas, boundary = layers()
        service = AddressLookupService(
            FallbackGeocoder(primary, fallback),
            GeographicClassifier(areas, boundary),
            FiscalResolver({}),
            Settings(),
        )
        result = service.lookup("Rua Exemplo", 10)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]

    def test_incompatible_primary_inside_ugr_never_becomes_confirmed(self) -> None:
        fallback = StubGeocoder([locationiq_candidate("Rua Oscar Freire", "1250")])
        result = self._service([geo_result("Rua Incompatível", "1250")], fallback).lookup(
            "Rua Oscar Freire", 1250
        )
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.provider, "locationiq")  # type: ignore[union-attr]
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(result.geocoding_diagnostics.geoapify_usable_candidate_count, 0)  # type: ignore[union-attr]
        self.assertEqual(
            result.geocoding_diagnostics.locationiq_fallback_reason,  # type: ignore[union-attr]
            "nenhum candidato Geoapify utilizável",
        )

    def test_incompatible_primary_and_empty_locationiq_remain_not_found(self) -> None:
        result = self._service([geo_result("Rua Incompatível", "1250")], StubGeocoder([])).lookup(
            "Rua Oscar Freire", 1250
        )
        self.assertEqual(result.status, LookupStatus.ENDERECO_NAO_LOCALIZADO)
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(result.geocoding_diagnostics.geoapify_usable_candidate_count, 0)  # type: ignore[union-attr]

    def test_homonym_outside_ugr_triggers_fallback_and_does_not_win(self) -> None:
        fallback = StubGeocoder([locationiq_candidate("Rua Mexico", "425")])
        result = self._service(
            [geo_result("Rua Mexico", "425", latitude=-23.4, longitude=-46.4)],
            fallback,
        ).lookup("Rua Mexico", 425)
        self.assertEqual(result.status, LookupStatus.CONFERIR)
        self.assertEqual(result.candidate.provider, "locationiq")  # type: ignore[union-attr]
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]

    def test_invalid_coordinates_do_not_block_fallback(self) -> None:
        fallback = StubGeocoder([locationiq_candidate("Rua Lourenco de Almeida", "743")])
        invalid = geo_result("Rua Lourenco de Almeida", "743")
        invalid["lat"] = "nan"
        result = self._service([invalid], fallback).lookup("Rua Lourenco de Almeida", 743)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.provider, "locationiq")  # type: ignore[union-attr]
        self.assertTrue(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]

    def test_needed_fallback_without_locationiq_is_explicit(self) -> None:
        result = self._service([geo_result("Rua Incompatível", "425")]).lookup("Rua Mexico", 425)
        self.assertEqual(result.status, LookupStatus.ENDERECO_NAO_LOCALIZADO)
        diagnostics = result.geocoding_diagnostics
        self.assertTrue(diagnostics.locationiq_fallback_needed)  # type: ignore[union-attr]
        self.assertFalse(diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(diagnostics.geoapify_usable_candidate_count, 0)  # type: ignore[union-attr]

    def test_high_confidence_amenity_haddock_lobo_is_confirmed_in_tome_4(self) -> None:
        fallback = StubGeocoder([])
        result = self._service(
            [
                geo_result(
                    "Rua Haddock Lobo",
                    "1307",
                    latitude=-23.5962,
                    longitude=-46.665,
                    building_confidence=1.0,
                    confidence=1.0,
                    street_confidence=1.0,
                    result_type="amenity",
                    match_type="inner_part",
                )
            ],
            fallback,
        ).lookup("Rua Haddock Lobo", 1307)
        self.assertEqual(result.precision, GeocodingPrecision.ALTA)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.area, "Tomé_4")
        self.assertFalse(result.geocoding_diagnostics.locationiq_fallback_used)  # type: ignore[union-attr]
        self.assertEqual(fallback.request_count, 0)

    def test_high_confidence_amenity_sena_madureira_remains_near_boundary(self) -> None:
        result = self._service(
            [
                geo_result(
                    "Rua Sena Madureira",
                    "1500",
                    latitude=-23.595,
                    longitude=-46.69983,
                    building_confidence=1.0,
                    confidence=1.0,
                    street_confidence=1.0,
                    result_type="amenity",
                    match_type="inner_part",
                )
            ]
        ).lookup("Rua Sena Madureira", 1500)
        self.assertEqual(result.precision, GeocodingPrecision.ALTA)
        self.assertEqual(result.status, LookupStatus.PROXIMO_DA_DIVISA)
        self.assertEqual(result.area, "Tomé_1")

    def test_amenity_requires_all_high_confidence_evidence(self) -> None:
        def amenity_candidate(**changes: object) -> GeocodeCandidate:
            values: dict[str, object] = {
                "latitude": -23.595,
                "longitude": -46.695,
                "display_name": "Amenity, 10",
                "house_number": "10",
                "road": "Rua Exemplo",
                "provider": "geoapify",
                "result_type": "amenity",
                "match_type": "inner_part",
                "confidence": 1.0,
                "confidence_street_level": 1.0,
                "confidence_building_level": 1.0,
            }
            values.update(changes)
            return GeocodeCandidate(**values)  # type: ignore[arg-type]

        cases = {
            "wrong_number": (amenity_candidate(), False, 1.0, True, True, True),
            "missing_number": (amenity_candidate(house_number=None), False, 1.0, True, True, True),
            "no_building": (amenity_candidate(confidence_building_level=0.0), True, 1.0, True, True, True),
            "partial_building": (amenity_candidate(confidence_building_level=0.5), True, 1.0, True, True, True),
            "low_street": (amenity_candidate(confidence_street_level=0.5), True, 1.0, True, True, True),
            "low_overall": (amenity_candidate(confidence=0.5), True, 1.0, True, True, True),
            "wrong_road": (amenity_candidate(), True, 0.5, True, True, True),
        }
        for name, (candidate, matches, similarity, country, city, inside) in cases.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    classify_geoapify_precision(
                        candidate,
                        matches,
                        road_similarity=similarity,
                        country_compatible=country,
                        city_compatible=city,
                        inside_ugr=inside,
                    ),
                    GeocodingPrecision.ALTA,
                )

    def test_high_confidence_amenity_outside_ugr_is_not_territorially_confirmed(self) -> None:
        result = self._service(
            [
                geo_result(
                    "Rua Exemplo",
                    "10",
                    latitude=-23.5,
                    longitude=-46.5,
                    building_confidence=1.0,
                    confidence=1.0,
                    street_confidence=1.0,
                    result_type="amenity",
                    match_type="inner_part",
                )
            ]
        ).lookup("Rua Exemplo", 10)
        self.assertEqual(result.precision, GeocodingPrecision.BAIXA)
        self.assertEqual(result.status, LookupStatus.FORA_DA_UGR)

    def test_structured_address_evidence_requires_general_and_street_confidence(self) -> None:
        for street, number, confidence, expected_status in (
            ("Rua Potiguar Medeiros", "123", 0.75, LookupStatus.CONFIRMADO),
            ("Rua Mofarrej", "1297", 0.25, LookupStatus.CONFERIR),
        ):
            with self.subTest(street=street):
                result = self._service(
                    [geo_result(street, number, building_confidence=0.0, confidence=confidence)]
                ).lookup(street, number)
                self.assertEqual(result.precision, GeocodingPrecision.BAIXA)
                self.assertEqual(result.status, expected_status)

    def test_geoapify_precision_thresholds_are_explicit(self) -> None:
        def candidate_with(confidence: float) -> GeocodeCandidate:
            return GeocodeCandidate(
                latitude=0,
                longitude=0,
                display_name="",
                house_number="1",
                road="Rua A",
                provider="geoapify",
                result_type="building",
                match_type="full_match",
                confidence=confidence,
                confidence_street_level=1,
                confidence_building_level=confidence,
            )

        self.assertEqual(classify_geoapify_precision(candidate_with(0.75), True), GeocodingPrecision.ALTA)
        self.assertEqual(classify_geoapify_precision(candidate_with(0.25), True), GeocodingPrecision.MEDIA)
        self.assertEqual(classify_geoapify_precision(candidate_with(0), True), GeocodingPrecision.BAIXA)
