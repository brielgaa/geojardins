"""Contrato de geocodificação, exceções e rate limiter reutilizável."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from .models import AddressQuery, GeocodeCandidate


class GeocodingError(RuntimeError):
    """Falha tratável de geocodificação, sem dados sensíveis."""


class GeocodingConfigurationError(GeocodingError):
    """Provider sem configuração necessária para uma consulta real."""


class GeocodingAuthenticationError(GeocodingError):
    """Token inválido ou sem acesso; não deve sofrer retry."""


class GeocodingRequestError(GeocodingError):
    """Requisição inválida; não deve sofrer retry."""


class GeocodingTemporaryError(GeocodingError):
    """Falha transitória que pode ser repetida dentro de um limite."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class Geocoder(Protocol):
    """Interface consumida pelo motor, sem dependência de LocationIQ."""

    def geocode(self, query: AddressQuery) -> list[GeocodeCandidate]:
        """Retorna todos os candidatos ordenados pelo provider."""


class RateLimiter:
    """Limita inícios de requisição em uma única execução/processo."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("locationiq_requests_per_second deve ser maior que zero.")
        self.interval_s = 1.0 / requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._next_allowed_at = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait_s = max(0.0, self._next_allowed_at - now)
            if wait_s:
                self._sleeper(wait_s)
                now = self._clock()
            self._next_allowed_at = max(now, self._next_allowed_at) + self.interval_s
