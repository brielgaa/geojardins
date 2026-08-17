from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ..theme import PALETTE, RADIUS, SPACING, TYPE


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "0", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setMinimumHeight(106)
        self.setStyleSheet(f"QFrame#metricCard {{ background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:{RADIUS['medium']}px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["lg"], 15, SPACING["lg"], 14)
        layout.setSpacing(4)
        self.value = QLabel(value)
        self.value.setStyleSheet(f"font-size:{TYPE['metric']}px; font-weight:650; color:{PALETTE['ink']};")
        caption = QLabel(label)
        caption.setStyleSheet(f"color:{PALETTE['muted']}; font-size:{TYPE['label']}px; font-weight:550;")
        layout.addWidget(self.value)
        layout.addWidget(caption)

    def set_value(self, value: int | str) -> None:
        self.value.setText(str(value))
