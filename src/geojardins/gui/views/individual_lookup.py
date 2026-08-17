from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ...address_lookup import AddressLookupResult, AddressLookupService
from ..theme import PALETTE, SIZES, SPACING, TYPE
from ..widgets.common import PageHeader, Toast
from ..widgets.result_panel import ResultPanel
from ..workers.lookup_worker import LookupWorker


class ResponsiveLookupBar(QWidget):
    def __init__(self, street: QLineEdit, number: QLineEdit, button: QPushButton, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.street = street
        self.number = number
        self.button = button
        self.street_label = QLabel("Logradouro")
        self.number_label = QLabel("Altura numérica")
        for label in (self.street_label, self.number_label):
            label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['label']}px; font-weight:600;")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(SPACING["sm"])
        self.grid.setVerticalSpacing(SPACING["xs"])
        self._compact = None
        self._relayout()

    def _relayout(self) -> None:
        compact = self.width() < 720
        if compact == self._compact:
            return
        while self.grid.count():
            self.grid.takeAt(0)
        if compact:
            self.grid.addWidget(self.street_label, 0, 0, 1, 2)
            self.grid.addWidget(self.street, 1, 0, 1, 2)
            self.grid.addWidget(self.number_label, 2, 0)
            self.grid.addWidget(self.number, 3, 0)
            self.grid.addWidget(self.button, 3, 1)
        else:
            self.grid.addWidget(self.street_label, 0, 0)
            self.grid.addWidget(self.number_label, 0, 1)
            self.grid.addWidget(self.street, 1, 0)
            self.grid.addWidget(self.number, 1, 1)
            self.grid.addWidget(self.button, 1, 2)
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnMinimumWidth(1, 152)
        self._compact = compact

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().resizeEvent(event)
        self._relayout()


class IndividualLookupView(QWidget):
    result_ready = Signal(object)

    def __init__(self, service_factory: Callable[[], AddressLookupService], parent=None) -> None:
        super().__init__(parent)
        self.service_factory = service_factory
        self.worker: LookupWorker | None = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SPACING["xxl"], 28, SPACING["xxl"], SPACING["xxl"])
        content = QWidget()
        content.setMinimumWidth(900)
        content.setMaximumWidth(SIZES["content_standard"])
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["xl"])
        outer.addStretch(1)
        outer.addWidget(content)
        outer.addStretch(1)
        layout.addWidget(PageHeader("Consulta individual", "Localize uma demanda e identifique a área operacional responsável."))

        self.street = QLineEdit()
        self.street.setPlaceholderText("Ex.: Rua Oscar Freire")
        self.street.setAccessibleName("Logradouro")
        self.number = QLineEdit()
        self.number.setPlaceholderText("Ex.: 1250")
        self.number.setMaximumWidth(170)
        self.number.setPlaceholderText("Ex.: 1250 (opcional em cruzamentos)")
        self.number.setAccessibleName("Altura ou numero")
        self.number.setAccessibleName("Altura numérica")
        self.locate = QPushButton("Localizar demanda")
        self.locate.setProperty("role", "primary")
        self.locate.setAccessibleName("Localizar demanda")
        self.locate.clicked.connect(self.start_lookup)
        self.form = ResponsiveLookupBar(self.street, self.number, self.locate)
        self.form.number_label.setText("Altura/n\u00famero")
        self.number.setAccessibleName("Altura ou n\u00famero")
        layout.addWidget(self.form)
        self.validation = QLabel("")
        self.validation.setStyleSheet("color:#A74342; font-size:11px;")
        self.validation.hide()
        layout.addWidget(self.validation)
        self.feedback = Toast(self)
        self.result_panel = ResultPanel()
        self.result_panel.edit_requested.connect(self.street.setFocus)
        self.result_panel.toast_requested.connect(lambda message, kind: self.feedback.show_message(message, kind=kind))
        layout.addWidget(self.result_panel, 1)
        self.number.returnPressed.connect(self.start_lookup)
        self.street.returnPressed.connect(self.start_lookup)
        self.setTabOrder(self.street, self.number)
        self.setTabOrder(self.number, self.locate)

    def start_lookup(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.street.text().strip():
            self.validation.setText("Informe o logradouro e a altura numérica para continuar.")
            self.validation.show()
            self.validation.setText("Informe o logradouro para continuar.")
            self.street.setFocus()
            return
        self.validation.hide()
        self.locate.setEnabled(False)
        self.locate.setText("Localizando…")
        self.worker = LookupWorker(self.street.text(), self.number.text(), self.service_factory, self)
        self.worker.completed.connect(self._show_result)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._finish)
        self.worker.start()

    def _show_result(self, result: AddressLookupResult) -> None:
        self.result_panel.show_result(result)
        self.result_ready.emit(result)

    def _show_error(self, message: str) -> None:
        self.feedback.show_message(message, kind="error", duration_ms=6500)

    def _finish(self) -> None:
        self.locate.setEnabled(True)
        self.locate.setText("Localizar demanda")
