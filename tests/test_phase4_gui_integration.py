from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from geojardins.address_lookup import AddressLookupResult, GeocodingPrecision, LookupFlag, LookupStatus
from geojardins.address_normalizer import QueryType
from geojardins.classification import GeographicClassification
from geojardins.confidence import ConfidenceAssessment, ConfidenceLevel
from geojardins.geocoding.base import GeocodingConfigurationError
from geojardins.geocoding.models import GeocodeCandidate
from geojardins.gui.views.batch_lookup import BatchLookupView, BatchTableModel
from geojardins.gui.views.individual_lookup import IndividualLookupView
from geojardins.gui.widgets.result_panel import ResultPanel
from geojardins.gui.workers.lookup_worker import LookupWorker
from geojardins.runtime import geocoding_cache_path, resource_path


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication(["GeoJardins-phase4", "-platform", "offscreen"])


class FakeService:
    def lookup(self, street: str, number: str) -> AddressLookupResult:
        return backend_result(original_street=street, original_house_number=number)


def backend_result(
    *,
    status: LookupStatus = LookupStatus.CONFERIR,
    original_street: str = "Rua Teste",
    original_house_number: str = "100",
    fiscal: str = "NÃO CONFIGURADO",
) -> AddressLookupResult:
    candidate = GeocodeCandidate(
        latitude=-23.595,
        longitude=-46.695,
        display_name="Rua Teste, 100, São Paulo",
        house_number="100",
        road="Rua Teste",
        city="São Paulo",
        state="SP",
        country="Brasil",
        country_code="br",
        provider="geoapify",
        raw_id="test-result",
        result_type="building",
        match_type="full_match",
        confidence=0.5,
        confidence_street_level=1.0,
        confidence_building_level=0.0,
    )
    confidence = ConfidenceAssessment(
        ConfidenceLevel.MEDIUM,
        7,
        ("logradouro compatível",),
        ("outro candidato forte contradiz o resultado selecionado",),
        "Confiança intermediária; promoção bloqueada por conflito real.",
        False,
        ("confidence_blocked_provider_conflict",),
    )
    geography = GeographicClassification(True, "Tomé_2", ("Tomé_2",), 72.5, False)
    return AddressLookupResult(
        original_street=original_street,
        original_house_number=original_house_number,
        normalized_house_number="100",
        normalized_street="RUA TESTE",
        query_type=QueryType.ADDRESS,
        status=status,
        reason="Providers fortes divergem espacialmente; conferir antes de confirmar.",
        candidate=candidate,
        geography=geography,
        fiscal=fiscal,
        precision=GeocodingPrecision.BAIXA,
        flags=(LookupFlag.BAIXA_PRECISAO, LookupFlag.PROVIDERS_DIVERGENTES),
        number_returned="100",
        confidence=confidence,
        suggested_correction="RUA TESTE",
        selection_reason="Candidato territorialmente preferido.",
    )


def test_result_panel_uses_backend_reason_and_exposes_backend_details(qapp: QApplication) -> None:
    result = backend_result()
    panel = ResultPanel()
    panel.show_result(result)

    texts = [label.text() for label in panel.findChildren(QLabel)]
    assert result.reason in texts
    assert "Evidências da decisão" in texts
    details = dict(ResultPanel._detail_items(result))
    assert details["Motivo da decisao"] == result.confidence.decision_reason  # type: ignore[union-attr]
    assert details["Flags"] == "BAIXA_PRECISAO; PROVIDERS_DIVERGENTES"
    assert details["Confianca final"] == "MEDIUM"


def test_result_panel_marks_candidate_fiscal_without_claiming_confirmation(qapp: QApplication) -> None:
    panel = ResultPanel()
    panel.show_result(backend_result(fiscal="NÃO CONFIGURADO"))

    texts = [label.text() for label in panel.findChildren(QLabel)]
    assert "AREA CANDIDATA" in texts
    assert "FISCAL CANDIDATO" in texts
    assert "NÃO CONFIGURADO" in texts


def test_intersection_can_start_without_number(qapp: QApplication) -> None:
    view = IndividualLookupView(lambda: FakeService())  # type: ignore[arg-type]
    view.street.setText("Rua Teste x Avenida Exemplo")
    view.number.clear()
    view.start_lookup()

    assert view.worker is not None
    assert view.worker.wait(3000)
    qapp.processEvents()
    assert view.validation.isHidden()


def test_missing_geoapify_credential_has_friendly_worker_message(qapp: QApplication) -> None:
    seen: list[str] = []

    def missing_service() -> object:
        raise GeocodingConfigurationError("secret technical detail")

    worker = LookupWorker("Rua Teste", "100", missing_service)  # type: ignore[arg-type]
    worker.failed.connect(seen.append)
    worker.start()
    assert worker.wait(3000)
    qapp.processEvents()
    assert seen == ["Geoapify nao configurado. Abra Configuracoes e informe a chave de API."]
    assert "secret" not in seen[0]


def test_batch_progress_uses_backend_position_message(qapp: QApplication) -> None:
    view = BatchLookupView(lambda: FakeService())  # type: ignore[arg-type]
    view._set_progress("[consulta 3/7] Rua Teste, 100 -> Tome_2 / CONFERIR")

    assert view.progress.minimum() == 0
    assert view.progress.maximum() == 7
    assert view.progress.value() == 3


def test_batch_table_keeps_not_configured_fiscal_exact(qapp: QApplication) -> None:
    from geojardins.batch_lookup import BatchRowResult

    model = BatchTableModel((BatchRowResult({"Logradouro": "Rua Teste"}, backend_result()),))
    assert model.data(model.index(0, 3)) == "NÃO CONFIGURADO"


def test_runtime_cache_path_is_central_and_not_cwd_relative() -> None:
    assert geocoding_cache_path() == resource_path("data", "cache", "geocoding_cache.sqlite")
    assert geocoding_cache_path().parent.is_dir()


def test_unknown_status_badge_remains_safe(qapp: QApplication) -> None:
    from geojardins.gui.widgets.status_badge import StatusBadge

    badge = StatusBadge("FUTURO_STATUS")
    assert badge.text() == "FUTURO STATUS"
