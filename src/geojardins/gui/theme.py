"""Sistema visual do GeoJardins: tokens, tipografia e controles nativos refinados."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .widgets.social_link import asset_path


PALETTE = {
    "canvas": "#F4F7F4",
    "surface": "#FFFFFF",
    "surface_hover": "#F7FAF7",
    "surface_subtle": "#EFF4F0",
    "ink": "#17372C",
    "muted": "#54675C",
    "faint": "#75877C",
    "line": "#D7E2D8",
    "moss": "#1D4939",
    "moss_dark": "#153B2E",
    "moss_hover": "#285845",
    "accent_soft": "#ECF1D7",
    "accent_line": "#A6B96A",
    "focus": "#4E7B5D",
    "success_surface": "#E2F2E6",
    "success": "#17643B",
    "warning_surface": "#FCF5DE",
    "warning": "#765A12",
    "danger_surface": "#FCEAEC",
    "danger": "#A74342",
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"small": 6, "medium": 8, "large": 10}
CONTROL_HEIGHT = 38
SIZES = {
    "sidebar": 224,
    "content_form": 840,
    "content_standard": 1060,
    "content_wide": 1320,
    # Compatibilidade explícita para componentes já existentes.
    "page_max": 1180,
    "form_max": 840,
    "input_height": CONTROL_HEIGHT,
}
TYPE = {"page": 26, "section": 18, "body": 13, "label": 12, "caption": 11, "metric": 30, "technical": 11}


def apply_theme(app: QApplication) -> None:
    """Tema claro, contido e legível para uma ferramenta operacional desktop."""

    app.setStyle("Fusion")
    font = QFont("Segoe UI Variable", 10)
    font.setFamilies(["Segoe UI Variable", "Segoe UI", "Arial", "sans-serif"])
    app.setFont(font)
    arrow = asset_path("chevron-down.svg").as_posix()
    app.setStyleSheet(
        f"""
        QWidget {{ background: {PALETTE['canvas']}; color: {PALETTE['ink']}; font-family: "Segoe UI Variable", "Segoe UI", Arial, sans-serif; }}
        QLabel {{ background: transparent; }}
        QMainWindow {{ background: {PALETTE['canvas']}; }}
        QLineEdit, QComboBox, QDoubleSpinBox {{
            background: {PALETTE['surface']}; border: 1px solid {PALETTE['line']};
            border-radius: {RADIUS['medium']}px; padding: 0 10px; min-height: {CONTROL_HEIGHT - 2}px;
            selection-background-color: #DDEDDD; selection-color: {PALETTE['ink']}; font-size: {TYPE['body']}px;
        }}
        QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover {{ border-color: #AFC2B2; background: #FCFDFC; }}
        QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {PALETTE['focus']}; padding: 0 9px; }}
        QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{ background:#EFF3F0; color:#85928A; border-color:#E1E8E2; }}
        QLineEdit::placeholder {{ color: #86968C; }}
        QComboBox {{ padding-right: 30px; }}
        QComboBox::drop-down {{ border: 0; width: 30px; }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 15px; height: 15px; }}
        QComboBox QAbstractItemView {{ background: {PALETTE['surface']}; border:1px solid {PALETTE['line']}; selection-background-color:#E6F0E5; selection-color:{PALETTE['ink']}; outline:0; padding:4px; }}
        QComboBox QAbstractItemView::item {{ min-height:28px; padding:0 8px; border-radius:4px; }}
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border:0; }}
        QPushButton {{ border: 1px solid transparent; border-radius: {RADIUS['medium']}px; padding: 0 14px; min-height: {CONTROL_HEIGHT - 2}px; font-size:{TYPE['body']}px; font-weight:600; }}
        QPushButton[role="primary"] {{ background: {PALETTE['moss']}; color: white; }}
        QPushButton[role="primary"]:hover {{ background: {PALETTE['moss_hover']}; }}
        QPushButton[role="primary"]:pressed {{ background: {PALETTE['moss_dark']}; }}
        QPushButton[role="secondary"] {{ background: #E7EFE8; color: {PALETTE['ink']}; }}
        QPushButton[role="secondary"]:hover {{ background: #DCE9DE; }}
        QPushButton[role="secondary"]:pressed {{ background: #D0E0D2; }}
        QPushButton[role="ghost"] {{ background: transparent; color: {PALETTE['moss']}; }}
        QPushButton[role="ghost"]:hover {{ background: #E8F1E9; }}
        QPushButton[role="ghost"]:pressed {{ background: #DDEBDD; }}
        QPushButton[role="danger"] {{ background: transparent; color: {PALETTE['danger']}; }}
        QPushButton[role="danger"]:hover {{ background: {PALETTE['danger_surface']}; }}
        QPushButton[role="icon"] {{ background: transparent; color:{PALETTE['moss']}; min-width:{CONTROL_HEIGHT}px; max-width:{CONTROL_HEIGHT}px; padding:0; }}
        QPushButton[role="icon"]:hover {{ background:#E8F1E9; }}
        QPushButton:focus {{ border: 2px solid {PALETTE['focus']}; }}
        QPushButton:disabled {{ background: #E6ECE7; color: #89958D; border: 0; }}
        QTableView {{ background:{PALETTE['surface']}; border:1px solid {PALETTE['line']}; border-radius:{RADIUS['medium']}px; gridline-color:transparent; selection-background-color:#E2EEE2; selection-color:{PALETTE['ink']}; outline:0; }}
        QTableView::item {{ padding:5px 9px; border-bottom:1px solid #EDF1ED; }}
        QTableView::item:hover {{ background:#F3F8F3; }}
        QTableView::item:selected {{ background:#E3F0E3; color:{PALETTE['ink']}; }}
        QHeaderView::section {{ background:#F5F8F5; color:{PALETTE['muted']}; border:0; border-bottom:1px solid {PALETTE['line']}; padding:10px 9px; font-size:{TYPE['caption']}px; font-weight:650; }}
        QProgressBar {{ background:#E6EDE7; border:0; border-radius:3px; height:6px; text-align:center; color:transparent; }}
        QProgressBar::chunk {{ background:{PALETTE['moss']}; border-radius:3px; }}
        QScrollArea {{ border:0; background:transparent; }}
        QScrollBar:vertical {{ background:transparent; width:10px; margin:3px 2px; }}
        QScrollBar::handle:vertical {{ background:#B8C7BB; min-height:32px; border-radius:4px; }}
        QScrollBar::handle:vertical:hover {{ background:#91A995; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QToolTip {{ background:{PALETTE['moss_dark']}; color:white; border:0; padding:6px 8px; border-radius:4px; font-size:{TYPE['caption']}px; }}
        """
    )
