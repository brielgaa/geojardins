from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...address_lookup import AddressLookupService, LookupStatus
from ...batch_lookup import BatchRowResult, BatchRunSummary
from ..theme import PALETTE, SPACING, TYPE
from ..widgets.common import PageHeader, Toast
from ..widgets.social_link import svg_icon
from ..workers.batch_worker import BatchWorker


class BatchTableModel(QAbstractTableModel):
    COLUMNS = ("Logradouro", "Altura", "Área", "Fiscal", "Status", "Precisão", "Distância da divisa", "Provider")

    def __init__(self, rows: tuple[BatchRowResult, ...] = (), parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.rows = rows

    def set_rows(self, rows: tuple[BatchRowResult, ...]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[no-untyped-def]  # noqa: N802
        return self.COLUMNS[section] if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal else None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[no-untyped-def]
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row = self.rows[index.row()]
        result = row.result
        values = (
            row.source.get("Logradouro") or row.source.get("logradouro") or row.source.get("Endereço") or row.source.get("endereco") or result.original_street,
            result.normalized_house_number or result.original_house_number,
            result.area.replace("_", " ") if result.area else "—",
            result.fiscal if result.fiscal != "NÃO CONFIGURADO" else "Nenhum fiscal configurado",
            result.status.value,
            result.precision.value.title() if result.precision else "—",
            f"{result.distance_to_boundary_m:.1f} m" if result.distance_to_boundary_m is not None else "—",
            result.candidate.provider.upper() if result.candidate else "—",
        )
        values = (*values[:3], result.fiscal or "NÃO CONFIGURADO", *values[4:])
        return str(values[index.column()])


class BatchFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.status = "Todos"
        self.area = "Todas"
        self.fiscal = "Todos"
        self.search = ""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, BatchTableModel):
            return True
        row = model.rows[source_row]
        result = row.result
        if self.status != "Todos" and result.status.value != self.status:
            return False
        if self.area != "Todas" and result.area != self.area:
            return False
        if self.fiscal != "Todos" and result.fiscal != self.fiscal:
            return False
        if self.search and self.search.casefold() not in " ".join(str(value) for value in row.source.values()).casefold():
            return False
        return True


class StatusPillDelegate(QStyledItemDelegate):
    COLORS = {
        "CONFIRMADO": ("#E0F2E7", "#17643B"),
        "PROXIMO_DA_DIVISA": ("#FFF1CC", "#765A12"),
        "CONFERIR": ("#FDE8D7", "#9A4312"),
        "FORA_DA_UGR": ("#E8ECF0", "#495A67"),
    }

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "—")
        background, foreground = self.COLORS.get(text, ("#F9E3E5", "#9B2534"))
        painter.save()
        rect = option.rect.adjusted(7, 7, -7, -7)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text.replace("_", " "))
        painter.restore()


class BatchDropZone(QFrame):
    file_dropped = Signal(Path)
    browse_requested = Signal()

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(174)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(SPACING["sm"])
        icon = QLabel()
        icon.setPixmap(svg_icon("batch.svg", "#5C8064", 30).pixmap(30, 30))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("Solte a planilha aqui")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size:15px; font-weight:650; color:#244537;")
        self.note = QLabel("ou clique para selecionar · XLSX · CSV · TXT")
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        layout.addWidget(icon)
        layout.addWidget(self.title)
        layout.addWidget(self.note)
        self._set_state("idle")

    def _set_state(self, state: str) -> None:
        styles = {
            "idle": f"background:{PALETTE['surface']}; border:1px dashed #B9C9BB; border-radius:10px;",
            "hover": "background:#F0F7EF; border:2px dashed #61835C; border-radius:10px;",
            "invalid": "background:#FDF3F2; border:1px dashed #B85A55; border-radius:10px;",
        }
        self.setStyleSheet(styles[state])
        self.title.setText("Solte para importar" if state == "hover" else "Solte a planilha aqui")

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse_requested.emit()
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if event.mimeData().hasUrls():
            self._set_state("hover")
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._set_state("idle")
        event.accept()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        urls = event.mimeData().urls()
        path = Path(urls[0].toLocalFile()) if urls else None
        if path and path.suffix.casefold() in {".xlsx", ".csv", ".txt"}:
            self._set_state("idle")
            self.file_dropped.emit(path)
            event.acceptProposedAction()
            return
        self._set_state("invalid")
        event.ignore()


class BatchLookupView(QWidget):
    batch_ready = Signal(object)

    def __init__(self, service_factory: Callable[[], AddressLookupService], parent=None) -> None:
        super().__init__(parent)
        self.service_factory = service_factory
        self.source_path: Path | None = None
        self.output_path: Path | None = None
        self.worker: BatchWorker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["xxl"], 28, SPACING["xxl"], SPACING["xxl"])
        layout.setSpacing(SPACING["xl"])
        layout.addWidget(PageHeader("Processamento em lote", "Envie uma planilha; o arquivo original é preservado e o resultado é exportado em XLSX."))

        self.drop_zone = BatchDropZone()
        self.drop_zone.browse_requested.connect(self.choose_file)
        self.drop_zone.file_dropped.connect(self.set_source_path)
        layout.addWidget(self.drop_zone)
        self.invalid_file = QLabel("Use somente planilhas .xlsx, .csv ou .txt.")
        self.invalid_file.setStyleSheet(f"color:{PALETTE['danger']}; font-size:{TYPE['caption']}px;")
        self.invalid_file.hide()
        layout.addWidget(self.invalid_file)

        self.file_strip = QFrame()
        self.file_strip.setStyleSheet(f"background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:8px;")
        strip = QHBoxLayout(self.file_strip)
        strip.setContentsMargins(12, 8, 8, 8)
        file_icon = QLabel()
        file_icon.setPixmap(svg_icon("batch.svg", "#315B4B", 18).pixmap(18, 18))
        path_block = QVBoxLayout()
        path_block.setSpacing(1)
        self.path_label = QLabel("Nenhum arquivo selecionado")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setStyleSheet("color:#244537; font-size:13px; font-weight:600;")
        self.file_meta = QLabel()
        self.file_meta.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        path_block.addWidget(self.path_label)
        path_block.addWidget(self.file_meta)
        self.select_file = QPushButton("Alterar")
        self.select_file.setProperty("role", "ghost")
        self.select_file.clicked.connect(self.choose_file)
        self.process = QPushButton("Processar lote")
        self.process.setProperty("role", "primary")
        self.process.setEnabled(False)
        self.process.clicked.connect(self.start_batch)
        strip.addWidget(file_icon)
        strip.addLayout(path_block, 1)
        strip.addWidget(self.select_file)
        strip.addWidget(self.process)
        self.file_strip.hide()
        layout.addWidget(self.file_strip)

        self.feedback = Toast(self)
        self.processing = QFrame()
        self.processing.setStyleSheet(f"background:{PALETTE['surface_subtle']}; border-radius:8px;")
        progress_layout = QVBoxLayout(self.processing)
        progress_layout.setContentsMargins(12, 9, 12, 9)
        progress_layout.setSpacing(6)
        self.processing_label = QLabel("Processando demandas...")
        self.processing_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:600;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        progress_layout.addWidget(self.processing_label)
        progress_layout.addWidget(self.progress)
        self.processing.hide()
        layout.addWidget(self.processing)

        self.results = QWidget()
        results_layout = QVBoxLayout(self.results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(SPACING["md"])
        self.summary_strip = QFrame()
        self.summary_strip.setStyleSheet(f"background:{PALETTE['surface_subtle']}; border-radius:8px;")
        self.summary_layout = QHBoxLayout(self.summary_strip)
        self.summary_layout.setContentsMargins(12, 9, 12, 9)
        self.summary_layout.setSpacing(18)
        results_layout.addWidget(self.summary_strip)
        filters = QHBoxLayout()
        filters.setSpacing(SPACING["sm"])
        self.status_filter = self._filter(("Todos",) + tuple(status.value for status in LookupStatus))
        self.area_filter = self._filter(("Todas",) + tuple(f"Tomé {index}" for index in range(1, 7)))
        self.fiscal_filter = self._filter(("Todos",))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar logradouro ou endereço")
        self.search.setAccessibleName("Buscar demandas do lote")
        self.search.addAction(svg_icon("search.svg", "#5B7565", 16), QLineEdit.ActionPosition.LeadingPosition)
        for label, widget in (("Status", self.status_filter), ("Área", self.area_filter), ("Fiscal", self.fiscal_filter)):
            field = QLabel(label)
            field.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:600;")
            filters.addWidget(field)
            filters.addWidget(widget)
        filters.addWidget(self.search, 1)
        results_layout.addLayout(filters)
        self.model = BatchTableModel()
        self.proxy = BatchFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setItemDelegateForColumn(4, StatusPillDelegate(self.table))
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.setMinimumHeight(310)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column, width in {0: 320, 1: 78, 2: 96, 3: 170, 4: 164, 5: 86, 6: 180, 7: 112}.items():
            header.resizeSection(column, width)
        results_layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        self.open_file = QPushButton("Abrir arquivo")
        self.open_folder = QPushButton("Abrir pasta")
        for button in (self.open_file, self.open_folder):
            button.setProperty("role", "secondary")
            button.setEnabled(False)
            bottom.addWidget(button)
        self.open_file.clicked.connect(lambda: self._open_output(False))
        self.open_folder.clicked.connect(lambda: self._open_output(True))
        bottom.insertWidget(0, self.summary, 1)
        results_layout.addLayout(bottom)
        self.results.hide()
        layout.addWidget(self.results, 1)
        layout.addStretch(1)

        self.status_filter.currentTextChanged.connect(self._apply_filters)
        self.area_filter.currentTextChanged.connect(self._apply_filters)
        self.fiscal_filter.currentTextChanged.connect(self._apply_filters)
        self.search.textChanged.connect(self._apply_filters)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, self.copy_selection)

    @staticmethod
    def _filter(values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        combo.setMinimumWidth(128)
        return combo

    def choose_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Selecionar arquivo", "", "Dados (*.xlsx *.csv *.txt)")
        if selected:
            self.set_source_path(Path(selected))

    def set_source_path(self, path: Path) -> None:
        if path.suffix.casefold() not in {".xlsx", ".csv", ".txt"}:
            self.invalid_file.show()
            self.drop_zone._set_state("invalid")
            self.feedback.show_message("Formato não suportado. Selecione uma planilha compatível.", kind="error")
            return
        self.invalid_file.hide()
        self.source_path = path
        self.path_label.setText(path.name)
        self.path_label.setToolTip(str(path))
        self.file_meta.setText(f"Arquivo {path.suffix.removeprefix('.').upper()} selecionado")
        self.drop_zone.hide()
        self.file_strip.show()
        self.process.setEnabled(True)

    def start_batch(self) -> None:
        if not self.source_path or (self.worker and self.worker.isRunning()):
            return
        self.process.setEnabled(False)
        self.select_file.setEnabled(False)
        self.progress.setRange(0, 0)
        self.processing_label.setText("Processando demandas em segundo plano...")
        self.processing.show()
        self.feedback.show_message("Processamento iniciado. Você pode continuar navegando.", kind="info", duration_ms=4200)
        self.worker = BatchWorker(self.source_path, self.service_factory, self)
        self.worker.progress.connect(self._set_progress)
        self.worker.completed.connect(self._set_results)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._finish)
        self.worker.start()

    def _set_progress(self, message: str) -> None:
        self.processing_label.setText(message or "Processando demandas...")
        match = re.search(r"\[consulta (\d+)/(\d+)\]", message)
        if match:
            current, total = (int(value) for value in match.groups())
            self.progress.setRange(0, total)
            self.progress.setValue(current)

    def _set_summary(self, summary: BatchRunSummary) -> None:
        while self.summary_layout.count():
            item = self.summary_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        counts = summary.statuses
        values = (
            ("Total", summary.processed), ("Confirmados", counts["CONFIRMADO"]),
            ("Próximos", counts["PROXIMO_DA_DIVISA"]), ("Conferir", counts["CONFERIR"]),
            ("Fora da UGR", counts["FORA_DA_UGR"]),
        )
        values = (*values[:4], ("Nao localizados", counts["ENDERECO_NAO_LOCALIZADO"]), *values[4:])
        for label, value in values:
            metric = QLabel(f"<b>{value}</b> <span style='color:{PALETTE['muted']};'>{label}</span>")
            metric.setStyleSheet(f"color:{PALETTE['ink']}; font-size:{TYPE['caption']}px;")
            self.summary_layout.addWidget(metric)
        self.summary_layout.addStretch(1)

    def _set_results(self, rows: tuple[BatchRowResult, ...], summary: BatchRunSummary, output: Path) -> None:
        self.model.set_rows(rows)
        self.output_path = output
        self.open_file.setEnabled(True)
        self.open_folder.setEnabled(True)
        self._update_fiscal_options(rows)
        self._set_summary(summary)
        self.summary.setText(f"Resultado exportado em {output.name}")
        self.results.show()
        self.feedback.show_message("Lote concluído e exportado em XLSX.", kind="success", duration_ms=5200)
        self.batch_ready.emit(summary)

    def _update_fiscal_options(self, rows: tuple[BatchRowResult, ...]) -> None:
        current = self.fiscal_filter.currentText()
        names = sorted({row.result.fiscal for row in rows if row.result.fiscal and row.result.fiscal != "NÃO CONFIGURADO"})
        self.fiscal_filter.blockSignals(True)
        self.fiscal_filter.clear()
        self.fiscal_filter.addItems(("Todos", *names))
        self.fiscal_filter.setCurrentText(current if current in names else "Todos")
        self.fiscal_filter.blockSignals(False)
        self._apply_filters()

    def _finish(self) -> None:
        self.processing.hide()
        self.process.setEnabled(bool(self.source_path))
        self.select_file.setEnabled(True)

    def _show_error(self, message: str) -> None:
        self.processing.hide()
        self.feedback.show_message(message, kind="error", duration_ms=7000)
        self.results.hide()

    def _apply_filters(self) -> None:
        self.proxy.status = self.status_filter.currentText()
        self.proxy.area = self.area_filter.currentText().replace(" ", "_") if self.area_filter.currentText() != "Todas" else "Todas"
        self.proxy.fiscal = self.fiscal_filter.currentText()
        self.proxy.search = self.search.text().strip()
        self.proxy.beginFilterChange()
        self.proxy.endFilterChange()

    def copy_selection(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        text = "\n".join(
            "\t".join(str(self.proxy.index(row.row(), column).data() or "") for column in range(self.proxy.columnCount()))
            for row in selected
        )
        QApplication.clipboard().setText(text)
        self.feedback.show_message("Linhas copiadas para a área de transferência.", kind="success")

    def _open_output(self, folder: bool) -> None:
        if self.output_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_path.parent if folder else self.output_path)))
