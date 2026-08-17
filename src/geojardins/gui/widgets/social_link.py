"""Links sociais acessíveis com SVGs locais e sem navegador embutido."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QPushButton

from ...runtime import resource_path


GITHUB_URL = "https://github.com/brielgaa"
LINKEDIN_URL = "https://www.linkedin.com/in/gabrieldasilvabitencourt/"


def asset_path(filename: str) -> Path:
    """Localiza SVG em desenvolvimento e no bundle, sem depender do CWD."""

    bundled = resource_path("assets", filename)
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parents[1] / "assets" / filename


def svg_icon(filename: str, color: str, size: int = 18) -> QIcon:
    """Pinta um SVG monocromático local para o contexto claro ou escuro."""

    source = asset_path(filename).read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class SocialLinkButton(QPushButton):
    """Botão de link externo com nome acessível, tooltip e foco de teclado."""

    def __init__(
        self,
        network: str,
        *,
        compact: bool = False,
        dark_surface: bool = False,
        parent=None,
    ) -> None:  # type: ignore[no-untyped-def]
        if network not in {"github", "linkedin"}:
            raise ValueError(f"Rede social não suportada: {network}")
        metadata = {
            "github": ("GitHub", "@brielgaa", GITHUB_URL, "github.svg"),
            "linkedin": ("LinkedIn", "Gabriel da Silva Bitencourt", LINKEDIN_URL, "linkedin.svg"),
        }[network]
        name, identity, url, filename = metadata
        super().__init__("" if compact else ("brielgaa" if network == "github" else "gabrieldasilvabitencourt"), parent)
        self.url = url
        color = "#BFD2C2" if dark_surface else "#315B4B"
        self.setIcon(svg_icon(filename, color, 16))
        self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Abrir perfil {name} de {identity}")
        self.setToolTip(f"{name} — {identity}")
        self.setProperty("socialCompact", compact)
        self.setProperty("socialDark", dark_surface)
        self.setStyleSheet(
            "QPushButton { background:transparent; border:0; border-radius:6px; padding:5px; color:#315B4B; font-size:11px; text-align:left; }"
            "QPushButton:hover { background:#E6EFE7; }"
            "QPushButton:focus { outline:0; border:1px solid #7A9A72; }"
            "QPushButton[socialDark=\"true\"] { color:#BFD2C2; }"
            "QPushButton[socialDark=\"true\"]:hover { background:#285242; }"
            "QPushButton[socialCompact=\"true\"] { min-width:26px; max-width:26px; min-height:26px; max-height:26px; padding:5px; }"
        )
        self.clicked.connect(self.open_profile)

    def open_profile(self) -> bool:
        return QDesktopServices.openUrl(QUrl(self.url))
