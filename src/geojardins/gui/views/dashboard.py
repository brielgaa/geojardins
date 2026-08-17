from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme import PALETTE, SIZES, SPACING, TYPE
from ..widgets.common import PageHeader
from ..widgets.metric_card import MetricCard


class ResponsiveMetricGrid(QWidget):
    """Cinco métricas em uma composição equilibrada de 3 + 2 na largura desktop."""

    def __init__(self, cards: list[MetricCard], parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.cards = cards
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(SPACING["md"])
        self.grid.setVerticalSpacing(SPACING["md"])
        self._columns = 0
        self._relayout()

    def _relayout(self) -> None:
        width = max(1, self.width())
        columns = 3 if width >= 780 else (2 if width >= 470 else 1)
        if columns == self._columns:
            return
        while self.grid.count():
            self.grid.takeAt(0)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)
        self._columns = columns

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().resizeEvent(event)
        self._relayout()


class DashboardView(QWidget):
    configure_requested = Signal()
    lookup_requested = Signal()
    batch_requested = Signal()

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SPACING["xxl"], 28, SPACING["xxl"], SPACING["xxl"])
        content = QWidget()
        content.setMaximumWidth(SIZES["content_wide"])
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["xl"])
        outer.addStretch(1)
        outer.addWidget(content)
        outer.addStretch(1)

        header_row = QHBoxLayout()
        header_row.setSpacing(SPACING["lg"])
        header_row.addWidget(PageHeader("Visão geral", "Acompanhe a operação territorial da UGR Jardins.", "UGR Jardins"), 1)
        quick_actions = QHBoxLayout()
        quick_actions.setSpacing(SPACING["sm"])
        lookup = QPushButton("Nova consulta")
        lookup.setProperty("role", "primary")
        lookup.setToolTip("Abrir consulta individual (Ctrl+L)")
        lookup.setAccessibleName("Abrir nova consulta individual")
        lookup.clicked.connect(self.lookup_requested)
        batch = QPushButton("Processar lote")
        batch.setProperty("role", "secondary")
        batch.setToolTip("Selecionar arquivo de lote (Ctrl+O)")
        batch.setAccessibleName("Abrir processamento em lote")
        batch.clicked.connect(self.batch_requested)
        quick_actions.addWidget(lookup)
        quick_actions.addWidget(batch)
        header_row.addLayout(quick_actions, 0)
        layout.addLayout(header_row)

        labels = ("Consultas", "Confirmados", "Próximos da divisa", "Conferir", "Fora da UGR")
        self.metrics = {label: MetricCard(label) for label in labels}
        layout.addWidget(ResponsiveMetricGrid(list(self.metrics.values())))

        self.banner = QFrame()
        self.banner.setStyleSheet(f"background:{PALETTE['warning_surface']}; border:1px solid #F0E5C0; border-radius:8px;")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(13, 10, 13, 10)
        info = QLabel("i")
        info.setStyleSheet("background:#E9DFAF; color:#6B5920; border-radius:9px; min-width:18px; max-width:18px; min-height:18px; font-weight:700;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = QLabel("Configure a chave da Geoapify para realizar consultas.")
        note.setStyleSheet(f"color:{PALETTE['warning']}; font-size:{TYPE['body']}px;")
        action = QPushButton("Configurar agora")
        action.setProperty("role", "ghost")
        action.clicked.connect(self.configure_requested)
        banner_layout.addWidget(info)
        banner_layout.addWidget(note)
        banner_layout.addStretch(1)
        banner_layout.addWidget(action)
        layout.addWidget(self.banner)

        self.provider_card = QFrame()
        self.provider_card.setStyleSheet(f"background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:10px;")
        provider_layout = QVBoxLayout(self.provider_card)
        provider_layout.setContentsMargins(18, 16, 18, 15)
        provider_layout.setSpacing(SPACING["sm"])
        provider_title = QLabel("Geocodificação")
        provider_title.setStyleSheet(f"font-size:{TYPE['section']}px; font-weight:650;")
        caption = QLabel("Disponibilidade dos provedores para esta sessão")
        caption.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px;")
        provider_layout.addWidget(provider_title)
        provider_layout.addWidget(caption)
        self.geoapify = self._provider_row("Geoapify")
        self.locationiq = self._provider_row("LocationIQ")
        provider_layout.addWidget(self.geoapify)
        provider_layout.addWidget(self.locationiq)
        layout.addWidget(self.provider_card)

        self.activity = QLabel()
        self.activity.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['caption']}px; padding:2px 0;")
        self.activity.hide()
        layout.addWidget(self.activity)
        layout.addStretch(1)
        self.set_provider_status(False, False)

    @staticmethod
    def _provider_row(name: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 5, 0, 5)
        label = QLabel(name)
        label.setStyleSheet(f"font-size:{TYPE['body']}px; font-weight:600;")
        status = QLabel()
        status.setObjectName("providerStatus")
        layout.addWidget(label)
        layout.addStretch(1)
        layout.addWidget(status)
        return row

    @staticmethod
    def _set_provider_row(row: QWidget, configured: bool) -> None:
        status = row.findChild(QLabel, "providerStatus")
        assert status is not None
        text = "CONFIGURADO" if configured else "NÃO CONFIGURADO"
        color = PALETTE["success"] if configured else PALETTE["muted"]
        background = PALETTE["success_surface"] if configured else "#EEF2EE"
        status.setText(text)
        status.setStyleSheet(f"background:{background}; color:{color}; border-radius:7px; padding:3px 7px; font-size:10px; font-weight:650;")

    def set_provider_status(self, geoapify: bool, locationiq: bool) -> None:
        self._set_provider_row(self.geoapify, geoapify)
        self._set_provider_row(self.locationiq, locationiq)
        self.banner.setVisible(not geoapify)

    def set_session(self, counts: Counter[str]) -> None:
        self.metrics["Consultas"].set_value(sum(counts.values()))
        self.metrics["Confirmados"].set_value(counts["CONFIRMADO"])
        self.metrics["Próximos da divisa"].set_value(counts["PROXIMO_DA_DIVISA"])
        self.metrics["Conferir"].set_value(counts["CONFERIR"])
        self.metrics["Fora da UGR"].set_value(counts["FORA_DA_UGR"])

    def set_last_activity(self, text: str) -> None:
        self.activity.setText(text)
        self.activity.show()
