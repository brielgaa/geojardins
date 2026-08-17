"""Renderiza as telas principais em viewports de validacao, sem rede."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import QApplication

from capture_ui_review_final import sample_result
from geojardins.address_lookup import LookupStatus
from geojardins.gui import main_window as main_window_module
from geojardins.gui.main_window import MainWindow
from geojardins.gui.theme import apply_theme
from geojardins.user_config import UserConfigStore


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "ui_review_responsive"
VIEWPORTS = ((1366, 768), (1600, 900), (2560, 1440))
PAGES = (("dashboard", 0), ("consulta", 1), ("lote", 2), ("fiscais", 3), ("configuracoes", 4))


def main() -> int:
    scale = os.environ.get("QT_SCALE_FACTOR", "1.0").replace(".", "_")
    destination = OUTPUT / f"scale_{scale}"
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication(["GeoJardins-responsive", "-platform", "offscreen"])
    apply_theme(app)
    main_window_module.save_window = lambda _window: None
    window = MainWindow(UserConfigStore(destination / "review_config.json"))
    window.individual.result_panel.show_result(sample_result(LookupStatus.CONFIRMADO))
    window.fiscais.select_area("Tomé_4")

    for width, height in VIEWPORTS:
        window.resize(width, height)
        window.show()
        for name, page_index in PAGES:
            window.show_page(page_index)
            QApplication.processEvents()
            view = window.stack.currentWidget()
            if not view.isVisible() or view.width() <= 0 or view.height() <= 0:
                raise RuntimeError(f"Viewport invalido para {name} em {width}x{height}.")
            image = window.grab()
            if image.isNull():
                raise RuntimeError(f"Falha na captura de {name} em {width}x{height}.")
            image.save(str(destination / f"{width}x{height}_{name}.png"))

    window.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
