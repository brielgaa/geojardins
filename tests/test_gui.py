from __future__ import annotations

from collections import Counter
from pathlib import Path
from threading import Event

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QShortcut
from PySide6.QtWidgets import QApplication, QLabel

from geojardins.address_lookup import AddressLookupResult, GeocodingPrecision, LookupStatus
from geojardins.batch_lookup import BatchRowResult, BatchRunSummary, InputRows
from geojardins.classification import GeographicClassification
from geojardins.gui.main_window import MainWindow
from geojardins.gui.views.batch_lookup import BatchDropZone, BatchFilterProxy, BatchLookupView, BatchTableModel
from geojardins.gui.widgets.result_panel import ResultPanel
from geojardins.gui.views.fiscais import FiscaisView
from geojardins.gui.views.individual_lookup import IndividualLookupView
from geojardins.gui.views.settings import SettingsView
from geojardins.gui.widgets.operational_map import OperationalMapView
from geojardins.gui.widgets.status_badge import StatusBadge
from geojardins.gui.workers.batch_worker import BatchWorker
from geojardins.gui.widgets.social_link import GITHUB_URL, LINKEDIN_URL, SocialLinkButton, asset_path
from geojardins.gui.window_state import restore_window, save_window
from geojardins.gui.windows_titlebar import apply_native_titlebar, colorref
from geojardins.runtime import local_app_data_dir, resource_path
from geojardins.settings import Settings
from geojardins.user_config import UserConfigStore


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication(["GeoJardins-tests", "-platform", "offscreen"])


class FakeSecrets:
    available = True

    def protect(self, _value: str) -> str:
        return "protected-test-value"

    def unprotect(self, value: object) -> str | None:
        return "restored" if value == "protected-test-value" else None


class FakeService:
    def lookup(self, street: str, number: str) -> AddressLookupResult:
        return AddressLookupResult(
            original_street=street,
            original_house_number=number,
            normalized_house_number=number,
            status=LookupStatus.CONFIRMADO,
            reason="Resultado de teste.",
            fiscal="Fiscal de teste",
            precision=GeocodingPrecision.ALTA,
        )


class OfflineBatchService:
    def __init__(self, *, started: Event | None = None, release: Event | None = None) -> None:
        self.started = started
        self.release = release
        self.calls = 0
        self.geocoder = type("OfflineGeocoder", (), {"call_count": 0})()
        self.cache = None
        self.settings = Settings()

    def query_key(self, street: object, number: object) -> str:
        return f"{street}|{number}"

    def _invalid(self, street: object, number: object, reason: str) -> AddressLookupResult:
        return AddressLookupResult(
            original_street=str(street or ""),
            original_house_number=number,
            normalized_house_number=None,
            status=LookupStatus.DADOS_INVALIDOS,
            reason=reason,
        )

    def lookup(self, street: object, number: object, *, force_fallback: bool = False) -> AddressLookupResult:
        self.calls += 1
        self.geocoder.call_count += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(3000)
        return AddressLookupResult(
            original_street=str(street or ""),
            original_house_number=number,
            normalized_house_number=str(number or ""),
            status=LookupStatus.CONFIRMADO,
            reason="Resultado offline de teste.",
        )


class FakeDropEvent:
    def __init__(self, path: Path) -> None:
        self.mime = QMimeData()
        self.mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.accepted = False
        self.ignored = False

    def mimeData(self) -> QMimeData:
        return self.mime

    def acceptProposedAction(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


def result(status: LookupStatus = LookupStatus.CONFIRMADO, area: str | None = "Tomé_4") -> AddressLookupResult:
    return AddressLookupResult(
        original_street="Rua Oscar Freire",
        original_house_number="1250",
        normalized_house_number="1250",
        status=status,
        reason="Teste.",
        geography=GeographicClassification(True, area, (area,) if area else (), 42.0 if area else None, False),
        fiscal="Ana" if area else "NÃO CONFIGURADO",
        precision=GeocodingPrecision.ALTA,
    )


def test_resource_paths_resolve_runtime_inputs() -> None:
    assert resource_path("data", "processed", "areas_jardins_partition.geojson").is_file()
    assert resource_path("data", "processed", "ugr_jardins_boundary_partition.geojson").is_file()
    assert resource_path("config", "settings.json").is_file()
    assert resource_path("config", "fiscais.json").is_file()
    assert asset_path("github.svg").is_file()
    assert asset_path("linkedin.svg").is_file()
    assert asset_path("operational_map.png").is_file()
    assert asset_path("operational_map_overlays.json").is_file()
    assert asset_path("geojardins.ico").is_file()


def test_localappdata_path_is_user_writable(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert local_app_data_dir() == tmp_path / "GeoJardins"


def test_user_config_keeps_plain_credentials_out_of_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets())
    assert store.save_credentials(geoapify="not-a-real-key")
    assert "not-a-real-key" not in (tmp_path / "config.json").read_text(encoding="utf-8")
    assert store.credentials()["geoapify"] == "restored"


def test_user_config_persists_only_small_batch_preferences(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets())
    store.save_preferences(batch_mode="paste", batch_status_filter="CONFERIR", last_import_dir=str(tmp_path))
    restored = UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets())

    assert restored.preferences() == {
        "batch_mode": "paste",
        "batch_status_filter": "CONFERIR",
        "last_import_dir": str(tmp_path),
    }
    raw = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert "planilha" not in raw
    assert "Rua" not in raw


def test_badge_always_exposes_status_text(qapp: QApplication) -> None:
    badge = StatusBadge("PROXIMO_DA_DIVISA")
    assert "PROXIMO DA DIVISA" in badge.text()
    assert "background" in badge.styleSheet()


def test_main_window_navigation_and_missing_credentials_are_safe(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets()))
    assert window.stack.count() == 5
    assert not window.dashboard.banner.isHidden()
    window.show_page(2)
    assert window.stack.currentIndex() == 2
    window.show_page(4)
    assert window.stack.currentIndex() == 4


def test_window_shortcuts_and_native_titlebar_fallback(qapp: QApplication, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets()))
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in window.findChildren(QShortcut)}
    shortcuts["Ctrl+L"].activated.emit()
    assert window.stack.currentIndex() == 1
    shortcuts["Ctrl+,"] .activated.emit()
    assert window.stack.currentIndex() == 4
    shortcuts["Ctrl+Shift+F"].activated.emit()
    assert window.stack.currentIndex() == 3
    assert "Ctrl+O" in shortcuts
    monkeypatch.setattr("geojardins.gui.windows_titlebar.os.name", "posix")
    assert not apply_native_titlebar(1, caption="#1D4939", text="#FFFFFF", border="#1D4939")
    assert colorref("#1D4939") == 0x0039491D


def test_individual_lookup_uses_worker_without_freezing(qapp: QApplication) -> None:
    view = IndividualLookupView(lambda: FakeService())  # type: ignore[arg-type]
    seen: list[AddressLookupResult] = []
    view.result_ready.connect(seen.append)
    view.street.setText("Rua Oscar Freire")
    view.number.setText("1250")
    view.start_lookup()
    assert view.worker is not None
    assert view.worker.wait(3000)
    qapp.processEvents()
    assert seen and seen[0].status is LookupStatus.CONFIRMADO
    assert view.result_panel.card is not None


def test_lookup_and_batch_empty_states_are_inline(qapp: QApplication) -> None:
    lookup = IndividualLookupView(lambda: FakeService())  # type: ignore[arg-type]
    lookup.start_lookup()
    assert not lookup.validation.isHidden()
    assert not lookup.result_panel.empty.isHidden()
    from geojardins.gui.views.batch_lookup import BatchLookupView

    batch = BatchLookupView(lambda: FakeService())  # type: ignore[arg-type]
    assert not batch.drop_zone.isHidden()
    assert batch.results.isHidden()
    batch.set_source_path(Path("invalido.pdf"))
    assert not batch.invalid_file.isHidden()


def test_batch_table_and_filters(qapp: QApplication) -> None:
    rows = (
        BatchRowResult({"Logradouro": "Rua Oscar Freire"}, result()),
        BatchRowResult({"Logradouro": "Rua Teste"}, result(LookupStatus.CONFERIR, "Tomé_1")),
    )
    model = BatchTableModel(rows)
    proxy = BatchFilterProxy()
    proxy.setSourceModel(model)
    proxy.status = "CONFIRMADO"
    proxy.beginFilterChange()
    proxy.endFilterChange()
    assert proxy.rowCount() == 1
    proxy.status = "Todos"
    proxy.area = "Tomé_1"
    proxy.beginFilterChange()
    proxy.endFilterChange()
    assert proxy.rowCount() == 1
    assert model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole) == "CONFIRMADO"


def test_batch_paste_mode_previews_inferred_columns_and_allows_manual_mapping(qapp: QApplication) -> None:
    view = BatchLookupView(lambda: FakeService())  # type: ignore[arg-type]
    view.show_paste_mode()
    view.paste_editor.setPlainText("A\tB\n10\t20\n30\t40")
    qapp.processEvents()

    assert view.paste_source is not None
    assert view.paste_process.isEnabled() is False
    assert "selecione" in view.paste_status.text().casefold()
    assert view.paste_preview.rowCount() == 2

    view.paste_number_column.setCurrentIndex(1)
    qapp.processEvents()
    assert view.paste_column_mapping is not None
    assert view.paste_process.isEnabled()


def test_batch_drop_and_import_share_the_same_path_handler(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    view = BatchLookupView(lambda: FakeService())  # type: ignore[arg-type]
    for suffix in (".xlsx", ".csv", ".txt"):
        event = FakeDropEvent(tmp_path / f"input{suffix}")
        view.drop_zone.dropEvent(event)
        assert event.accepted
        assert view.source_path == tmp_path / f"input{suffix}"

    invalid = FakeDropEvent(tmp_path / "input.pdf")
    view.drop_zone.dropEvent(invalid)
    assert invalid.accepted
    assert view.source_path == tmp_path / "input.txt"
    assert not view.invalid_file.isHidden()


def test_batch_reprocess_actions_and_selected_detail_use_existing_rows(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    view = BatchLookupView(lambda: FakeService())  # type: ignore[arg-type]
    rows = (
        BatchRowResult({"Logradouro": "Rua A"}, result(LookupStatus.CONFERIR), 0),
        BatchRowResult({"Logradouro": "Rua B"}, result(), 1),
    )
    summary = BatchRunSummary(2, Counter({"CONFERIR": 1, "CONFIRMADO": 1}), Counter(), 0, 0, total_rows=2)
    view.paste_source = None
    view.set_source_path(tmp_path / "input.csv")
    view._set_results(rows, summary, tmp_path / "output.xlsx")

    assert view.reprocess_pending.isEnabled()
    row_for_source_zero = next(
        row
        for row in range(view.proxy.rowCount())
        if view.proxy.mapToSource(view.proxy.index(row, 0)).row() == 0
    )
    view.table.selectRow(row_for_source_zero)
    qapp.processEvents()
    assert view._selected_source_indices() == (0,)
    assert view.reprocess_selected.isEnabled()
    assert not view.detail_panel.isHidden()
    assert "Evidências da decisão" in [label.text() for label in view.detail_panel.findChildren(QLabel)]


def test_batch_view_restores_local_mode_and_status_preference(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets())
    store.save_preferences(batch_mode="paste", batch_status_filter="CONFERIR")

    view = BatchLookupView(lambda: FakeService(), store)  # type: ignore[arg-type]

    assert view.source_mode == "paste"
    assert not view.paste_panel.isHidden()
    assert view.status_filter.currentText() == "CONFERIR"


def test_batch_worker_cancel_before_first_query_finishes_with_valid_partial_xlsx(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    service = OfflineBatchService()
    source = InputRows(("Logradouro", "Altura"), ({"Logradouro": "Rua A", "Altura": "10"},))
    output = tmp_path / "cancelled.xlsx"
    completed: list[tuple[object, object, object]] = []
    failed: list[str] = []
    worker = BatchWorker(source, lambda: service, output_path=output)  # type: ignore[arg-type]
    worker.completed.connect(lambda *values: completed.append(values))
    worker.failed.connect(failed.append)
    worker.request_cancel()
    worker.start()
    assert worker.wait(3000)
    qapp.processEvents()

    assert not failed
    assert completed
    rows, summary, destination = completed[0]
    assert rows == ()
    assert summary.cancelled
    assert destination == output
    assert output.is_file()
    workbook = load_workbook(output)
    assert workbook.sheetnames == ["GeoJardins", "Resumo"]
    workbook.close()


def test_batch_view_cancel_button_is_only_active_while_worker_runs(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    started = Event()
    release = Event()
    service = OfflineBatchService(started=started, release=release)
    view = BatchLookupView(lambda: service)  # type: ignore[arg-type]
    source = InputRows(
        ("Logradouro", "Altura"),
        ({"Logradouro": "Rua A", "Altura": "10"}, {"Logradouro": "Rua B", "Altura": "20"}),
    )
    output = tmp_path / "view-cancelled.xlsx"

    assert not view.cancel_button.isEnabled()
    view._start_worker(source, output_path=output, street_column="Logradouro", number_column="Altura")
    assert started.wait(2000)
    assert view.cancel_button.isEnabled()
    view.request_cancel()
    assert not view.cancel_button.isEnabled()
    release.set()
    assert view.worker is not None
    assert view.worker.wait(3000)
    qapp.processEvents()

    assert not view.cancel_button.isEnabled()
    assert not view.processing.isVisible()
    assert output.is_file()


def test_dashboard_metrics_accept_session_counts(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets()))
    window.dashboard.set_session(Counter({"CONFIRMADO": 2, "CONFERIR": 1}))
    assert window.dashboard.metrics["Consultas"].value.text() == "3"
    assert window.dashboard.metrics["Confirmados"].value.text() == "2"


def test_fiscais_view_persists_assignments(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets())
    view = FiscaisView(store)
    view.inputs["Tomé_4"].setText("Equipe Oeste")
    view.save()
    assert store.fiscais()["Tomé_4"] == "Equipe Oeste"


def test_offline_operational_map_and_editor_selection(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    map_view = OperationalMapView()
    selected: list[str] = []
    map_view.area_selected.connect(selected.append)
    map_view.set_fiscais({"Tomé_4": "Equipe Oeste"})
    map_view.set_selected("Tomé_4", emit=True)
    assert len(map_view.areas) == 6
    assert map_view.areas["Tomé_4"].active
    assert "Equipe Oeste" in map_view.areas["Tomé_4"].toolTip()
    assert selected == ["Tomé_4"]
    view = FiscaisView(UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets()))
    view.select_area("Tomé_2")
    assert view.map.areas["Tomé_2"].active
    assert "859D4D" in view.cards["Tomé_2"].styleSheet()


def test_window_state_roundtrip_is_safe(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    source = MainWindow(UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets()))
    source.resize(1220, 760)
    source.move(30, 30)
    path = tmp_path / "window_state.json"
    save_window(source, path)
    restored = MainWindow(UserConfigStore(tmp_path / "restored.json", secrets=FakeSecrets()))
    assert restore_window(restored, path)
    assert restored.size().width() == 1220
    assert restored.size().height() == 760


def test_social_links_are_accessible_and_open_expected_urls(qapp: QApplication, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    github = SocialLinkButton("github", compact=True, dark_surface=True)
    linkedin = SocialLinkButton("linkedin")
    assert github.toolTip() == "GitHub — @brielgaa"
    assert linkedin.toolTip() == "LinkedIn — Gabriel da Silva Bitencourt"
    assert "GitHub" in github.accessibleName()
    assert "LinkedIn" in linkedin.accessibleName()
    github.click()
    linkedin.click()
    assert opened == [GITHUB_URL, LINKEDIN_URL]


def test_sidebar_and_about_expose_social_controls(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets())
    window = MainWindow(store)
    settings = window.settings
    assert window.sidebar.github_button.iconSize().width() == 16
    assert window.sidebar.linkedin_button.iconSize().height() == 16
    assert settings.github_button.text() == "brielgaa"
    assert settings.linkedin_button.text() == "gabrieldasilvabitencourt"


def test_settings_is_grouped_into_operation_providers_and_about(qapp: QApplication, tmp_path) -> None:  # type: ignore[no-untyped-def]
    view = SettingsView(UserConfigStore(tmp_path / "config.json", secrets=FakeSecrets()))
    labels = {label.text() for label in view.findChildren(QLabel)}
    assert {"Operação", "Provedores", "Sobre"}.issubset(labels)
    assert view.geoapify.echoMode().name == "Password"
    assert view.geoapify.text() == ""
