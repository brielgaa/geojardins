from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from shapely.geometry import Polygon

from geojardins.address_lookup import (
    AddressLookupService,
    GeocodingPrecision,
    LookupStatus,
    classify_precision,
)
from geojardins.address_normalizer import AddressValidationError, normalize_house_number
from geojardins.batch_lookup import (
    BatchInputError,
    BatchProcessor,
    BatchRunSummary,
    InputRows,
    NUMBER_ALIASES,
    STREET_ALIASES,
    build_paste_export_path,
    format_summary,
    read_input,
    read_paste_input,
    process_batch_source,
    resolve_column,
    resolve_batch_columns,
    write_output,
)
from geojardins.classification import GeographicClassifier
from geojardins.fiscais import FiscalResolver, NOT_CONFIGURED
from geojardins.geocoding.base import GeocodingTemporaryError
from geojardins.geocoding.mock import MockGeocoder
from geojardins.geocoding.models import GeocodeCandidate
from geojardins.georef import build_boundary_collection
from geojardins.settings import Settings


def classifier() -> GeographicClassifier:
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
    return GeographicClassifier(areas, build_boundary_collection(areas))


def candidate(
    *,
    latitude: float = -23.595,
    longitude: float = -46.695,
    number: str | None = "1250",
    road: str | None = "Rua Oscar Freire",
    match_code: str = "exact",
    match_type: str = "point",
    match_level: str = "building",
    rank: int = 1,
    query_mode: str = "unknown",
) -> GeocodeCandidate:
    return GeocodeCandidate(
        latitude=latitude,
        longitude=longitude,
        display_name="Rua Oscar Freire, 1250, São Paulo",
        house_number=number,
        road=road,
        city="São Paulo",
        state="SP",
        country="Brasil",
        country_code="br",
        provider="mock",
        original_rank=rank,
        match_code=match_code,
        match_type=match_type,
        match_level=match_level,
        raw_id="mock-1",
        query_mode=query_mode,
    )


def query_key(street: str = "Rua Oscar Freire", number: str = "1250") -> str:
    return f"{street}, {number}, São Paulo - SP, Brasil"


def service(responses: dict[str, list[GeocodeCandidate] | Exception]) -> tuple[AddressLookupService, MockGeocoder]:
    mock = MockGeocoder(responses)  # type: ignore[arg-type]
    return (
        AddressLookupService(mock, classifier(), FiscalResolver({"Tomé_1": ""}), Settings(boundary_warning_m=30)),
        mock,
    )


class AddressNormalizationTests(unittest.TestCase):
    def test_numeric_height_is_normalized_without_decimal_suffix(self) -> None:
        for value in (1250, 1250.0, "1250", "1250 ", "01250"):
            with self.subTest(value=value):
                self.assertEqual(normalize_house_number(value), "1250")
        for value in ("1250.5", "s/n", None):
            with self.subTest(value=value):
                with self.assertRaises(AddressValidationError):
                    normalize_house_number(value)


class AddressLookupTests(unittest.TestCase):
    def test_oscar_freire_structured_number_beats_freeform_street_centroid(self) -> None:
        svc, _ = service(
            {
                query_key("Rua Oscar Freire", "1250"): [
                    candidate(query_mode="structured", rank=1),
                    candidate(
                        number=None,
                        match_type="centroid",
                        match_level="street",
                        rank=1,
                        query_mode="freeform",
                    ),
                ]
            }
        )
        result = svc.lookup("Rua Oscar Freire", 1250)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.query_mode, "structured")  # type: ignore[union-attr]

    def test_dona_julia_without_house_number_remains_conferir(self) -> None:
        svc, _ = service(
            {
                query_key("Rua Dona Júlia", "14"): [
                    candidate(
                        road="Rua Dona Júlia",
                        number=None,
                        match_type="centroid",
                        match_level="street",
                        query_mode="structured",
                    ),
                    candidate(
                        road="Rua Dona Júlia",
                        number=None,
                        match_type="centroid",
                        match_level="street",
                        rank=2,
                        query_mode="freeform",
                    ),
                ]
            }
        )
        self.assertEqual(svc.lookup("Rua Dona Júlia", 14).status, LookupStatus.CONFERIR)

    def test_palestra_italia_fallback_inside_candidate_beats_weak_outside_structured(self) -> None:
        svc, _ = service(
            {
                query_key("Rua Palestra Itália", "14"): [
                    candidate(
                        road="Rua Palestra Itália",
                        latitude=-23.5,
                        longitude=-46.5,
                        number=None,
                        match_type="centroid",
                        match_level="street",
                        query_mode="structured",
                    ),
                    candidate(
                        road="Rua Palestra Itália",
                        number="14",
                        query_mode="freeform",
                    ),
                ]
            }
        )
        result = svc.lookup("Rua Palestra Itália", 14)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.query_mode, "freeform")  # type: ignore[union-attr]

    def test_inside_but_incompatible_structured_candidate_is_never_confirmed(self) -> None:
        svc, _ = service(
            {
                query_key("Rua Oscar Freire", "1250"): [
                    candidate(road="Rua Incompatível", query_mode="structured"),
                ]
            }
        )
        self.assertEqual(svc.lookup("Rua Oscar Freire", 1250).status, LookupStatus.ENDERECO_NAO_LOCALIZADO)

    def test_selects_plausible_inside_candidate_instead_of_first_outside_one(self) -> None:
        svc, _ = service(
            {
                query_key(): [
                    candidate(latitude=-23.5, longitude=-46.5, rank=1),
                    candidate(rank=2),
                ]
            }
        )
        result = svc.lookup("Rua Oscar Freire", 1250.0)
        self.assertEqual(result.status, LookupStatus.CONFIRMADO)
        self.assertEqual(result.candidate.original_rank, 2)  # type: ignore[union-attr]
        self.assertEqual(result.normalized_house_number, "1250")
        self.assertEqual(result.fiscal, NOT_CONFIGURED)
        self.assertEqual(result.precision, GeocodingPrecision.ALTA)

    def test_status_precedence_for_numbers_precision_boundary_and_outside(self) -> None:
        scenarios = {
            "without_number": (candidate(number=None), LookupStatus.CONFERIR),
            "street_level": (candidate(match_level="street", match_type="centroid"), LookupStatus.CONFERIR),
            "near_boundary": (candidate(longitude=-46.69999), LookupStatus.PROXIMO_DA_DIVISA),
            "outside": (candidate(latitude=-23.5, longitude=-46.5), LookupStatus.FORA_DA_UGR),
        }
        for name, (returned, expected) in scenarios.items():
            with self.subTest(name=name):
                svc, _ = service({query_key(): [returned]})
                self.assertEqual(svc.lookup("Rua Oscar Freire", 1250).status, expected)

    def test_incompatible_empty_and_provider_failure_have_distinct_statuses(self) -> None:
        incompatible = candidate(road="Rua da Consolação")
        cases = {
            "incompatible": ([incompatible], LookupStatus.ENDERECO_NAO_LOCALIZADO),
            "empty": ([], LookupStatus.ENDERECO_NAO_LOCALIZADO),
            "failure": (GeocodingTemporaryError("sem rede"), LookupStatus.ERRO_GEOCODIFICACAO),
        }
        for name, (response, expected) in cases.items():
            with self.subTest(name=name):
                svc, _ = service({query_key(): response})
                self.assertEqual(svc.lookup("Rua Oscar Freire", 1250).status, expected)
        svc, _ = service({})
        self.assertEqual(svc.lookup("", 1250).status, LookupStatus.DADOS_INVALIDOS)

    def test_precision_mapping_is_conservative(self) -> None:
        self.assertEqual(classify_precision(candidate(), True), GeocodingPrecision.ALTA)
        self.assertEqual(
            classify_precision(candidate(match_code="fallback", match_type="interpolated"), True),
            GeocodingPrecision.MEDIA,
        )
        self.assertEqual(
            classify_precision(candidate(match_code="approximate", match_type="centroid", match_level="street"), False),
            GeocodingPrecision.BAIXA,
        )


class BatchLookupTests(unittest.TestCase):
    def test_summary_exposes_real_request_mode_counters(self) -> None:
        summary = BatchRunSummary(
            processed=7,
            statuses=Counter(),
            areas=Counter(),
            geocoding_calls=11,
            reused_results=2,
            structured_requests=7,
            freeform_requests=4,
            geoapify_requests=7,
            geoapify_candidates_returned=9,
            geoapify_candidates_usable=5,
            geoapify_candidates_used=4,
            locationiq_fallbacks=2,
            locationiq_requests=4,
        )
        rendered = format_summary(summary)
        self.assertIn("Endereços processados: 7", rendered)
        self.assertIn("Requests totais dos providers: 11", rendered)
        self.assertIn("LocationIQ structured requests: 7", rendered)
        self.assertIn("LocationIQ free-form fallback requests: 4", rendered)
        self.assertIn("Geoapify candidatos retornados: 9", rendered)
        self.assertIn("Geoapify candidatos utilizáveis: 5", rendered)
        self.assertIn("LocationIQ fallbacks: 2", rendered)

    def _service_for_good_candidate(self) -> tuple[AddressLookupService, MockGeocoder]:
        return service({query_key(): [candidate()]})

    def test_csv_aliases_deduplication_and_hyperlink_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "demandas.csv"
            csv_path.write_text(
                "Processo;Endereço;Nº;Polo\nP-1;Rua Oscar Freire;1250;A\nP-2;Rua Oscar Freire;1250.0;B\n",
                encoding="utf-8-sig",
            )
            source = read_input(csv_path)
            street = resolve_column(source.headers, STREET_ALIASES, None, "logradouro")
            number = resolve_column(source.headers, NUMBER_ALIASES, None, "altura")
            svc, mock = self._service_for_good_candidate()
            results, summary = BatchProcessor(svc, progress=None).process(
                source, street_column=street, number_column=number
            )
            output = root / "resultado.xlsx"
            write_output(csv_path, source, results, output)
            self.assertEqual(mock.call_count, 1)
            self.assertEqual(summary.geocoding_calls, 1)
            self.assertEqual(summary.reused_results, 1)
            workbook = load_workbook(output)
            worksheet = workbook.active
            headers = [cell.value for cell in worksheet[1]]
            self.assertEqual(headers[:4], ["Processo", "Endereço", "Nº", "Polo"])
            self.assertIn("Status", headers)
            self.assertEqual(worksheet.cell(2, 1).value, "P-1")
            maps_column = headers.index("Link Google Maps") + 1
            self.assertTrue(str(worksheet.cell(2, maps_column).value).startswith("https://www.google.com/maps/search/"))
            self.assertEqual(worksheet.cell(2, maps_column).hyperlink.target, worksheet.cell(2, maps_column).value)
            workbook.close()

    def test_xlsx_is_preserved_and_float_height_is_sent_as_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "demandas.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["Processo", "Logradouro", "Altura", "Observação"])
            worksheet.append([77, "Rua Oscar Freire", 1250.0, "manter"])
            workbook.save(source_path)
            workbook.close()
            source = read_input(source_path)
            svc, mock = self._service_for_good_candidate()
            results, _ = BatchProcessor(svc, progress=None).process(
                source, street_column="Logradouro", number_column="Altura"
            )
            output = root / "demandas_geojardins.xlsx"
            write_output(source_path, source, results, output)
            self.assertEqual(mock.queries[0].house_number, "1250")
            rendered = load_workbook(output)
            worksheet = rendered.active
            self.assertEqual(worksheet.cell(2, 1).value, 77)
            self.assertEqual(worksheet.cell(2, 4).value, "manter")
            header_values = [cell.value for cell in worksheet[1]]
            self.assertIn("Motivo Status", header_values)
            rendered.close()

    def test_txt_invalid_line_and_provider_failure_do_not_stop_lot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "demandas.txt"
            path.write_text("Rua Oscar Freire;1250\nlinha ambígua, 2, 3\nRua Oscar Freire;99\n", encoding="utf-8")
            source = read_input(path)
            svc, _ = service(
                {
                    query_key("Rua Oscar Freire", "1250"): [candidate()],
                    query_key("Rua Oscar Freire", "99"): GeocodingTemporaryError("sem rede"),
                }
            )
            results, summary = BatchProcessor(svc, progress=None).process(
                source, street_column="Logradouro", number_column="Altura"
            )
            self.assertEqual([item.result.status for item in results], [
                LookupStatus.CONFIRMADO,
                LookupStatus.DADOS_INVALIDOS,
                LookupStatus.ERRO_GEOCODIFICACAO,
            ])
            self.assertEqual(summary.processed, 3)

    def test_ambiguous_headers_require_explicit_mapping(self) -> None:
        source = InputRows(("Rua", "Via", "Altura"), tuple())
        with self.assertRaises(BatchInputError):
            resolve_column(source.headers, STREET_ALIASES, None, "logradouro")
        self.assertEqual(resolve_column(source.headers, STREET_ALIASES, "Via", "logradouro"), "Via")


class BatchInputDetectionTests(unittest.TestCase):
    def _xlsx(self, rows: list[list[object]]) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(temporary, ignore_errors=True))
        path = temporary / "input.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        workbook.close()
        return path

    def test_named_two_column_xlsx_uses_aliases(self) -> None:
        source = read_input(self._xlsx([["Logradouro", "Altura"], ["Rua Augusta", 2770]]))
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("Logradouro", "Altura"))
        self.assertFalse(mapping.inferred)

    def test_generic_two_column_xlsx_infers_from_content(self) -> None:
        source = read_input(self._xlsx([["A", "B"], ["Rua Augusta", 2770]]))
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("A", "B"))
        self.assertTrue(mapping.inferred)

    def test_headerless_xlsx_keeps_first_row_and_infers(self) -> None:
        source = read_input(self._xlsx([["Rua Augusta", 2770], ["Rua Oscar Freire", 1250]]))
        self.assertFalse(source.has_header)
        self.assertEqual(len(source.rows), 2)
        self.assertEqual(source.rows[0]["Coluna 1"], "Rua Augusta")
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("Coluna 1", "Coluna 2"))

    def test_headerless_inverted_xlsx_infers_from_content(self) -> None:
        source = read_input(self._xlsx([[2770, "Rua Augusta"], [1250, "Rua Oscar Freire"]]))
        self.assertFalse(source.has_header)
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("Coluna 2", "Coluna 1"))

    def test_street_names_containing_digits_are_not_inferred_as_heights(self) -> None:
        source = read_paste_input(
            "Rua 13 de Maio\t120\nRua 24 de Maio\t300\nRua dos Pinheiros\t950"
        )
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("Coluna 1", "Coluna 2"))

    def test_inverted_columns_keep_digit_street_names_as_streets(self) -> None:
        source = read_paste_input(
            "120\tRua 13 de Maio\n300\tRua 24 de Maio\n950\tRua dos Pinheiros"
        )
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("Coluna 2", "Coluna 1"))

    def test_one_known_header_infers_the_other_column(self) -> None:
        source = read_input(self._xlsx([["ENDEREÇO", "XYZ"], ["Rua Augusta", 2770]]))
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("ENDEREÇO", "XYZ"))

    def test_numeric_and_textual_ambiguities_require_manual_mapping(self) -> None:
        numeric = read_input(self._xlsx([["A", "B"], [10, 20], [30, 40]]))
        textual = read_input(self._xlsx([["A", "B"], ["Rua Augusta", "Urgente"], ["Rua Oscar Freire", "Revisar"]]))
        with self.assertRaises(BatchInputError):
            resolve_batch_columns(numeric)
        with self.assertRaises(BatchInputError):
            resolve_batch_columns(textual)

    def test_paste_tab_header_and_blank_lines_share_the_same_table_shape(self) -> None:
        source = read_paste_input("Logradouro\tAltura\n\nRua Augusta\t2770\nRua Oscar Freire\t1250\n")
        self.assertTrue(source.has_header)
        self.assertEqual(len(source.rows), 2)
        self.assertEqual(resolve_batch_columns(source).number_column, "Altura")

    def test_paste_without_header_and_inverted_columns(self) -> None:
        source = read_paste_input("2770\tRua Augusta\n1250\tRua Oscar Freire")
        self.assertFalse(source.has_header)
        mapping = resolve_batch_columns(source)
        self.assertEqual((mapping.street_column, mapping.number_column), ("Coluna 2", "Coluna 1"))

    def test_paste_semicolon_and_decimal_height_are_supported(self) -> None:
        source = read_paste_input("Rua Augusta;1250.0\nRua Oscar Freire;1250 ")
        mapping = resolve_batch_columns(source)
        self.assertEqual(mapping.street_column, "Coluna 1")
        self.assertEqual(mapping.number_column, "Coluna 2")

    def test_paste_uses_the_same_processing_and_export_pipeline(self) -> None:
        source = read_paste_input("Rua Oscar Freire\t1250.0\nRua Oscar Freire\t1250")
        svc, mock = service({query_key(): [candidate()]})
        output = Path(tempfile.mkdtemp()) / "paste-result.xlsx"
        self.addCleanup(lambda: __import__("shutil").rmtree(output.parent, ignore_errors=True))
        results, summary, destination = process_batch_source(
            source,
            service=svc,
            output_path=output,
            street_column="Coluna 1",
            number_column="Coluna 2",
            progress=None,
        )
        self.assertEqual(destination, output)
        self.assertEqual(len(results), 2)
        self.assertEqual(summary.reused_results, 1)
        self.assertEqual(mock.call_count, 1)
        workbook = load_workbook(output)
        self.assertEqual([cell.value for cell in workbook.active[1]][:2], ["Coluna 1", "Coluna 2"])
        self.assertEqual(workbook.active.cell(2, 2).value, "1250.0")
        workbook.close()

    def test_paste_export_path_is_timestamped_and_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_data = Path(temporary)
            fixed_time = datetime(2026, 9, 4, 11, 25, 30)
            with patch("geojardins.batch_lookup.local_app_data_dir", return_value=app_data):
                first = build_paste_export_path(fixed_time)
                self.assertEqual(first.parent, app_data / "exports")
                self.assertEqual(first.name, "lote_colado_2026-09-04_112530_geojardins.xlsx")

                first.write_text("first batch", encoding="utf-8")
                second = build_paste_export_path(fixed_time)

            self.assertEqual(second.name, "lote_colado_2026-09-04_112530_geojardins_2.xlsx")
            self.assertFalse(second.exists())
            self.assertEqual(first.read_text(encoding="utf-8"), "first batch")
