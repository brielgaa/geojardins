from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ..address_lookup import AddressLookupService, build_geocoding_service
from ..user_config import UserConfigStore
from .views.batch_lookup import BatchLookupView
from .views.dashboard import DashboardView
from .views.fiscais import FiscaisView
from .views.individual_lookup import IndividualLookupView
from .views.settings import SettingsView
from .widgets.sidebar import Sidebar
from .widgets.social_link import asset_path
from .window_state import restore_window, save_window
from .windows_titlebar import apply_native_titlebar


class MainWindow(QMainWindow):
    def __init__(self, store: UserConfigStore, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.store = store
        self.session = Counter()
        self.setWindowTitle("GeoJardins · UGR Jardins")
        self.setWindowIcon(QIcon(str(asset_path("geojardins.ico"))))
        self.setMinimumSize(1200, 720)
        self._restored_window = restore_window(self)
        if not self._restored_window:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.resize(min(1440, max(1200, screen.width() - 72)), min(900, max(720, screen.height() - 88)))

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.sidebar = Sidebar()
        content.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        self.dashboard = DashboardView()
        self.individual = IndividualLookupView(self.create_service)
        self.batch = BatchLookupView(self.create_service)
        self.fiscais = FiscaisView(store)
        self.settings = SettingsView(store)
        for page in (self.dashboard, self.individual, self.batch, self.fiscais, self.settings):
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)
        root.addLayout(content, 1)
        self.setCentralWidget(central)

        self.sidebar.page_requested.connect(self.show_page)
        self.dashboard.configure_requested.connect(lambda: self.show_page(4))
        self.dashboard.lookup_requested.connect(lambda: self.show_page(1))
        self.dashboard.batch_requested.connect(lambda: self.show_page(2))
        self.individual.result_ready.connect(self.record_result)
        self.batch.batch_ready.connect(self.record_batch)
        self.settings.saved.connect(self.refresh_provider_status)
        self.fiscais.saved.connect(self.refresh_provider_status)
        self.refresh_provider_status()
        self._shortcuts()

    def create_service(self) -> AddressLookupService:
        credentials = self.store.credentials()
        return build_geocoding_service(
            settings=self.store.settings(),
            fiscal_assignments=self.store.fiscais(),
            geoapify_api_key=credentials["geoapify"],
            locationiq_token=credentials["locationiq"],
        )

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.sidebar.select(index)
        if index == 4:
            self.settings.refresh_status()
        if index == 1:
            self.individual.street.setFocus()

    def record_result(self, result: object) -> None:
        status = getattr(getattr(result, "status", None), "value", None)
        if status:
            self.session[status] += 1
            address = getattr(result, "formatted_address", None) or getattr(result, "original_street", "Consulta concluída")
            self.dashboard.set_last_activity(f"Última consulta · {address}")
        self.dashboard.set_session(self.session)

    def record_batch(self, summary: object) -> None:
        statuses = getattr(summary, "statuses", {})
        self.session.update(statuses)
        self.dashboard.set_last_activity(f"Último lote · {getattr(summary, 'processed', 0)} demandas processadas")
        self.dashboard.set_session(self.session)

    def refresh_provider_status(self) -> None:
        credentials = self.store.credentials()
        self.dashboard.set_provider_status(bool(credentials["geoapify"]), bool(credentials["locationiq"]))

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.show_page(1))
        QShortcut(QKeySequence("Ctrl+O"), self, lambda: (self.show_page(2), self.batch.choose_file()))
        QShortcut(QKeySequence("Ctrl+,"), self, lambda: self.show_page(4))
        QShortcut(QKeySequence("Ctrl+Shift+F"), self, lambda: self.show_page(3))

    def _apply_titlebar(self) -> None:
        apply_native_titlebar(int(self.winId()), caption="#1D4939", text="#FFFFFF", border="#1D4939")

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_titlebar)

    def event(self, event) -> bool:  # type: ignore[no-untyped-def]
        if event.type() in (QEvent.Type.WinIdChange, QEvent.Type.WindowActivate):
            QTimer.singleShot(0, self._apply_titlebar)
        return super().event(event)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        active_workers = (
            worker
            for worker in (self.individual.worker, self.batch.worker)
            if worker is not None and worker.isRunning()
        )
        if any(active_workers):
            QMessageBox.information(
                self,
                "Processamento em andamento",
                "Aguarde a conclusao da consulta ou do lote antes de fechar o GeoJardins.",
            )
            event.ignore()
            return
        save_window(self)
        super().closeEvent(event)
