from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from ..theme import TYPE


STATUS_COLORS = {
    "CONFIRMADO": ("#E0F2E7", "#17643B"),
    "PROXIMO_DA_DIVISA": ("#FFF1CC", "#765A12"),
    "CONFERIR": ("#FDE8D7", "#9A4312"),
    "FORA_DA_UGR": ("#E8ECF0", "#495A67"),
    "ENDERECO_NAO_LOCALIZADO": ("#F9E3E5", "#9B2534"),
    "ERRO_GEOCODIFICACAO": ("#F9E3E5", "#9B2534"),
    "DADOS_INVALIDOS": ("#F9E3E5", "#9B2534"),
}


class StatusBadge(QLabel):
    """Badge textual: a cor reforça, mas nunca é a única informação."""

    def __init__(self, status: str = "—", parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        background, foreground = STATUS_COLORS.get(status, ("#E8ECF0", "#495A67"))
        self.setText(status.replace("_", " "))
        self.setStyleSheet(f"background:{background}; color:{foreground}; border-radius:9px; padding:4px 8px; font-size:{TYPE['caption']}px; font-weight:650;")
