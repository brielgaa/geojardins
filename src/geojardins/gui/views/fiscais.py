from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSplitter, QVBoxLayout, QWidget

from ...user_config import UserConfigStore
from ..theme import PALETTE, SPACING, TYPE
from ..widgets.common import IconButton, PageHeader, Toast
from ..widgets.operational_map import OperationalMapView


class FiscaisView(QWidget):
    saved = Signal()

    def __init__(self, store: UserConfigStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.inputs: dict[str, QLineEdit] = {}
        self.cards: dict[str, QFrame] = {}
        self.dirty = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["xxl"], 28, SPACING["xxl"], SPACING["xxl"])
        layout.setSpacing(SPACING["xl"])
        layout.addWidget(PageHeader("Fiscais por área", "Associe responsáveis às áreas Tomé. A visualização é totalmente offline.", "Operação territorial"))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"QSplitter::handle {{ background:{PALETTE['line']}; }}")
        splitter.addWidget(self._editor_panel())
        splitter.addWidget(self._map_panel())
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 65)
        splitter.setSizes([365, 835])
        layout.addWidget(splitter, 1)
        self.feedback = Toast(self)

    def _editor_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet(f"background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:10px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(SPACING["md"])
        title = QLabel("Responsáveis")
        title.setStyleSheet(f"font-size:{TYPE['section']}px; font-weight:650; color:{PALETTE['ink']};")
        note = QLabel("Clique em uma Tomé no mapa ou edite diretamente o nome do fiscal.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        layout.addWidget(title)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        body = QWidget()
        rows_layout = QVBoxLayout(body)
        rows_layout.setContentsMargins(0, 2, 0, 0)
        rows_layout.setSpacing(2)
        assignments = self.store.fiscais()
        for index in range(1, 7):
            area = f"Tomé_{index}"
            row = QFrame()
            row.setObjectName(f"areaCard{index}")
            row.setStyleSheet(self._card_style(False))
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(9, 7, 8, 7)
            row_layout.setSpacing(SPACING["sm"])
            heading = QLabel(area.replace("_", " "))
            heading.setMinimumWidth(62)
            heading.setStyleSheet(f"font-size:{TYPE['label']}px; font-weight:650; color:#244537;")
            field = QLineEdit(assignments.get(area, ""))
            field.setPlaceholderText("Nome do fiscal")
            field.setAccessibleName(f"Fiscal responsável por {area.replace('_', ' ')}")
            field.textEdited.connect(self._mark_dirty)
            field.focusInEvent = self._focus_handler(area, field.focusInEvent)  # type: ignore[method-assign]
            row_layout.addWidget(heading)
            row_layout.addWidget(field, 1)
            self.inputs[area] = field
            self.cards[area] = row
            rows_layout.addWidget(row)
        rows_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        self.dirty_label = QLabel("✓ Alterações salvas")
        self.dirty_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        from PySide6.QtWidgets import QPushButton

        self.save_button = QPushButton("Salvar alterações")
        self.save_button.setProperty("role", "primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.dirty_label, 1)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        return panel

    @staticmethod
    def _card_style(selected: bool) -> str:
        if selected:
            return f"background:#F4F7E6; border:1px solid #A5B76A; border-left:3px solid #859D4D; border-radius:6px;"
        return "background:transparent; border:1px solid transparent; border-bottom:1px solid #E8EEE8; border-radius:6px;"

    def _focus_handler(self, area: str, original):  # type: ignore[no-untyped-def]
        def handler(event) -> None:  # type: ignore[no-untyped-def]
            original(event)
            self.select_area(area, focus=False)

        return handler

    def _map_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(SPACING["lg"], 0, 0, 0)
        layout.setSpacing(SPACING["sm"])
        header = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel("Mapa operacional")
        title.setStyleSheet(f"font-size:{TYPE['section']}px; font-weight:650; color:{PALETTE['ink']};")
        note = QLabel("Território UGR Jardins · áreas clicáveis · uso offline")
        note.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        text.addWidget(title)
        text.addWidget(note)
        header.addLayout(text, 1)
        self.selected_info = QLabel()
        self.selected_info.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        header.addWidget(self.selected_info)
        self.zoom_out = IconButton("zoom-out.svg", "Reduzir zoom")
        self.zoom_in = IconButton("zoom-in.svg", "Aumentar zoom")
        self.fit = IconButton("fit.svg", "Enquadrar território")
        header.addWidget(self.zoom_out)
        header.addWidget(self.zoom_in)
        header.addWidget(self.fit)
        layout.addLayout(header)
        self.map = OperationalMapView()
        self.map.setMinimumWidth(420)
        self.map.set_fiscais(self.store.fiscais())
        self.map.area_selected.connect(self.select_area)
        self.zoom_out.clicked.connect(self.map.zoom_out)
        self.zoom_in.clicked.connect(self.map.zoom_in)
        self.fit.clicked.connect(self.map.fit_to_view)
        layout.addWidget(self.map, 1)
        return panel

    def select_area(self, area: str, focus: bool = True) -> None:
        if area not in self.inputs:
            return
        self.map.set_selected(area)
        for name, card in self.cards.items():
            card.setStyleSheet(self._card_style(name == area))
        fiscal = self.inputs[area].text().strip() or "Fiscal não configurado"
        self.selected_info.setText(f"{area.replace('_', ' ')} · {fiscal}")
        if focus:
            self.inputs[area].setFocus(Qt.FocusReason.OtherFocusReason)

    def _mark_dirty(self, _text: str) -> None:
        if not self.dirty:
            self.dirty = True
            self.dirty_label.setText("● Alterações não salvas")
            self.dirty_label.setStyleSheet(f"color:{PALETTE['warning']}; font-size:{TYPE['caption']}px; font-weight:600;")
            self.save_button.setEnabled(True)

    def save(self) -> None:
        assignments = {area: field.text() for area, field in self.inputs.items()}
        self.store.save_fiscais(assignments)
        self.map.set_fiscais(assignments)
        self.dirty = False
        self.save_button.setEnabled(False)
        self.dirty_label.setText("✓ Alterações salvas")
        self.dirty_label.setStyleSheet(f"color:{PALETTE['success']}; font-size:{TYPE['caption']}px; font-weight:600;")
        QTimer.singleShot(3600, self._calm_saved_label)
        self.feedback.show_message("Fiscais salvos.", kind="success")
        self.saved.emit()

    def _calm_saved_label(self) -> None:
        if not self.dirty:
            self.dirty_label.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
