from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ... import __version__
from ..theme import SIZES, SPACING
from .social_link import SocialLinkButton, svg_icon


class Sidebar(QFrame):
    page_requested = Signal(int)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setFixedWidth(SIZES["sidebar"])
        self.setStyleSheet("background:#1D4939; QFrame { background:#1D4939; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 18)
        layout.setSpacing(SPACING["xs"])
        brand_row = QHBoxLayout()
        brand_row.setSpacing(7)
        brand_icon = QLabel()
        brand_icon.setPixmap(svg_icon("geojardins.svg", "#E7F0D1", 18).pixmap(18, 18))
        brand = QLabel("GEOJARDINS")
        brand.setStyleSheet("color:#FFFFFF; font-weight:700; font-size:15px; letter-spacing:0.4px;")
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        subtitle = QLabel("UGR Jardins · operação territorial")
        subtitle.setStyleSheet("color:#BDD0C1; font-size:10px;")
        layout.addLayout(brand_row)
        layout.addWidget(subtitle)
        layout.addSpacing(26)

        self.group = QButtonGroup(self)
        self.buttons: list[QPushButton] = []
        self._button_icons: list[str] = []
        pages = (
            ("Visão geral", "dashboard.svg", "Abrir visão geral"),
            ("Consulta", "lookup.svg", "Abrir consulta individual (Ctrl+L)"),
            ("Lote", "batch.svg", "Abrir processamento em lote (Ctrl+O)"),
            ("Fiscais", "fiscais.svg", "Abrir gestão de fiscais (Ctrl+Shift+F)"),
            ("Configurações", "settings.svg", "Abrir configurações (Ctrl+,)"),
        )
        for index, (label, icon, tooltip) in enumerate(pages):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(svg_icon(icon, "#D4E2D6", 18))
            button.setIconSize(QSize(17, 17))
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setStyleSheet(
                "QPushButton { text-align:left; color:#D4E2D6; background:transparent; padding:0 10px; min-height:38px; border-radius:6px; font-size:12px; }"
                "QPushButton:checked { color:#17372C; background:#ECF1D7; font-weight:650; border-left:3px solid #9CAD62; padding-left:7px; }"
                "QPushButton:hover:!checked { background:#285645; }"
                "QPushButton:focus { border:1px solid #DCEB87; }"
            )
            self.group.addButton(button, index)
            self.buttons.append(button)
            self._button_icons.append(icon)
            layout.addWidget(button)
        self.group.idClicked.connect(self.page_requested)
        layout.addStretch(1)

        footer = QLabel(f"GeoJardins • v{__version__}")
        footer.setStyleSheet("color:#B5CCBA; font-size:10px; font-weight:650;")
        layout.addWidget(footer)
        credit = QLabel("Gabriel Bitencourt")
        credit.setStyleSheet("color:#A8C0AD; font-size:10px;")
        layout.addWidget(credit)
        socials = QHBoxLayout()
        socials.setContentsMargins(0, 2, 0, 0)
        socials.setSpacing(SPACING["sm"])
        self.github_button = SocialLinkButton("github", compact=True, dark_surface=True)
        self.linkedin_button = SocialLinkButton("linkedin", compact=True, dark_surface=True)
        socials.addWidget(self.github_button)
        socials.addWidget(self.linkedin_button)
        socials.addStretch(1)
        layout.addLayout(socials)
        self.select(0)

    def select(self, index: int) -> None:
        self.buttons[index].setChecked(True)
        for item_index, button in enumerate(self.buttons):
            button.setIcon(svg_icon(self._button_icons[item_index], "#17372C" if item_index == index else "#D4E2D6", 18))
