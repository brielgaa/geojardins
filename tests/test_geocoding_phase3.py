from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlparse

from geojardins.geocoding.base import (
    GeocodingAuthenticationError,
    GeocodingRequestError,
    GeocodingTemporaryError,
    RateLimiter,
)
from geojardins.geocoding.locationiq import HttpResponse, LocationIQGeocoder
from geojardins.geocoding.models import AddressQuery


def response(status: int, body: object = None, headers: dict[str, str] | None = None) -> HttpResponse:
    encoded = json.dumps(body if body is not None else []).encode("utf-8")
    return HttpResponse(status, headers or {}, encoded)


class LocationIQGeocoderTests(unittest.TestCase):
    def test_request_has_official_parameters_and_parses_candidates(self) -> None:
        captured: list[str] = []

        def fake_get(url: str, timeout: float) -> HttpResponse:
            captured.append(url)
            self.assertEqual(timeout, 7)
            return response(
                200,
                [
                    {
                        "place_id": "123",
                        "lat": "-23.5601",
                        "lon": "-46.6702",
                        "display_name": "Rua Exemplo, 1250, São Paulo",
                        "importance": 0.8,
                        "address": {
                            "house_number": "1250",
                            "road": "Rua Exemplo",
                            "neighbourhood": "Jardins",
                            "suburb": "Jardim Paulista",
                            "city": "São Paulo",
                            "state": "São Paulo",
                            "postcode": "01400-000",
                            "country": "Brasil",
                            "country_code": "br",
                        },
                        "matchquality": {
                            "matchcode": "exact",
                            "matchtype": "point",
                            "matchlevel": "building",
                        },
                    },
                    {
                        "osm_id": "987",
                        "lat": "-23.5602",
                        "lon": "-46.6703",
                        "display_name": "Rua Exemplo, São Paulo",
                        "address": {"road": "Rua Exemplo", "country_code": "br"},
                        "matchquality": {
                            "matchcode": "fallback",
                            "matchtype": "interpolated",
                            "matchlevel": "street",
                        },
                    },
                    {
                        "lat": "-23.5603",
                        "lon": "-46.6704",
                        "display_name": "Bairro Exemplo, São Paulo",
                        "address": {"country_code": "br"},
                        "matchquality": {
                            "matchcode": "approximate",
                            "matchtype": "centroid",
                            "matchlevel": "neighbourhood",
                        },
                    },
                ],
            )

        geocoder = LocationIQGeocoder(
            "token-de-teste",
            timeout_s=7,
            result_limit=8,
            viewbox="-46.8,-23.7,-46.5,-23.4",
            http_get=fake_get,
        )
        candidates = geocoder.geocode(AddressQuery("Rua Exemplo", "1250"))
        structured_url = urlparse(captured[0])
        self.assertTrue(structured_url.path.endswith("/search/structured"))
        params = parse_qs(structured_url.query)
        self.assertEqual(params["street"], ["1250 Rua Exemplo"])
        self.assertEqual(params["city"], ["São Paulo"])
        self.assertEqual(params["state"], ["São Paulo"])
        self.assertEqual(params["country"], ["Brasil"])
        self.assertNotIn("q", params)
        for name, expected in {
            "format": "json",
            "addressdetails": "1",
            "normalizeaddress": "1",
            "normalizecity": "1",
            "matchquality": "1",
            "countrycodes": "br",
            "accept-language": "pt-BR",
            "limit": "8",
        }.items():
            self.assertEqual(params[name], [expected])
        self.assertEqual(params["viewbox"], ["-46.8,-23.7,-46.5,-23.4"])
        self.assertNotIn("bounded", params)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0].house_number, "1250")
        self.assertEqual(candidates[0].match_code, "exact")
        self.assertEqual(candidates[1].match_type, "interpolated")
        self.assertEqual(candidates[2].match_level, "neighbourhood")
        self.assertEqual(candidates[1].raw_id, "987")
        self.assertEqual(len(captured), 1)
        self.assertEqual(geocoder.last_diagnostics.mode_label, "STRUCTURED")
        self.assertEqual(geocoder.last_diagnostics.structured_requests, 1)
        self.assertEqual(geocoder.last_diagnostics.freeform_requests, 0)

    def test_weak_structured_result_uses_freeform_and_deduplicates(self) -> None:
        calls: list[str] = []

        def candidate_payload(*, house_number: str | None, level: str, kind: str) -> dict[str, object]:
            address: dict[str, str] = {
                "road": "Rua Dona Júlia",
                "city": "São Paulo",
                "state": "SP",
                "country": "Brasil",
                "country_code": "br",
            }
            if house_number is not None:
                address["house_number"] = house_number
            return {
                "place_id": "same-place",
                "lat": "-23.595",
                "lon": "-46.695",
                "display_name": "Rua Dona Júlia, São Paulo",
                "address": address,
                "matchquality": {"matchcode": "exact", "matchtype": kind, "matchlevel": level},
            }

        def fake_get(url: str, _timeout: float) -> HttpResponse:
            calls.append(url)
            if urlparse(url).path.endswith("/structured"):
                return response(200, [candidate_payload(house_number=None, level="street", kind="centroid")])
            return response(200, [candidate_payload(house_number="14", level="building", kind="point")])

        geocoder = LocationIQGeocoder("test", http_get=fake_get)
        candidates = geocoder.geocode(AddressQuery("Rua Dona Júlia", "14"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].house_number, "14")
        self.assertEqual(candidates[0].query_mode, "structured+freeform")
        self.assertEqual(geocoder.last_diagnostics.mode_label, "STRUCTURED + FREEFORM FALLBACK")
        self.assertEqual(geocoder.last_diagnostics.structured_candidate_count, 1)
        self.assertEqual(geocoder.last_diagnostics.freeform_candidate_count, 1)
        self.assertEqual(geocoder.request_count, 2)
        fallback_params = parse_qs(urlparse(calls[1]).query)
        self.assertEqual(fallback_params["q"], ["Rua Dona Júlia, 14, São Paulo - SP, Brasil"])

    def test_real_case_shaped_http_mocks_exercise_both_query_modes(self) -> None:
        def item(street: str, house_number: str | None, *, kind: str, level: str) -> dict[str, object]:
            address: dict[str, str] = {
                "road": street,
                "city": "São Paulo",
                "state": "SP",
                "country": "Brasil",
                "country_code": "br",
            }
            if house_number is not None:
                address["house_number"] = house_number
            return {
                "lat": "-23.595",
                "lon": "-46.695",
                "display_name": f"{street}, São Paulo",
                "address": address,
                "matchquality": {"matchcode": "exact", "matchtype": kind, "matchlevel": level},
            }

        def fake_get(url: str, _timeout: float) -> HttpResponse:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            structured = parsed.path.endswith("/structured")
            requested = params["street"][0] if structured else params["q"][0]
            if "Oscar Freire" in requested:
                return response(200, [item("Rua Oscar Freire", "1250", kind="point", level="building")])
            if "Dona Júlia" in requested:
                return response(200, [item("Rua Dona Júlia", None, kind="centroid", level="street")])
            if "Palestra Itália" in requested:
                return response(
                    200,
                    [
                        item(
                            "Rua Palestra Itália",
                            None if structured else "14",
                            kind="centroid" if structured else "point",
                            level="street" if structured else "building",
                        )
                    ],
                )
            self.fail(f"Consulta HTTP mockada inesperada: {requested}")

        cases = (
            ("Rua Oscar Freire", "1250", "STRUCTURED", "1250"),
            ("Rua Dona Júlia", "14", "STRUCTURED + FREEFORM FALLBACK", None),
            ("Rua Palestra Itália", "14", "STRUCTURED + FREEFORM FALLBACK", "14"),
        )
        for street, number, mode, expected_house_number in cases:
            with self.subTest(street=street):
                geocoder = LocationIQGeocoder("test", http_get=fake_get)
                candidates = geocoder.geocode(AddressQuery(street, number))
                self.assertEqual(geocoder.last_diagnostics.mode_label, mode)
                self.assertEqual(candidates[0].house_number, expected_house_number)

    def test_bounded_is_only_sent_when_explicitly_enabled(self) -> None:
        geocoder = LocationIQGeocoder("test", viewbox="1,2,3,4", bounded=True, http_get=lambda *_: response(404))
        params = parse_qs(urlparse(geocoder._request_url(AddressQuery("Rua A", "1"))).query)
        self.assertEqual(params["bounded"], ["1"])

    def test_empty_and_non_retryable_http_errors(self) -> None:
        query = AddressQuery("Rua A", "1")
        self.assertEqual(LocationIQGeocoder("test", http_get=lambda *_: response(404)).geocode(query), [])
        with self.assertRaises(GeocodingRequestError):
            LocationIQGeocoder("test", http_get=lambda *_: response(400)).geocode(query)
        for status in (401, 403):
            geocoder = LocationIQGeocoder("test", max_retries=3, http_get=lambda *_args, code=status: response(code))
            with self.assertRaises(GeocodingAuthenticationError):
                geocoder.geocode(query)
            self.assertEqual(geocoder.request_count, 1)

    def test_transient_errors_retry_with_retry_after(self) -> None:
        responses = [
            response(429, headers={"Retry-After": "3"}),
            response(200, []),
            response(200, []),
        ]
        delays: list[float] = []
        geocoder = LocationIQGeocoder(
            "test",
            max_retries=2,
            retry_base_s=1,
            sleeper=delays.append,
            http_get=lambda *_: responses.pop(0),
        )
        self.assertEqual(geocoder.geocode(AddressQuery("Rua A", "1")), [])
        self.assertEqual(geocoder.request_count, 3)
        self.assertEqual(delays, [3.0])
        for failed_response in (response(500), HttpResponse(200, {}, b"not json")):
            with self.subTest(failed_response=failed_response):
                with self.assertRaises(GeocodingTemporaryError):
                    LocationIQGeocoder("test", max_retries=0, http_get=lambda *_: failed_response).geocode(
                        AddressQuery("Rua A", "1")
                    )

    def test_timeout_and_missing_token_are_friendly(self) -> None:
        def timeout(*_args: object) -> HttpResponse:
            raise GeocodingTemporaryError("Falha transitória de rede ao consultar LocationIQ.")

        with self.assertRaises(GeocodingTemporaryError):
            LocationIQGeocoder("test", max_retries=0, http_get=timeout).geocode(AddressQuery("Rua A", "1"))
        with self.assertRaisesRegex(Exception, "GEOJARDINS_LOCATIONIQ_TOKEN"):
            LocationIQGeocoder("")


class RateLimiterTests(unittest.TestCase):
    def test_limiter_spaces_request_starts(self) -> None:
        state = {"time": 0.0}
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            state["time"] += seconds

        limiter = RateLimiter(2, clock=lambda: state["time"], sleeper=sleep)
        limiter.acquire()
        limiter.acquire()
        limiter.acquire()
        self.assertEqual(waits, [0.5, 0.5])
