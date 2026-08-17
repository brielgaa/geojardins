"""Cache SQLite persistente para respostas normalizadas de providers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AddressQuery, GeocodeCandidate, GeocodingDiagnostics


CACHE_SCHEMA_VERSION = "3b-1"
LEGACY_CACHE_SCHEMA_VERSION = "3a-1"


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    hits_by_provider: dict[str, int] = field(default_factory=dict)
    misses_by_provider: dict[str, int] = field(default_factory=dict)
    hits_by_provider_mode: dict[str, int] = field(default_factory=dict)
    misses_by_provider_mode: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def _increment(values: dict[str, int], key: str) -> None:
        values[key] = values.get(key, 0) + 1

    def record_hit(self, provider: str, modes: tuple[str, ...] = ()) -> None:
        self.hits += 1
        self._increment(self.hits_by_provider, provider)
        for mode in modes:
            self._increment(self.hits_by_provider_mode, f"{provider}:{mode}")

    def record_miss(self, provider: str) -> None:
        self.misses += 1
        self._increment(self.misses_by_provider, provider)

    def record_mode_miss(self, provider: str, modes: tuple[str, ...]) -> None:
        for mode in modes:
            self._increment(self.misses_by_provider_mode, f"{provider}:{mode}")


class PersistentGeocodingCache:
    """Armazena candidatos limpos, nunca tokens, chaves ou URLs com segredo."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stats = CacheStats()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS geocoding_cache (
                provider TEXT NOT NULL, query_key TEXT NOT NULL, schema_version TEXT NOT NULL,
                candidates_json TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                PRIMARY KEY (provider, query_key, schema_version))""")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(geocoding_cache)")}
            if "metadata_json" not in columns:
                connection.execute("ALTER TABLE geocoding_cache ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        self.last_metadata: dict[str, Any] = {}

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @contextmanager
    def _connection(self):  # type: ignore[no-untyped-def]
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get(self, provider: str, query: AddressQuery) -> list[GeocodeCandidate] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT candidates_json, metadata_json FROM geocoding_cache WHERE provider=? AND query_key=? AND schema_version=?",
                (provider, query.cache_key, CACHE_SCHEMA_VERSION),
            ).fetchone()
            if row is None and query.cache_key.startswith("3b-1|"):
                # A resposta limpa da Fase 3A continua válida; apenas os novos
                # metadados de modo não existiam. Reutilizamos os candidatos
                # sem apagar a base persistente nem provocar chamadas extras.
                legacy_key = query.cache_key.replace("3b-1|", "3a-1|", 1)
                row = connection.execute(
                    "SELECT candidates_json, metadata_json FROM geocoding_cache WHERE provider=? AND query_key=? AND schema_version=?",
                    (provider, legacy_key, LEGACY_CACHE_SCHEMA_VERSION),
                ).fetchone()
        if row is None:
            self.stats.record_miss(provider)
            return None
        try:
            raw = json.loads(row[0])
            if not isinstance(raw, list):
                raise ValueError("cached candidates must be a list")
            candidates = [GeocodeCandidate(**value) for value in raw if isinstance(value, dict)]
            metadata = json.loads(row[1]) if row[1] else {}
            if not isinstance(metadata, dict):
                metadata = {}
        except (TypeError, ValueError, json.JSONDecodeError):
            self.stats.record_miss(provider)
            return None
        self.last_metadata = metadata
        modes = tuple(str(mode) for mode in metadata.get("modes", ()) if isinstance(mode, str))
        self.stats.record_hit(provider, modes)
        return candidates

    def put(
        self,
        provider: str,
        query: AddressQuery,
        candidates: list[GeocodeCandidate],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=False, separators=(",", ":"))
        metadata_payload = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute("""INSERT INTO geocoding_cache(provider,query_key,schema_version,candidates_json,metadata_json,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(provider,query_key,schema_version) DO UPDATE SET
                candidates_json=excluded.candidates_json, metadata_json=excluded.metadata_json, created_at=excluded.created_at""", (
                provider, query.cache_key, CACHE_SCHEMA_VERSION, payload, metadata_payload, datetime.now(timezone.utc).isoformat(),
            ))

    def clear(self) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM geocoding_cache")


class CachedGeocoder:
    """Adaptador transparente que registra cache hit/miss por provider e consulta."""

    def __init__(self, delegate: Any, cache: PersistentGeocodingCache) -> None:
        self.delegate = delegate
        self.cache = cache
        self.provider_name = str(getattr(delegate, "provider_name", delegate.__class__.__name__.casefold()))
        self.last_diagnostics = GeocodingDiagnostics()

    @property
    def request_count(self) -> int:
        return int(getattr(self.delegate, "request_count", 0))

    def geocode(self, query: AddressQuery) -> list[GeocodeCandidate]:
        cached = self.cache.get(self.provider_name, query)
        if cached is not None:
            modes = tuple(self.cache.last_metadata.get("modes", ()))
            self.last_diagnostics = GeocodingDiagnostics(
                structured_used="structured" in modes,
                freeform_used="freeform" in modes,
            )
            return cached
        candidates = self.delegate.geocode(query)
        self.last_diagnostics = getattr(self.delegate, "last_diagnostics", GeocodingDiagnostics())
        modes = tuple(
            mode
            for mode, used in (
                ("structured", self.last_diagnostics.structured_used),
                ("freeform", self.last_diagnostics.freeform_used),
            )
            if used
        )
        self.cache.stats.record_mode_miss(self.provider_name, modes)
        self.cache.put(self.provider_name, query, candidates, metadata={"modes": modes})
        return candidates

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
