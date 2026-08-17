"""Provider Geoapify Forward Geocoding, isolado do domínio GeoJardins."""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import (
    GeocodingAuthenticationError,
    GeocodingConfigurationError,
    GeocodingRequestError,
    GeocodingTemporaryError,
    RateLimiter,
)
from .models import AddressQuery, GeocodeCandidate, GeocodingDiagnostics

LOGGER = logging.getLogger(__name__)
SEARCH_ENDPOINT = "https://api.geoapify.com/v1/geocode/search"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


HttpGet = Callable[[str, float], HttpResponse]


def _stdlib_http_get(url: str, timeout_s: float) -> HttpResponse:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "GeoJardins/0.3"})
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - endpoint constante
            return HttpResponse(
                status_code=int(response.status),
                headers={key: value for key, value in response.headers.items()},
                body=response.read(),
            )
    except HTTPError as exc:
        return HttpResponse(
            status_code=int(exc.code),
            headers={key: value for key, value in exc.headers.items()} if exc.headers else {},
            body=exc.read(),
        )
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise GeocodingTemporaryError("Falha transitória de rede ao consultar Geoapify.") from exc


class GeoapifyGeocoder:
    """Cliente estruturado da Geoapify, com retries limitados e sem logar chave."""

    provider_name = "geoapify"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = 10.0,
        max_retries: int = 3,
        retry_base_s: float = 1.0,
        result_limit: int = 10,
        filter_rect: str | None = None,
        rate_limiter: RateLimiter | None = None,
        http_get: HttpGet = _stdlib_http_get,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not api_key.strip():
            raise GeocodingConfigurationError(
                "GEOJARDINS_GEOAPIFY_API_KEY não definida. "
                "No PowerShell: $env:GEOJARDINS_GEOAPIFY_API_KEY=\"SUA_CHAVE\""
            )
        if timeout_s <= 0 or max_retries < 0 or not 1 <= result_limit <= 50:
            raise ValueError("Configuração Geoapify inválida.")
        self._api_key = api_key.strip()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.result_limit = result_limit
        self.filter_rect = filter_rect
        self.rate_limiter = rate_limiter
        self._http_get = http_get
        self._sleeper = sleeper
        self.request_count = 0
        self.last_diagnostics = GeocodingDiagnostics()

    @classmethod
    def from_environment(cls, **kwargs: object) -> "GeoapifyGeocoder":
        return cls(os.environ.get("GEOJARDINS_GEOAPIFY_API_KEY", ""), **kwargs)

    def _request_url(self, query: AddressQuery) -> str:
        params: dict[str, str | int] = {
            "apiKey": self._api_key,
            "city": query.city,
            "state": "São Paulo",
            "country": "Brazil",
            "lang": "pt",
            "limit": self.result_limit,
            "format": "json",
        }
        if query.is_intersection:
            # A Geoapify aceita texto livre via `text`; número não participa de
            # uma consulta de cruzamento.
            params["text"] = f"{query.street_1 or query.street} x {query.street_2 or ''}, {query.city}, {query.state}, {query.country}"
        else:
            params["housenumber"] = query.house_number or ""
            params["street"] = query.street
        filters = ["countrycode:br"]
        if self.filter_rect:
            filters.insert(0, f"rect:{self.filter_rect}")
        params["filter"] = "|".join(filters)
        return SEARCH_ENDPOINT + "?" + urlencode(params)

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        value = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
        try:
            return max(0.0, float(value)) if value is not None else None
        except ValueError:
            return None

    @staticmethod
    def _text(value: object) -> str | None:
        result = str(value).strip() if value is not None else ""
        return result or None

    @staticmethod
    def _number(value: object) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _perform_request(self, query: AddressQuery) -> list[dict[str, object]]:
        if self.rate_limiter:
            self.rate_limiter.acquire()
        self.request_count += 1
        response = self._http_get(self._request_url(query), self.timeout_s)
        if response.status_code == 400:
            raise GeocodingRequestError("Geoapify rejeitou a consulta de endereço.")
        if response.status_code in {401, 403}:
            raise GeocodingAuthenticationError("Geoapify recusou as credenciais configuradas.")
        if response.status_code == 429:
            raise GeocodingTemporaryError(
                "Geoapify atingiu o limite de requisições.",
                retry_after_s=self._retry_after(response.headers),
            )
        if 500 <= response.status_code <= 599:
            raise GeocodingTemporaryError(f"Geoapify apresentou erro temporário HTTP {response.status_code}.")
        if response.status_code != 200:
            raise GeocodingRequestError(f"Geoapify retornou HTTP {response.status_code} para a consulta.")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GeocodingTemporaryError("Geoapify retornou uma resposta JSON inválida.") from exc
        results = payload.get("results") if isinstance(payload, Mapping) else None
        if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
            raise GeocodingTemporaryError("Geoapify retornou um formato de resposta inesperado.")
        return results

    def _parse_candidate(self, item: Mapping[str, object], rank: int) -> GeocodeCandidate:
        latitude = self._number(item.get("lat"))
        longitude = self._number(item.get("lon"))
        if latitude is None or longitude is None or not math.isfinite(latitude) or not math.isfinite(longitude):
            raise GeocodingTemporaryError("Geoapify retornou candidato sem latitude/longitude válidas.")
        quality = item.get("rank")
        quality = quality if isinstance(quality, Mapping) else {}
        place_id = self._text(item.get("place_id"))
        return GeocodeCandidate(
            latitude=latitude,
            longitude=longitude,
            display_name=self._text(item.get("formatted")) or "",
            house_number=self._text(item.get("housenumber")),
            road=self._text(item.get("street")),
            neighbourhood=self._text(item.get("district")),
            suburb=self._text(item.get("suburb")),
            city=self._text(item.get("city")),
            state=self._text(item.get("state") or item.get("state_code")),
            postcode=self._text(item.get("postcode")),
            country=self._text(item.get("country")),
            country_code=self._text(item.get("country_code")),
            state_code=self._text(item.get("state_code")),
            county_code=self._text(item.get("county_code")),
            provider=self.provider_name,
            original_rank=rank,
            match_type=self._text(quality.get("match_type")),
            importance=self._number(quality.get("importance")),
            raw_id=place_id,
            provider_place_id=place_id,
            result_type=self._text(item.get("result_type")),
            confidence=self._number(quality.get("confidence")),
            confidence_street_level=self._number(quality.get("confidence_street_level")),
            confidence_building_level=self._number(quality.get("confidence_building_level")),
            query_mode="structured",
        )

    def geocode(self, query: AddressQuery) -> list[GeocodeCandidate]:
        before = self.request_count
        for attempt in range(self.max_retries + 1):
            try:
                candidates = [
                    self._parse_candidate(item, rank)
                    for rank, item in enumerate(self._perform_request(query), start=1)
                ]
                self.last_diagnostics = GeocodingDiagnostics(
                    geoapify_used=True,
                    geoapify_candidate_count=len(candidates),
                    geoapify_requests=self.request_count - before,
                )
                LOGGER.info("Geoapify retornou %s candidato(s).", len(candidates))
                return candidates
            except GeocodingTemporaryError as exc:
                if attempt >= self.max_retries:
                    raise
                delay = max(self.retry_base_s * (2**attempt), exc.retry_after_s or 0.0)
                LOGGER.warning("Falha transitória Geoapify; nova tentativa em %.1f s.", delay)
                self._sleeper(delay)
        raise AssertionError("Fluxo de retry Geoapify inesperado.")
