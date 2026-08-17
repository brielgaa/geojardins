"""Processamento em massa de endereços, com exportação XLSX auditável."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .address_lookup import AddressLookupResult, AddressLookupService, LookupFlag, LookupStatus, build_geocoding_service
from .address_normalizer import AddressValidationError, QueryType, address_cache_key, header_key
from .geocoding.cache import PersistentGeocodingCache
from .maps_links import google_maps_search_link
from .runtime import geocoding_cache_path, resource_path
from .settings import load_settings

LOGGER = logging.getLogger(__name__)

OUTPUT_COLUMNS = (
    "Logradouro Original",
    "Logradouro Normalizado",
    "Tipo Consulta",
    "Latitude",
    "Longitude",
    "Endereço Geocodificado",
    "Área",
    "Área Candidata",
    "Áreas Candidatas",
    "Fiscal",
    "Fiscal Candidato",
    "Distância Divisa (m)",
    "Match Code",
    "Match Type",
    "Match Level",
    "Precisão Geocodificação",
    "Status",
    "Flags",
    "Motivo Status",
    "Link Google Maps",
    "Place ID",
    "Provider Principal",
    "Provider Utilizado",
    "Fallback Utilizado",
    "Distância Providers (m)",
    "Número Solicitado",
    "Número Retornado",
    "Número Compatível",
    "Sugestão de Correção",
    "Tipo da Sugestão",
    "Confiança da Sugestão",
    "Confiança Final",
    "Motivo Decisão",
    "Cruzamento Via 1 Compatível",
    "Cruzamento Via 2 Compatível",
)

STREET_ALIASES = ("logradouro", "endereço", "endereco", "rua", "via")
NUMBER_ALIASES = (
    "altura",
    "alt num",
    "alt_num",
    "altura numerica",
    "altura numérica",
    "numero",
    "número",
    "num",
    "nº",
)


class BatchInputError(ValueError):
    """Formato ou mapeamento de colunas impossível de interpretar com segurança."""


@dataclass(frozen=True)
class InputRows:
    headers: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class BatchRowResult:
    source: dict[str, Any]
    result: AddressLookupResult


@dataclass(frozen=True)
class BatchDecisionRecord:
    line: int
    key: str
    status: str
    flags: tuple[str, ...]
    candidate_area: str | None
    confirmed_area: str | None


@dataclass(frozen=True)
class BatchRunSummary:
    processed: int
    statuses: Counter[str]
    areas: Counter[str]
    geocoding_calls: int
    reused_results: int
    structured_requests: int = 0
    freeform_requests: int = 0
    geoapify_requests: int = 0
    geoapify_candidates_returned: int = 0
    geoapify_candidates_usable: int = 0
    geoapify_candidates_used: int = 0
    locationiq_fallbacks: int = 0
    locationiq_requests: int = 0
    unique_addresses: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    generic_coordinates: int = 0
    provider_divergences: int = 0
    area_overlaps: int = 0
    fallback_resolved_not_found: int = 0
    fallback_changed_winning_provider: int = 0
    fallback_elevated_confidence: int = 0
    fallback_detected_divergence: int = 0
    fallback_result_rejected: int = 0
    fallback_no_effect: int = 0
    cache_hit_geoapify: int = 0
    cache_miss_geoapify: int = 0
    cache_hit_locationiq_structured: int = 0
    cache_miss_locationiq_structured: int = 0
    cache_hit_locationiq_freeform: int = 0
    cache_miss_locationiq_freeform: int = 0
    composite_promotions: int = 0
    correction_suggestions: int = 0
    street_incompatibilities: int = 0
    secondary_results_rejected: int = 0
    decision_settings: dict[str, float] | None = None
    records: tuple[BatchDecisionRecord, ...] = ()
    confidence_promotion_blockers: Counter[str] = field(default_factory=Counter)

    @property
    def queries_avoided(self) -> int:
        unique = self.unique_addresses or self.processed
        return max(0, self.processed - unique)

    def to_report(self) -> dict[str, Any]:
        return {
            "lines_processed": self.processed,
            "unique_addresses": self.unique_addresses,
            "queries_avoided_by_deduplication": self.queries_avoided,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "provider_calls": {"geoapify": self.geoapify_requests, "locationiq": self.locationiq_requests},
            "fallbacks": self.locationiq_fallbacks,
            "statuses": dict(self.statuses),
            "areas": dict(self.areas),
            "generic_coordinates": self.generic_coordinates,
            "provider_divergences": self.provider_divergences,
            "area_overlaps": self.area_overlaps,
            "decision_metrics": {
                "composite_promotions": self.composite_promotions,
                "correction_suggestions": self.correction_suggestions,
                "street_incompatibilities": self.street_incompatibilities,
                "secondary_results_rejected": self.secondary_results_rejected,
                "confidence_promotion_blockers": dict(self.confidence_promotion_blockers),
            },
            "decision_settings": self.decision_settings or {},
            "fallback_roi": {
                "fallbacks_triggered": self.locationiq_fallbacks,
                "resolved_not_found": self.fallback_resolved_not_found,
                "changed_winning_provider": self.fallback_changed_winning_provider,
                "elevated_confidence": self.fallback_elevated_confidence,
                "detected_divergence": self.fallback_detected_divergence,
                "result_rejected": self.fallback_result_rejected,
                "no_effect": self.fallback_no_effect,
            },
            "cache_breakdown": {
                "geoapify": {"hits": self.cache_hit_geoapify, "misses": self.cache_miss_geoapify},
                "locationiq_structured": {"hits": self.cache_hit_locationiq_structured, "misses": self.cache_miss_locationiq_structured},
                "locationiq_freeform": {"hits": self.cache_hit_locationiq_freeform, "misses": self.cache_miss_locationiq_freeform},
            },
            "records": [asdict(record) for record in self.records],
        }


def _decode_csv(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BatchInputError(f"Não foi possível decodificar o CSV {path}.")


def read_csv_input(path: Path) -> InputRows:
    text = _decode_csv(path)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    headers = tuple(reader.fieldnames or ())
    if not headers:
        raise BatchInputError("CSV sem cabeçalho.")
    return InputRows(headers, tuple(dict(row) for row in reader))


def _parse_txt_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped:
        raise BatchInputError("Linha vazia.")
    if ";" in stripped:
        parts = [part.strip() for part in stripped.split(";")]
    elif "\t" in stripped:
        parts = [part.strip() for part in stripped.split("\t")]
    elif stripped.count(",") == 1:
        parts = [part.strip() for part in stripped.split(",")]
    else:
        raise BatchInputError("Linha TXT ambígua; use ';' ou TAB entre logradouro e altura.")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise BatchInputError("Linha TXT deve conter exatamente logradouro e altura.")
    return parts[0], parts[1]


def read_txt_input(path: Path) -> InputRows:
    text = _decode_csv(path)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            street, number = _parse_txt_line(line)
            rows.append({"Logradouro": street, "Altura": number, "Linha original": line})
        except BatchInputError as exc:
            rows.append({"Logradouro": None, "Altura": None, "Linha original": line, "_erro_txt": str(exc)})
    return InputRows(("Logradouro", "Altura", "Linha original"), tuple(rows))


def read_xlsx_input(path: Path) -> InputRows:
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        worksheet = workbook.active
        values = worksheet.iter_rows(values_only=True)
        header_values = next(values, None)
        if header_values is None:
            raise BatchInputError("XLSX sem cabeçalho.")
        headers = tuple("" if value is None else str(value) for value in header_values)
        if not any(headers):
            raise BatchInputError("XLSX sem cabeçalho.")
        rows = tuple(
            {headers[index]: row[index] if index < len(row) else None for index in range(len(headers))}
            for row in values
        )
        return InputRows(headers, rows)
    finally:
        workbook.close()


def read_input(path: Path) -> InputRows:
    suffix = path.suffix.casefold()
    if suffix == ".xlsx":
        return read_xlsx_input(path)
    if suffix == ".csv":
        return read_csv_input(path)
    if suffix == ".txt":
        return read_txt_input(path)
    raise BatchInputError("Formato não suportado. Use .xlsx, .csv ou .txt.")


def resolve_column(
    headers: Iterable[str],
    aliases: Iterable[str],
    explicit: str | None,
    label: str,
) -> str:
    available = list(headers)
    if explicit is not None:
        if explicit not in available:
            raise BatchInputError(f"Coluna informada para {label!r} não existe: {explicit!r}.")
        return explicit
    alias_keys = {header_key(alias) for alias in aliases}
    matches = [header for header in available if header_key(header) in alias_keys]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise BatchInputError(
            f"Não encontrei a coluna de {label}. Use --{label}-col para indicar explicitamente."
        )
    raise BatchInputError(
        f"Há mais de uma coluna possível para {label}: {', '.join(matches)}. "
        f"Use --{label}-col para escolher."
    )


class BatchProcessor:
    """Executa consultas únicas e replica o resultado preservando a ordem de origem."""

    def __init__(
        self,
        service: AddressLookupService,
        progress: Callable[[str], None] | None = print,
    ) -> None:
        self.service = service
        self.progress = progress

    def process(
        self,
        source: InputRows,
        *,
        street_column: str,
        number_column: str,
    ) -> tuple[tuple[BatchRowResult, ...], BatchRunSummary]:
        result_by_key: dict[str, AddressLookupResult] = {}
        row_keys: list[str | None] = []
        first_rows: dict[str, tuple[Any, Any]] = {}
        invalid_results: dict[int, AddressLookupResult] = {}
        total = len(source.rows)
        for index, row in enumerate(source.rows):
            street = row.get(street_column)
            number = row.get(number_column)
            forced_error = row.get("_erro_txt")
            if forced_error:
                invalid_results[index] = self.service._invalid(street, number, str(forced_error))
                row_keys.append(None)
                continue
            try:
                key = self.service.query_key(street, number)
            except AddressValidationError:
                # Mantém a validação detalhada no serviço e não agrupa entradas
                # inválidas por acidente.
                key = f"INVALID:{index}"
            row_keys.append(key)
            first_rows.setdefault(key, (street, number))

        for position, (key, (street, number)) in enumerate(first_rows.items(), start=1):
            result_by_key[key] = self.service.lookup(street, number)
            if self.progress is not None:
                result = result_by_key[key]
                self.progress(f"[consulta {position}/{len(first_rows)}] {street or '-'}, {number or '-'} → {result.area or '-'} / {result.status.value}")

        generic_keys = self._generic_coordinate_keys(result_by_key)
        for key in generic_keys:
            street, number = first_rows[key]
            retried = self.service.lookup(street, number, force_fallback=True)
            trusted_fallback = (
                retried.candidate is not None
                and retried.candidate.provider == "locationiq"
                and retried.number_compatible is not False
                and not retried.providers_divergent
            )
            result_by_key[key] = retried.with_flags(
                LookupFlag.COORDENADA_GENERICA,
                force_conferir=not trusted_fallback,
                reason=(
                    retried.reason if trusted_fallback
                    else "Coordenada genérica detectada para alturas distintas; requer conferência ou resolução independente."
                ),
            )

        processed: list[BatchRowResult] = []
        reused = 0
        seen: set[str] = set()
        for index, row in enumerate(source.rows):
            key = row_keys[index]
            if key is None:
                result = invalid_results[index]
            else:
                result = result_by_key[key]
                if key in seen:
                    reused += 1
                seen.add(key)
            processed.append(BatchRowResult(dict(row), result))
        statuses = Counter(item.result.status.value for item in processed)
        areas = Counter(item.result.area for item in processed if item.result.area)
        calls = getattr(self.service.geocoder, "request_count", None)
        if calls is None:
            calls = getattr(self.service.geocoder, "call_count", None)
        if calls is None:
            calls = len(getattr(self.service.geocoder, "queries", ()))
        structured_requests = int(getattr(self.service.geocoder, "structured_request_count", 0))
        freeform_requests = int(getattr(self.service.geocoder, "freeform_request_count", 0))
        geoapify_requests = int(getattr(self.service.geocoder, "geoapify_request_count", 0))
        geoapify_candidates_returned = int(getattr(self.service.geocoder, "geoapify_candidates_returned", 0))
        geoapify_candidates_usable = int(getattr(self.service.geocoder, "geoapify_usable_candidate_count", 0))
        geoapify_candidates_used = int(getattr(self.service.geocoder, "geoapify_candidates_used", 0))
        locationiq_fallbacks = int(getattr(self.service.geocoder, "locationiq_fallback_count", 0))
        locationiq_requests = int(getattr(self.service.geocoder, "locationiq_request_count", 0))
        cache = self.service.cache
        cache_hits = cache.stats.hits if cache is not None else 0
        cache_misses = cache.stats.misses if cache is not None else 0
        decision_records = tuple(
            BatchDecisionRecord(
                line=index + 2,
                key=f"{index + 2}|{item.result.normalized_street or item.result.original_street}|{item.result.normalized_house_number or '-'}",
                status=item.result.status.value,
                flags=tuple(flag.value for flag in item.result.flags),
                candidate_area=item.result.candidate_area,
                confirmed_area=item.result.confirmed_area,
            )
            for index, item in enumerate(processed)
        )
        confidence_promotion_blockers: Counter[str] = Counter()
        for item in processed:
            result = item.result
            if result.precision is None or result.precision.value != "BAIXA" or result.confidence_promoted:
                continue
            blockers = result.confidence.promotion_blockers if result.confidence else ()
            confidence_promotion_blockers.update(blockers or ("confidence_blocked_other",))
        return tuple(processed), BatchRunSummary(
            total,
            statuses,
            areas,
            int(calls),
            reused,
            structured_requests,
            freeform_requests,
            geoapify_requests,
            geoapify_candidates_returned,
            geoapify_candidates_usable,
            geoapify_candidates_used,
            locationiq_fallbacks,
            locationiq_requests,
            len(first_rows),
            cache_hits,
            cache_misses,
            sum(LookupFlag.COORDENADA_GENERICA in item.result.flags for item in processed),
            sum(LookupFlag.PROVIDERS_DIVERGENTES in item.result.flags for item in processed),
            sum(LookupFlag.SOBREPOSICAO_AREAS in item.result.flags for item in processed),
            int(getattr(self.service.geocoder, "fallback_resolved_not_found", 0)),
            int(getattr(self.service.geocoder, "fallback_changed_winning_provider", 0)),
            int(getattr(self.service.geocoder, "fallback_elevated_confidence", 0)),
            int(getattr(self.service.geocoder, "fallback_detected_divergence", 0)),
            int(getattr(self.service.geocoder, "fallback_result_rejected", 0)),
            int(getattr(self.service.geocoder, "fallback_no_effect", 0)),
            cache.stats.hits_by_provider.get("geoapify", 0) if cache is not None else 0,
            cache.stats.misses_by_provider.get("geoapify", 0) if cache is not None else 0,
            cache.stats.hits_by_provider_mode.get("locationiq:structured", 0) if cache is not None else 0,
            cache.stats.misses_by_provider_mode.get("locationiq:structured", 0) if cache is not None else 0,
            cache.stats.hits_by_provider_mode.get("locationiq:freeform", 0) if cache is not None else 0,
            cache.stats.misses_by_provider_mode.get("locationiq:freeform", 0) if cache is not None else 0,
            sum(item.result.confidence_promoted for item in processed),
            sum(bool(item.result.suggested_correction) for item in processed),
            sum(LookupFlag.LOGRADOURO_INCOMPATIVEL in item.result.flags for item in processed),
            sum(LookupFlag.SECONDARY_RESULT_REJECTED in item.result.flags for item in processed),
            {
                "boundary_warning_m": self.service.settings.boundary_warning_m,
                "provider_divergence_m": self.service.settings.provider_divergence_m,
                "provider_agreement_strong_m": self.service.settings.provider_agreement_strong_m,
                "provider_agreement_acceptable_m": self.service.settings.provider_agreement_acceptable_m,
                "safe_distance_from_divider_m": self.service.settings.safe_distance_from_divider_m,
                "generic_coordinate_tolerance_m": self.service.settings.generic_coordinate_tolerance_m,
                "street_fuzzy_suggest_threshold": self.service.settings.street_fuzzy_suggest_threshold,
                "street_fuzzy_auto_threshold": self.service.settings.street_fuzzy_auto_threshold,
            },
            decision_records,
            confidence_promotion_blockers=confidence_promotion_blockers,
        )

    def _generic_coordinate_keys(self, results: dict[str, AddressLookupResult]) -> set[str]:
        """Identifica mesmo logradouro, alturas distintas e coordenadas quase iguais."""

        by_street: dict[str, list[tuple[str, AddressLookupResult]]] = {}
        for key, result in results.items():
            if (
                result.query_type is not QueryType.ADDRESS
                or result.candidate is None
                or result.normalized_house_number is None
                or not result.normalized_street
            ):
                continue
            by_street.setdefault(result.normalized_street, []).append((key, result))
        suspicious: set[str] = set()
        tolerance = self.service.settings.generic_coordinate_tolerance_m
        from pyproj import Geod

        geod = Geod(ellps="GRS80")
        for values in by_street.values():
            for index, (first_key, first) in enumerate(values):
                for second_key, second in values[index + 1:]:
                    if first.normalized_house_number == second.normalized_house_number:
                        continue
                    assert first.latitude is not None and first.longitude is not None
                    assert second.latitude is not None and second.longitude is not None
                    _, _, distance = geod.inv(first.longitude, first.latitude, second.longitude, second.latitude)
                    same_place = first.candidate.raw_id and first.candidate.raw_id == second.candidate.raw_id
                    if same_place or abs(float(distance)) <= tolerance:
                        suspicious.update((first_key, second_key))
        return suspicious


def default_output_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}_geojardins.xlsx")


def process_batch(
    input_path: Path,
    *,
    service: AddressLookupService,
    output_path: Path | None = None,
    street_column: str | None = None,
    number_column: str | None = None,
    progress: Callable[[str], None] | None = None,
    report_path: Path | None = None,
    compare_baseline: Path | None = None,
) -> tuple[tuple[BatchRowResult, ...], BatchRunSummary, Path]:
    """API compartilhada para lote; GUI e CLI usam a mesma implementação."""

    source = read_input(input_path)
    street = resolve_column(source.headers, STREET_ALIASES, street_column, "logradouro")
    number = resolve_column(source.headers, NUMBER_ALIASES, number_column, "altura")
    results, summary = BatchProcessor(service, progress=progress).process(
        source, street_column=street, number_column=number
    )
    destination = output_path or default_output_path(input_path)
    write_output(input_path, source, results, destination)
    if report_path is not None:
        write_batch_report(summary, report_path, compare_baseline=compare_baseline)
    return results, summary, destination


def _new_output_workbook(source: InputRows) -> tuple[Workbook, Any, tuple[str, ...]]:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "GeoJardins"
    headers = tuple(source.headers)
    # As colunas de resultado são acrescentadas por _column_positions, em uma
    # única posição por nome, tanto para fontes tabulares quanto para XLSX.
    worksheet.append(list(headers))
    return workbook, worksheet, headers


def _existing_xlsx_workbook(path: Path) -> tuple[Workbook, Any, tuple[str, ...]]:
    workbook = load_workbook(path)
    worksheet = workbook.active
    headers = tuple("" if cell.value is None else str(cell.value) for cell in worksheet[1])
    return workbook, worksheet, headers


def _column_positions(worksheet: Any, existing_headers: tuple[str, ...]) -> dict[str, int]:
    positions = {header: index + 1 for index, header in enumerate(existing_headers) if header}
    for name in OUTPUT_COLUMNS:
        if name == "Link Google Maps" and "Google Maps" in positions:
            positions[name] = positions["Google Maps"]
            continue
        if name not in positions:
            position = worksheet.max_column + 1
            worksheet.cell(row=1, column=position, value=name)
            positions[name] = position
    return positions


def _format_worksheet(worksheet: Any) -> None:
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for column in range(1, worksheet.max_column + 1):
        widest = max(
            (len(str(worksheet.cell(row=row, column=column).value or "")) for row in range(1, worksheet.max_row + 1)),
            default=0,
        )
        worksheet.column_dimensions[get_column_letter(column)].width = min(max(widest + 2, 12), 55)


def write_output(
    input_path: Path,
    source: InputRows,
    results: Iterable[BatchRowResult],
    output_path: Path,
) -> None:
    if input_path.suffix.casefold() == ".xlsx":
        workbook, worksheet, original_headers = _existing_xlsx_workbook(input_path)
    else:
        workbook, worksheet, original_headers = _new_output_workbook(source)
    try:
        positions = _column_positions(worksheet, original_headers)
        start_row = 2
        for row_number, item in enumerate(results, start=start_row):
            if input_path.suffix.casefold() != ".xlsx":
                for column, header in enumerate(source.headers, start=1):
                    worksheet.cell(row=row_number, column=column, value=item.source.get(header))
            values = item.result.export_values()
            for name in OUTPUT_COLUMNS:
                cell = worksheet.cell(row=row_number, column=positions[name], value=values.get(name))
                if name == "Link Google Maps" and cell.value:
                    cell.hyperlink = str(cell.value)
                    cell.style = "Hyperlink"
            if item.result.distance_to_boundary_m is not None:
                worksheet.cell(row=row_number, column=positions["Distância Divisa (m)"]).number_format = "0.0"
            if item.result.provider_distance_m is not None:
                worksheet.cell(row=row_number, column=positions["Distância Providers (m)"]).number_format = "0.0"
        _format_worksheet(worksheet)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
    finally:
        workbook.close()


def format_summary(summary: BatchRunSummary) -> str:
    unique = summary.unique_addresses or max(0, summary.processed - summary.reused_results)
    avoided = max(0, summary.processed - unique)
    lines = [
        # Mantém a linha legada para automações existentes e expõe a métrica
        # explícita requerida pela Fase 3A logo abaixo.
        f"Endereços processados: {summary.processed}",
        f"Linhas processadas: {summary.processed}",
        f"Endereços únicos: {unique}",
        f"Consultas evitadas por deduplicação: {avoided}",
        f"Cache hits: {summary.cache_hits}",
        f"Cache misses: {summary.cache_misses}",
        "",
    ]
    for status in LookupStatus:
        lines.append(f"{status.value}: {summary.statuses.get(status.value, 0)}")
    lines.append("")
    for index in range(1, 7):
        area = f"Tomé_{index}"
        lines.append(f"{area}: {summary.areas.get(area, 0)}")
    lines.extend(
        (
            "",
            f"Geoapify requests: {summary.geoapify_requests}",
            f"Geoapify candidatos retornados: {summary.geoapify_candidates_returned}",
            f"Geoapify candidatos utilizáveis: {summary.geoapify_candidates_usable}",
            f"Geoapify candidatos utilizados: {summary.geoapify_candidates_used}",
            f"LocationIQ fallbacks: {summary.locationiq_fallbacks}",
            f"LocationIQ requests: {summary.locationiq_requests}",
            f"Requests totais dos providers: {summary.geocoding_calls}",
            f"LocationIQ structured requests: {summary.structured_requests}",
            f"LocationIQ free-form fallback requests: {summary.freeform_requests}",
            f"Fallback resolveu não localizado: {summary.fallback_resolved_not_found}",
            f"Fallback mudou provider vencedor: {summary.fallback_changed_winning_provider}",
            f"Fallback elevou confiança: {summary.fallback_elevated_confidence}",
            f"Fallback detectou divergência: {summary.fallback_detected_divergence}",
            f"Fallback rejeitado/sem efeito: {summary.fallback_result_rejected}/{summary.fallback_no_effect}",
            f"Cache Geoapify (hit/miss): {summary.cache_hit_geoapify}/{summary.cache_miss_geoapify}",
            f"Cache LocationIQ structured (hit/miss): {summary.cache_hit_locationiq_structured}/{summary.cache_miss_locationiq_structured}",
            f"Cache LocationIQ free-form (hit/miss): {summary.cache_hit_locationiq_freeform}/{summary.cache_miss_locationiq_freeform}",
            f"Resultados reutilizados por deduplicação: {summary.reused_results}",
            f"Coordenadas genéricas: {summary.generic_coordinates}",
            f"Providers divergentes: {summary.provider_divergences}",
            f"Sobreposições de áreas: {summary.area_overlaps}",
            f"Promoções por confiança composta: {summary.composite_promotions}",
            f"Sugestões de correção: {summary.correction_suggestions}",
            f"Logradouros incompatíveis: {summary.street_incompatibilities}",
            f"Resultados secundários rejeitados: {summary.secondary_results_rejected}",
        )
    )
    if summary.confidence_promotion_blockers:
        lines.extend(("", "Bloqueios de promoção por confiança:"))
        lines.extend(
            f"{reason}: {count}"
            for reason, count in sorted(summary.confidence_promotion_blockers.items())
        )
    return "\n".join(lines)


def _records_from_xlsx(path: Path) -> list[dict[str, Any]]:
    source = read_xlsx_input(path)
    headers = set(source.headers)
    if "Status" not in headers:
        raise BatchInputError("XLSX-base não possui a coluna Status.")
    street_column = "Logradouro Normalizado" if "Logradouro Normalizado" in headers else "Logradouro Original"
    number_column = "Número Solicitado" if "Número Solicitado" in headers else "Altura"
    area_column = "Área Candidata" if "Área Candidata" in headers else "Área"
    return [
        {
            "line": index + 2,
            "key": f"{index + 2}|{row.get(street_column) or ''}|{row.get(number_column) or '-'}",
            "status": row.get("Status"),
            "flags": tuple(part.strip() for part in str(row.get("Flags") or "").split(";") if part.strip()),
            "candidate_area": row.get(area_column),
            "confirmed_area": row.get("Área"),
        }
        for index, row in enumerate(source.rows)
    ]


def _comparison_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("records", [])
    return [value for value in values if isinstance(value, dict) and isinstance(value.get("key"), str)] if isinstance(values, list) else []


def compare_batch_reports(before_path: Path, after_report: dict[str, Any]) -> dict[str, Any]:
    """Compara status agregados e, quando disponíveis, decisões por linha."""

    if before_path.suffix.casefold() == ".xlsx":
        records = _records_from_xlsx(before_path)
        before: dict[str, Any] = {"records": records, "statuses": dict(Counter(str(record.get("status") or "") for record in records))}
    else:
        try:
            before = json.loads(before_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchInputError(f"Não foi possível ler relatório-base {before_path}: {exc}") from exc
        if not isinstance(before, dict):
            raise BatchInputError("Relatório-base deve conter objeto JSON.")
    before_status = before.get("statuses", {}) if isinstance(before.get("statuses", {}), dict) else {}
    after_status = after_report["statuses"]
    statuses = sorted(set(before_status) | set(after_status))
    before_records = _comparison_records(before)
    after_records = _comparison_records(after_report)
    before_by_key = {str(record["key"]): record for record in before_records}
    transitions: Counter[str] = Counter()
    added_flags: Counter[str] = Counter()
    removed_flags: Counter[str] = Counter()
    area_changes: list[dict[str, Any]] = []
    for after in after_records:
        previous = before_by_key.get(str(after["key"]))
        if previous is None:
            continue
        before_value = str(previous.get("status") or "")
        after_value = str(after.get("status") or "")
        if before_value != after_value:
            transitions[f"{before_value} -> {after_value}"] += 1
        before_flags = {str(flag) for flag in previous.get("flags", ())}
        after_flags = {str(flag) for flag in after.get("flags", ())}
        for flag in sorted(after_flags - before_flags):
            added_flags[flag] += 1
        for flag in sorted(before_flags - after_flags):
            removed_flags[flag] += 1
        before_area = previous.get("candidate_area") or previous.get("confirmed_area")
        after_area = after.get("candidate_area") or after.get("confirmed_area")
        if before_area != after_area:
            area_changes.append({"key": after["key"], "before_area": before_area, "after_area": after_area})
    result = {
        "baseline_path": str(before_path),
        "status_delta": {
            status: int(after_status.get(status, 0)) - int(before_status.get(status, 0))
            for status in statuses
        },
        "before_statuses": before_status,
        "after_statuses": after_status,
        "record_transitions_available": bool(before_records and after_records),
        "status_transitions": dict(transitions),
        "flags_added": dict(added_flags),
        "flags_removed": dict(removed_flags),
        "area_changes": area_changes,
    }
    if not result["record_transitions_available"]:
        result["transition_note"] = "O relatório-base é agregado; use a saída XLSX da Fase 3A ou um relatório com records para transições por linha."
    return result


def write_batch_report(
    summary: BatchRunSummary,
    output_path: Path,
    *,
    compare_baseline: Path | None = None,
) -> dict[str, Any]:
    report = summary.to_report()
    if compare_baseline is not None:
        report["benchmark_comparison"] = compare_batch_reports(compare_baseline, report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Geocodifica um arquivo XLSX, CSV ou TXT.")
    parser.add_argument("arquivo", type=Path, nargs="?", help="Arquivo XLSX, CSV ou TXT a processar.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--logradouro-col")
    parser.add_argument("--altura-col")
    parser.add_argument("--areas", type=Path)
    parser.add_argument("--boundary", type=Path)
    parser.add_argument("--fiscais", type=Path)
    parser.add_argument("--settings", type=Path, default=resource_path("config", "settings.json"))
    parser.add_argument("--no-cache", action="store_true", help="Ignora o cache SQLite nesta execução.")
    parser.add_argument("--cache-path", type=Path, default=geocoding_cache_path())
    parser.add_argument("--clear-cache", action="store_true", help="Limpa o cache e encerra sem geocodificar.")
    parser.add_argument("--primary", choices=("geoapify",), default="geoapify")
    parser.add_argument("--secondary", choices=("locationiq", "none"), default="locationiq")
    parser.add_argument("--divider-warning-m", type=float, help="Sobrescreve a margem de divisa somente nesta execução.")
    parser.add_argument("--report", type=Path, default=Path("output/reports/batch_report.json"))
    parser.add_argument("--compare-baseline", type=Path, help="Relatório batch_report.json anterior para comparação.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    if args.clear_cache:
        PersistentGeocodingCache(args.cache_path).clear()
        print(f"Cache limpo: {args.cache_path}")
        return 0
    if args.arquivo is None:
        print("Lote não executado: informe o arquivo de entrada ou use --clear-cache.")
        return 2
    try:
        settings = load_settings(args.settings)
        if args.divider_warning_m is not None:
            if args.divider_warning_m < 0:
                raise BatchInputError("--divider-warning-m não pode ser negativo.")
            settings = replace(settings, boundary_warning_m=args.divider_warning_m)
        service = build_geocoding_service(
            settings=settings,
            areas_path=args.areas,
            boundary_path=args.boundary,
            fiscais_path=args.fiscais,
            locationiq_token="" if args.secondary == "none" else None,
            cache_path=args.cache_path,
            use_cache=not args.no_cache,
        )
    except (BatchInputError, ValueError) as exc:
        print(f"Lote não executado: {exc}")
        return 2
    try:
        results, summary, output = process_batch(
            args.arquivo,
            service=service,
            output_path=args.output,
            street_column=args.logradouro_col,
            number_column=args.altura_col,
            report_path=args.report,
            compare_baseline=args.compare_baseline,
        )
    except (BatchInputError, OSError, ValueError) as exc:
        print(f"Lote não executado: {exc}")
        return 2
    print()
    print(format_summary(summary))
    print(f"\nArquivo gerado: {output}")
    print(f"Relatório gerado: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
