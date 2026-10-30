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
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...address_lookup import AddressLookupService, LookupStatus
from ...batch_lookup import (
    BatchColumnMapping,
    BatchInputError,
    BatchRowResult,
    BatchRunSummary,
    InputRows,
    build_paste_export_path,
    default_output_path,
    read_paste_input,
    resolve_batch_columns,
)
from ...user_config import UserConfigStore
from ..theme import PALETTE, SPACING, TYPE
from ..widgets.common import PageHeader, Toast
from ..widgets.result_panel import ResultPanel
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
            urls = event.mimeData().urls()
            path = Path(urls[0].toLocalFile()) if urls else None
            accepted = path is not None and path.suffix.casefold() in {".xlsx", ".csv", ".txt"}
            self._set_state("hover" if accepted else "invalid")
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._set_state("idle")
        event.accept()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        urls = event.mimeData().urls()
        path = Path(urls[0].toLocalFile()) if urls else None
        if path:
            accepted = path.suffix.casefold() in {".xlsx", ".csv", ".txt"}
            self._set_state("idle" if accepted else "invalid")
            self.file_dropped.emit(path)
            event.acceptProposedAction()
            return
        event.ignore()


class BatchLookupView(QWidget):
    batch_ready = Signal(object)

    def __init__(
        self,
        service_factory: Callable[[], AddressLookupService],
        store: UserConfigStore | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service_factory = service_factory
        self.store = store
        self.source_path: Path | None = None
        self.output_path: Path | None = None
        self.worker: BatchWorker | None = None
        self.paste_source: InputRows | None = None
        self.paste_column_mapping: BatchColumnMapping | None = None
        self.source_mode = "file"
        self.cancel_requested = False
        self.total_rows = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["xxl"], 28, SPACING["xxl"], SPACING["xxl"])
        layout.setSpacing(SPACING["xl"])
        layout.addWidget(PageHeader("Processamento em lote", "Envie uma planilha; o arquivo original é preservado e o resultado é exportado em XLSX."))

        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(SPACING["sm"])
        self.import_file_button = QPushButton("Importar arquivo")
        self.import_file_button.setProperty("role", "primary")
        self.import_file_button.clicked.connect(self.choose_file)
        self.paste_data_button = QPushButton("Colar dados")
        self.paste_data_button.setProperty("role", "secondary")
        self.paste_data_button.clicked.connect(self.show_paste_mode)
        mode_layout.addWidget(self.import_file_button)
        mode_layout.addWidget(self.paste_data_button)
        mode_layout.addStretch(1)
        layout.addLayout(mode_layout)

        self.drop_zone = BatchDropZone()
        self.drop_zone.browse_requested.connect(self.choose_file)
        self.drop_zone.file_dropped.connect(self.set_source_path)
        layout.addWidget(self.drop_zone)
        self.invalid_file = QLabel("Use somente planilhas .xlsx, .csv ou .txt.")
        self.invalid_file.setStyleSheet(f"color:{PALETTE['danger']}; font-size:{TYPE['caption']}px;")
        self.invalid_file.hide()
        layout.addWidget(self.invalid_file)

        self.paste_panel = QFrame()
        self.paste_panel.setStyleSheet(f"background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:8px;")
        paste_layout = QVBoxLayout(self.paste_panel)
        paste_layout.setContentsMargins(16, 14, 16, 14)
        paste_layout.setSpacing(SPACING["sm"])
        paste_title = QLabel("Cole duas colunas: Logradouro e Altura")
        paste_title.setStyleSheet(f"color:{PALETTE['ink']}; font-size:{TYPE['section']}px; font-weight:650;")
        paste_hint = QLabel("Aceita TAB do Excel/Sheets, ponto e vírgula ou vírgula. O cabeçalho é opcional.")
        paste_hint.setWordWrap(True)
        paste_hint.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        self.paste_editor = QPlainTextEdit()
        self.paste_editor.setPlaceholderText("Logradouro\tAltura\nRua Oscar Freire\t1250\nRua Augusta\t2770")
        self.paste_editor.setMinimumHeight(164)
        self.paste_editor.setAccessibleName("Dados de logradouro e altura para processamento em lote")
        self.paste_status = QLabel("Cole os dados para visualizar a interpretação.")
        self.paste_status.setWordWrap(True)
        self.paste_status.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        self.paste_mapping_frame = QFrame()
        mapping_layout = QHBoxLayout(self.paste_mapping_frame)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        mapping_layout.setSpacing(SPACING["sm"])
        mapping_label = QLabel("Colunas")
        mapping_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:600;")
        self.paste_street_column = QComboBox()
        self.paste_street_column.setAccessibleName("Coluna de logradouro")
        self.paste_number_column = QComboBox()
        self.paste_number_column.setAccessibleName("Coluna de altura")
        mapping_layout.addWidget(mapping_label)
        mapping_layout.addWidget(QLabel("Logradouro"))
        mapping_layout.addWidget(self.paste_street_column)
        mapping_layout.addWidget(QLabel("Altura"))
        mapping_layout.addWidget(self.paste_number_column)
        mapping_layout.addStretch(1)
        self.paste_preview = QTableWidget(0, 2)
        self.paste_preview.setHorizontalHeaderLabels(("Logradouro", "Altura"))
        self.paste_preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.paste_preview.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.paste_preview.setMinimumHeight(128)
        self.paste_preview.setMaximumHeight(210)
        self.paste_preview.horizontalHeader().setStretchLastSection(True)
        self.paste_preview.verticalHeader().setVisible(False)
        paste_actions = QHBoxLayout()
        self.paste_process = QPushButton("Processar dados colados")
        self.paste_process.setProperty("role", "primary")
        self.paste_process.setEnabled(False)
        self.paste_process.clicked.connect(self.start_paste_batch)
        self.paste_cancel = QPushButton("Voltar para arquivo")
        self.paste_cancel.setProperty("role", "ghost")
        self.paste_cancel.clicked.connect(self.show_file_mode)
        paste_actions.addWidget(self.paste_process)
        paste_actions.addWidget(self.paste_cancel)
        paste_actions.addStretch(1)
        paste_layout.addWidget(paste_title)
        paste_layout.addWidget(paste_hint)
        paste_layout.addWidget(self.paste_editor)
        paste_layout.addWidget(self.paste_status)
        paste_layout.addWidget(self.paste_mapping_frame)
        paste_layout.addWidget(self.paste_preview)
        paste_layout.addLayout(paste_actions)
        self.paste_editor.textChanged.connect(self._update_paste_preview)
        self.paste_street_column.currentTextChanged.connect(self._update_paste_mapping)
        self.paste_number_column.currentTextChanged.connect(self._update_paste_mapping)
        self.paste_panel.hide()
        layout.addWidget(self.paste_panel)

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
        progress_header = QHBoxLayout()
        self.processing_label = QLabel("Processando demandas...")
        self.processing_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; font-weight:600;")
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setProperty("role", "danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.request_cancel)
        progress_header.addWidget(self.processing_label, 1)
        progress_header.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        progress_layout.addLayout(progress_header)
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
        self.detail_panel = ResultPanel()
        self.detail_panel.setMinimumHeight(250)
        self.detail_panel.setMaximumHeight(430)
        self.detail_panel.hide()
        results_layout.addWidget(self.detail_panel)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        self.reprocess_pending = QPushButton("Reprocessar pendências")
        self.reprocess_pending.setProperty("role", "secondary")
        self.reprocess_pending.setEnabled(False)
        self.reprocess_pending.clicked.connect(self.reprocess_pending_rows)
        self.reprocess_selected = QPushButton("Reprocessar selecionados")
        self.reprocess_selected.setProperty("role", "ghost")
        self.reprocess_selected.setEnabled(False)
        self.reprocess_selected.clicked.connect(self.reprocess_selected_rows)
        self.open_file = QPushButton("Abrir arquivo")
        self.open_folder = QPushButton("Abrir pasta")
        for button in (self.open_file, self.open_folder):
            button.setProperty("role", "secondary")
            button.setEnabled(False)
            bottom.addWidget(button)
        bottom.addWidget(self.reprocess_pending)
        bottom.addWidget(self.reprocess_selected)
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
        self.status_filter.currentTextChanged.connect(self._save_status_preference)
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)
        QShortcut(QKeySequence.StandardKey.Copy, self.table, self.copy_selection)
        self._restore_preferences()

    @staticmethod
    def _filter(values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        combo.setMinimumWidth(128)
        return combo

    def _preferences(self) -> dict[str, object]:
        return self.store.preferences() if self.store is not None else {}

    def _save_preferences(self, **values: object) -> None:
        if self.store is not None:
            self.store.save_preferences(**values)

    def _restore_preferences(self) -> None:
        preferences = self._preferences()
        saved_status = preferences.get("batch_status_filter")
        if isinstance(saved_status, str) and self.status_filter.findText(saved_status) >= 0:
            self.status_filter.setCurrentText(saved_status)
        if preferences.get("batch_mode") == "paste":
            self.show_paste_mode()

    def _save_status_preference(self, value: str) -> None:
        self._save_preferences(batch_status_filter=value)

    def choose_file(self) -> None:
        self.show_file_mode()
        last_directory = self._preferences().get("last_import_dir", "")
        directory = str(last_directory) if isinstance(last_directory, str) else ""
        selected, _ = QFileDialog.getOpenFileName(self, "Selecionar arquivo", directory, "Dados (*.xlsx *.csv *.txt)")
        if selected:
            self.set_source_path(Path(selected))

    def set_source_path(self, path: Path) -> None:
        if path.suffix.casefold() not in {".xlsx", ".csv", ".txt"}:
            self.invalid_file.show()
            self.drop_zone._set_state("invalid")
            self.feedback.show_message("Formato não suportado. Selecione uma planilha compatível.", kind="error")
            return
        self.drop_zone._set_state("idle")
        self.source_mode = "file"
        self._save_preferences(batch_mode="file", last_import_dir=str(path.parent))
        self.paste_source = None
        self.paste_column_mapping = None
        self.total_rows = 0
        self.paste_panel.hide()
        self.invalid_file.hide()
        self.source_path = path
        self.path_label.setText(path.name)
        self.path_label.setToolTip(str(path))
        self.file_meta.setText(f"Arquivo {path.suffix.removeprefix('.').upper()} selecionado")
        self.drop_zone.hide()
        self.file_strip.show()
        self.process.setEnabled(True)

    def show_file_mode(self, _checked: bool = False, *, clear_source: bool = True) -> None:
        self.source_mode = "file"
        self._save_preferences(batch_mode="file")
        self.paste_panel.hide()
        self.drop_zone.show()
        self.invalid_file.hide()
        if clear_source:
            self.source_path = None
            self.output_path = None
            self.total_rows = 0
            self.file_strip.hide()
            self.process.setEnabled(False)

    def show_paste_mode(self, _checked: bool = False) -> None:
        self.source_mode = "paste"
        self._save_preferences(batch_mode="paste")
        self.source_path = None
        self.output_path = None
        self.total_rows = 0
        self.drop_zone.hide()
        self.file_strip.hide()
        self.invalid_file.hide()
        self.paste_panel.show()
        self._update_paste_preview()
        self.paste_editor.setFocus()

    def _set_paste_columns(self, source: InputRows, mapping: BatchColumnMapping | None) -> None:
        headers = list(source.headers)
        for combo, selected in (
            (self.paste_street_column, mapping.street_column if mapping else None),
            (self.paste_number_column, mapping.number_column if mapping else None),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(headers)
            if selected in headers:
                combo.setCurrentText(selected)
            combo.blockSignals(False)

    def _render_paste_preview(self) -> None:
        source = self.paste_source
        if source is None:
            self.paste_preview.setRowCount(0)
            return
        columns = [
            self.paste_street_column.currentText() or source.headers[0],
            self.paste_number_column.currentText() or source.headers[1],
        ]
        self.paste_preview.setRowCount(min(5, len(source.rows)))
        for row_index, row in enumerate(source.rows[:5]):
            for column_index, column in enumerate(columns):
                value = row.get(column)
                self.paste_preview.setItem(row_index, column_index, QTableWidgetItem("" if value is None else str(value)))
        self.paste_preview.resizeColumnsToContents()
        self.paste_preview.horizontalHeader().setStretchLastSection(True)

    def _manual_paste_mapping_is_valid(self) -> bool:
        return bool(
            self.paste_source
            and self.paste_street_column.currentText()
            and self.paste_number_column.currentText()
            and self.paste_street_column.currentText() != self.paste_number_column.currentText()
        )

    def _update_paste_mapping(self, _value: str = "") -> None:
        if self.paste_source is None:
            return
        self.paste_column_mapping = (
            BatchColumnMapping(
                self.paste_street_column.currentText(),
                self.paste_number_column.currentText(),
                inferred=False,
            )
            if self._manual_paste_mapping_is_valid()
            else None
        )
        self._render_paste_preview()
        if self.paste_column_mapping is None:
            self.paste_status.setText("Selecione colunas diferentes para Logradouro e Altura.")
        else:
            self.paste_status.setText(f"{len(self.paste_source.rows)} linhas reconhecidas · seleção manual")
        self.paste_process.setEnabled(self.paste_column_mapping is not None and bool(self.paste_source.rows))

    def _update_paste_preview(self) -> None:
        text = self.paste_editor.toPlainText()
        if not text.strip():
            self.paste_source = None
            self.paste_column_mapping = None
            self.paste_mapping_frame.hide()
            self.paste_preview.setRowCount(0)
            self.paste_status.setText("Cole os dados para visualizar a interpretação.")
            self.paste_process.setEnabled(False)
            return
        try:
            source = read_paste_input(text)
        except BatchInputError as exc:
            self.paste_source = None
            self.paste_column_mapping = None
            self.paste_mapping_frame.hide()
            self.paste_preview.setRowCount(0)
            self.paste_status.setText(str(exc))
            self.paste_process.setEnabled(False)
            return

        self.paste_source = source
        self.paste_mapping_frame.show()
        try:
            mapping = resolve_batch_columns(source)
        except BatchInputError:
            mapping = None
            self._set_paste_columns(source, None)
            self.paste_status.setText(f"{len(source.rows)} linhas reconhecidas · selecione as colunas manualmente.")
        else:
            self.paste_column_mapping = mapping
            self._set_paste_columns(source, mapping)
            detail = "colunas inferidas pelo conteúdo" if mapping.inferred else "colunas reconhecidas"
            self.paste_status.setText(f"{len(source.rows)} linhas reconhecidas · {detail}.")
        self._render_paste_preview()
        if mapping is None:
            self.paste_column_mapping = None
            self.paste_process.setEnabled(self._manual_paste_mapping_is_valid() and bool(source.rows))
        else:
            self.paste_process.setEnabled(bool(source.rows))

    def start_batch(self) -> None:
        if not self.source_path or (self.worker and self.worker.isRunning()):
            return
        self._start_worker(self.source_path)

    def start_paste_batch(self) -> None:
        if not self.paste_source or not self.paste_column_mapping or (self.worker and self.worker.isRunning()):
            return
        self._start_worker(
            self.paste_source,
            output_path=build_paste_export_path(),
            street_column=self.paste_column_mapping.street_column,
            number_column=self.paste_column_mapping.number_column,
        )

    def _selected_source_indices(self) -> tuple[int, ...]:
        indices: set[int] = set()
        for proxy_index in self.table.selectionModel().selectedRows():
            source_row = self.proxy.mapToSource(proxy_index).row()
            item = self.model.rows[source_row]
            indices.add(item.source_index if item.source_index is not None else source_row)
        return tuple(sorted(indices))

    def _pending_source_indices(self) -> tuple[int, ...]:
        pending = {"CONFERIR", "ENDERECO_NAO_LOCALIZADO", "ERRO_GEOCODIFICACAO"}
        return tuple(
            item.source_index if item.source_index is not None else index
            for index, item in enumerate(self.model.rows)
            if item.result.status.value in pending
        )

    def reprocess_pending_rows(self) -> None:
        self._start_reprocessing(self._pending_source_indices())

    def reprocess_selected_rows(self) -> None:
        self._start_reprocessing(self._selected_source_indices())

    def _start_reprocessing(self, indices: tuple[int, ...]) -> None:
        source: Path | InputRows | None = self.source_path or self.paste_source
        if source is None or not self.model.rows:
            self.feedback.show_message("Conclua um lote antes de reprocessar linhas.", kind="info")
            return
        if self.total_rows and len(self.model.rows) != self.total_rows:
            self.feedback.show_message("O reprocessamento exige um resultado completo do lote.", kind="info")
            return
        if not indices:
            self.feedback.show_message("Não há linhas elegíveis para reprocessamento.", kind="info")
            return
        output = (
            (self.output_path or default_output_path(self.source_path))
            if self.source_path is not None
            else build_paste_export_path()
        )
        self._start_worker(
            source,
            output_path=output,
            row_indices=indices,
            existing_results=self.model.rows,
        )

    def _start_worker(
        self,
        source: Path | InputRows,
        *,
        output_path: Path | None = None,
        street_column: str | None = None,
        number_column: str | None = None,
        row_indices: tuple[int, ...] | None = None,
        existing_results: tuple[BatchRowResult, ...] | None = None,
    ) -> None:
        self.process.setEnabled(False)
        self.paste_process.setEnabled(False)
        self.select_file.setEnabled(False)
        self.import_file_button.setEnabled(False)
        self.paste_data_button.setEnabled(False)
        self.reprocess_pending.setEnabled(False)
        self.reprocess_selected.setEnabled(False)
        self.cancel_requested = False
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.processing_label.setText("Processando demandas em segundo plano...")
        self.processing.show()
        self.feedback.show_message("Processamento iniciado. Você pode continuar navegando.", kind="info", duration_ms=4200)
        self.worker = BatchWorker(
            source,
            self.service_factory,
            self,
            output_path=output_path,
            street_column=street_column,
            number_column=number_column,
            row_indices=row_indices,
            existing_results=existing_results,
        )
        self.worker.progress.connect(self._set_progress)
        self.worker.completed.connect(self._set_results)
        self.worker.failed.connect(self._show_error)
        self.worker.finished.connect(self._finish)
        self.worker.start()

    def request_cancel(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return
        self.cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.processing_label.setText("Cancelando após a consulta atual...")
        self.worker.request_cancel()

    def _set_progress(self, message: str) -> None:
        if self.cancel_requested:
            return
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
        self.total_rows = summary.total_rows or len(rows)
        self.output_path = output
        self.open_file.setEnabled(True)
        self.open_folder.setEnabled(True)
        self._update_fiscal_options(rows)
        self._set_summary(summary)
        if summary.cancelled:
            if summary.reprocessed:
                self.summary.setText("Reprocessamento cancelado; resultados anteriores foram preservados")
            else:
                total = summary.total_rows or summary.processed
                self.summary.setText(f"Processamento cancelado: {summary.processed} de {total} linhas processadas")
            self.feedback.show_message("Processamento cancelado. Os resultados concluídos foram preservados.", kind="info", duration_ms=5200)
        else:
            self.summary.setText(f"Resultado exportado em {output.name}")
            self.feedback.show_message("Lote concluído e exportado em XLSX.", kind="success", duration_ms=5200)
        self.results.show()
        self._update_reprocess_buttons()
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
        self.cancel_button.setEnabled(False)
        self.cancel_requested = False
        self.process.setEnabled(bool(self.source_path))
        self.paste_process.setEnabled(bool(self.paste_source and self.paste_column_mapping and self.paste_source.rows))
        self.select_file.setEnabled(True)
        self.import_file_button.setEnabled(True)
        self.paste_data_button.setEnabled(True)
        self._update_reprocess_buttons()

    def _show_error(self, message: str) -> None:
        self.processing.hide()
        self.feedback.show_message(message, kind="error", duration_ms=7000)
        self.results.hide()
        self._finish()

    def _apply_filters(self) -> None:
        self.proxy.status = self.status_filter.currentText()
        self.proxy.area = self.area_filter.currentText().replace(" ", "_") if self.area_filter.currentText() != "Todas" else "Todas"
        self.proxy.fiscal = self.fiscal_filter.currentText()
        self.proxy.search = self.search.text().strip()
        self.proxy.beginFilterChange()
        self.proxy.endFilterChange()

    def _on_table_selection_changed(self) -> None:
        selected = self._selected_source_indices()
        if selected:
            source_row = self.proxy.mapToSource(self.table.selectionModel().selectedRows()[0]).row()
            self.detail_panel.show_result(self.model.rows[source_row].result, expanded=True)
            self.detail_panel.show()
        else:
            self.detail_panel.hide()
        self._update_reprocess_buttons()

    def _update_reprocess_buttons(self) -> None:
        active = self.worker is not None and self.worker.isRunning()
        self.reprocess_pending.setEnabled(not active and bool(self._pending_source_indices()))
        self.reprocess_selected.setEnabled(not active and bool(self._selected_source_indices()))

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
