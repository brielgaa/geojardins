"""Provider LocationIQ Search / Forward Geocoding."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
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
from ..address_normalizer import (
    AddressValidationError,
    normalize_house_number,
    normalize_text,
    street_similarity,
    street_tokens_compatible,
)

LOGGER = logging.getLogger(__name__)
SEARCH_ENDPOINT = "https://us1.locationiq.com/v1/search"
STRUCTURED_SEARCH_ENDPOINT = "https://us1.locationiq.com/v1/search/structured"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


HttpGet = Callable[[str, float], HttpResponse]


def _stdlib_http_get(url: str, timeout_s: float) -> HttpResponse:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "GeoJardins/0.3"})
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - endpoint is fixed above
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
        raise GeocodingTemporaryError("Falha transitória de rede ao consultar LocationIQ.") from exc


class LocationIQGeocoder:
    """Cliente do endpoint oficial Search, com retry limitado e sem logar token."""

    provider_name = "locationiq"

    def __init__(
        self,
        token: str,
        *,
        timeout_s: float = 10.0,
        max_retries: int = 3,
        retry_base_s: float = 1.0,
        result_limit: int = 10,
        viewbox: str | None = None,
        bounded: bool = False,
        rate_limiter: RateLimiter | None = None,
        http_get: HttpGet = _stdlib_http_get,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token or not token.strip():
            raise GeocodingConfigurationError(
                "GEOJARDINS_LOCATIONIQ_TOKEN não definido. "
                "No PowerShell: $env:GEOJARDINS_LOCATIONIQ_TOKEN=\"SEU_TOKEN\""
            )
        if not 1 <= result_limit <= 50:
            raise ValueError("locationiq_result_limit deve estar entre 1 e 50.")
        if max_retries < 0:
            raise ValueError("geocoding_max_retries não pode ser negativo.")
        self._token = token.strip()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.result_limit = result_limit
        self.viewbox = viewbox
        self.bounded = bounded
        self.rate_limiter = rate_limiter
        self._http_get = http_get
        self._sleeper = sleeper
        self.request_count = 0
        self.structured_request_count = 0
        self.freeform_request_count = 0
        self.last_diagnostics = GeocodingDiagnostics()

    @classmethod
    def from_environment(cls, **kwargs: object) -> "LocationIQGeocoder":
        return cls(os.environ.get("GEOJARDINS_LOCATIONIQ_TOKEN", ""), **kwargs)

    def _request_url(self, query: AddressQuery, mode: str = "freeform") -> str:
        params: dict[str, str | int] = {
            "key": self._token,
            "format": "json",
            "addressdetails": 1,
            "normalizeaddress": 1,
            "normalizecity": 1,
            "matchquality": 1,
            "countrycodes": "br",
            "accept-language": "pt-BR",
            "limit": self.result_limit,
        }
        if mode == "structured":
            # A documentação do endpoint structured define street como
            # "building number, name and road"; não existe parâmetro separado
            # house_number para esta busca.
            if query.is_intersection:
                # O endpoint estruturado não possui campos para duas vias.
                # Usa-se o endpoint livre com a consulta de cruzamento íntegra.
                params["q"] = query.provider_query
                endpoint = SEARCH_ENDPOINT
            else:
                params.update(
                    {
                        "street": f"{query.house_number or ''} {query.street}".strip(),
                        "city": query.city,
                        "state": "São Paulo",
                        "country": query.country,
                    }
                )
                endpoint = STRUCTURED_SEARCH_ENDPOINT
        elif mode == "freeform":
            params["q"] = query.provider_query
            endpoint = SEARCH_ENDPOINT
        else:
            raise ValueError(f"Modo de geocodificação desconhecido: {mode}")
        if self.viewbox:
            params["viewbox"] = self.viewbox
            # bounded=0 é deliberado: a caixa favorece a UGR, mas não elimina um
            # endereço limítrofe cuja posição do provider tenha pequeno desvio.
            if self.bounded:
                params["bounded"] = 1
        return endpoint + "?" + urlencode(params)

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        value = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    def _perform_request(self, query: AddressQuery, mode: str) -> list[dict[str, object]]:
        url = self._request_url(query, mode)
        if self.rate_limiter:
            self.rate_limiter.acquire()
        self.request_count += 1
        if mode == "structured":
            self.structured_request_count += 1
        else:
            self.freeform_request_count += 1
        response = self._http_get(url, self.timeout_s)
        if response.status_code == 404:
            return []
        if response.status_code == 400:
            raise GeocodingRequestError("LocationIQ rejeitou a consulta de endereço.")
        if response.status_code in {401, 403}:
            raise GeocodingAuthenticationError("LocationIQ recusou as credenciais configuradas.")
        if response.status_code == 429:
            raise GeocodingTemporaryError(
                "LocationIQ atingiu o limite de requisições.",
                retry_after_s=self._retry_after(response.headers),
            )
        if 500 <= response.status_code <= 599:
            raise GeocodingTemporaryError(
                f"LocationIQ apresentou erro temporário HTTP {response.status_code}."
            )
        if response.status_code != 200:
            raise GeocodingRequestError(
                f"LocationIQ retornou HTTP {response.status_code} para a consulta."
            )
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GeocodingTemporaryError("LocationIQ retornou uma resposta JSON inválida.") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise GeocodingTemporaryError("LocationIQ retornou um formato de resposta inesperado.")
        return payload

    @staticmethod
    def _optional_text(value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    def _parse_candidate(
        self,
        item: Mapping[str, object],
        rank: int,
        mode: str,
    ) -> GeocodeCandidate:
        try:
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingTemporaryError("LocationIQ retornou candidato sem latitude/longitude válidas.") from exc
        address = item.get("address")
        address = address if isinstance(address, Mapping) else {}
        quality = item.get("matchquality")
        quality = quality if isinstance(quality, Mapping) else {}
        importance: float | None
        try:
            importance = float(item["importance"]) if item.get("importance") is not None else None
        except (TypeError, ValueError):
            importance = None
        raw_id = item.get("place_id") or item.get("osm_id")
        return GeocodeCandidate(
            latitude=latitude,
            longitude=longitude,
            display_name=self._optional_text(item.get("display_name")) or "",
            house_number=self._optional_text(address.get("house_number")),
            road=self._optional_text(address.get("road")),
            neighbourhood=self._optional_text(address.get("neighbourhood")),
            suburb=self._optional_text(address.get("suburb")),
            city=self._optional_text(address.get("city")),
            state=self._optional_text(address.get("state") or address.get("state_code")),
            postcode=self._optional_text(address.get("postcode")),
            country=self._optional_text(address.get("country")),
            country_code=self._optional_text(address.get("country_code")),
            state_code=self._optional_text(address.get("state_code")),
            county_code=self._optional_text(address.get("county_code")),
            provider=self.provider_name,
            original_rank=rank,
            match_code=self._optional_text(quality.get("matchcode")),
            match_type=self._optional_text(quality.get("matchtype")),
            match_level=self._optional_text(quality.get("matchlevel")),
            importance=importance,
            raw_id=self._optional_text(raw_id),
            provider_place_id=self._optional_text(raw_id),
            query_mode=mode,
        )

    def _request_candidates(self, query: AddressQuery, mode: str) -> list[GeocodeCandidate]:
        for attempt in range(self.max_retries + 1):
            try:
                payload = self._perform_request(query, mode)
                LOGGER.info("LocationIQ %s retornou %s candidato(s).", mode, len(payload))
                return [
                    self._parse_candidate(item, rank, mode)
                    for rank, item in enumerate(payload, start=1)
                ]
            except GeocodingTemporaryError as exc:
                if attempt >= self.max_retries:
                    raise
                backoff = self.retry_base_s * (2**attempt)
                delay = max(backoff, exc.retry_after_s or 0.0)
                LOGGER.warning("Falha transitória de geocodificação; nova tentativa em %.1f s.", delay)
                self._sleeper(delay)
        raise AssertionError("Fluxo de retry inesperado.")

    @staticmethod
    def _returned_number(candidate: GeocodeCandidate) -> str | None:
        if candidate.house_number is None:
            return None
        try:
            return normalize_house_number(candidate.house_number)
        except AddressValidationError:
            return None

    @classmethod
    def _has_strong_structured_match(
        cls,
        query: AddressQuery,
        candidate: GeocodeCandidate,
    ) -> bool:
        """Decide se há evidência suficiente para evitar o fallback pago.

        A condição combina número, via, país, match quality e granularidade;
        centroid de rua sem número nunca é suficiente.
        """

        if query.is_intersection:
            return street_similarity(query.street_1 or query.street, candidate.road) >= 0.5
        if cls._returned_number(candidate) != query.house_number:
            return False
        evidence = 4  # house_number explicitamente compatível
        if street_tokens_compatible(query.street, candidate.road):
            evidence += 2
        if normalize_text(candidate.country_code) == "br":
            evidence += 1
        if normalize_text(candidate.match_code) in {"exact", "fallback"}:
            evidence += 1
        if normalize_text(candidate.match_type) in {"point", "interpolated"}:
            evidence += 1
        if normalize_text(candidate.match_level) in {"building", "venue"}:
            evidence += 1
        return evidence >= 7

    @classmethod
    def _same_candidate(cls, left: GeocodeCandidate, right: GeocodeCandidate) -> bool:
        if left.raw_id and right.raw_id and left.raw_id == right.raw_id:
            return True
        close_coordinates = (
            abs(left.latitude - right.latitude) <= 0.00002
            and abs(left.longitude - right.longitude) <= 0.00002
        )
        if not close_coordinates:
            return False
        same_display = normalize_text(left.display_name) == normalize_text(right.display_name)
        same_address = (
            normalize_text(left.road) == normalize_text(right.road)
            and cls._returned_number(left) == cls._returned_number(right)
        )
        return same_display or same_address

    @staticmethod
    def _merge_modes(first: str, second: str) -> str:
        modes = set(first.split("+")) | set(second.split("+"))
        ordered = [mode for mode in ("structured", "freeform") if mode in modes]
        return "+".join(ordered)

    @classmethod
    def _deduplicate_candidates(cls, candidates: list[GeocodeCandidate]) -> list[GeocodeCandidate]:
        deduplicated: list[GeocodeCandidate] = []
        for candidate in candidates:
            for index, existing in enumerate(deduplicated):
                if cls._same_candidate(existing, candidate):
                    # Prefere dados que tragam número e granularidade maior,
                    # mas preserva a proveniência de ambas as consultas.
                    existing_evidence = sum(
                        value is not None
                        for value in (existing.house_number, existing.road, existing.match_level)
                    )
                    candidate_evidence = sum(
                        value is not None
                        for value in (candidate.house_number, candidate.road, candidate.match_level)
                    )
                    preferred = candidate if candidate_evidence > existing_evidence else existing
                    deduplicated[index] = replace(
                        preferred,
                        query_mode=cls._merge_modes(existing.query_mode, candidate.query_mode),
                    )
                    break
            else:
                deduplicated.append(candidate)
        return deduplicated

    def geocode(self, query: AddressQuery) -> list[GeocodeCandidate]:
        if query.is_intersection:
            # A API não oferece uma consulta estruturada para duas vias.  Não
            # a contabilizamos como "structured" apenas porque o chamador
            # original entrou por este método.
            candidates = self._request_candidates(query, "freeform")
            self.last_diagnostics = GeocodingDiagnostics(
                freeform_used=True,
                freeform_candidate_count=len(candidates),
                freeform_requests=1,
            )
            return candidates

        structured_before = self.structured_request_count
        freeform_before = self.freeform_request_count
        structured = self._request_candidates(query, "structured")
        use_freeform = not any(self._has_strong_structured_match(query, candidate) for candidate in structured)
        freeform: list[GeocodeCandidate] = []
        if use_freeform:
            freeform = self._request_candidates(query, "freeform")
        self.last_diagnostics = GeocodingDiagnostics(
            structured_used=True,
            structured_candidate_count=len(structured),
            freeform_used=use_freeform,
            freeform_candidate_count=len(freeform),
            structured_requests=self.structured_request_count - structured_before,
            freeform_requests=self.freeform_request_count - freeform_before,
        )
        return self._deduplicate_candidates(structured + freeform)
