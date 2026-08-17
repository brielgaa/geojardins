"""Ponto de entrada seguro da aplicação gráfica e do smoke test empacotado."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from ..area_lookup import load_geojson
from ..fiscais import FiscalResolver
from ..runtime import log_directory, resource_path
from ..settings import load_settings
from ..user_config import UserConfigStore
from .main_window import MainWindow
from .theme import apply_theme
from .widgets.operational_map import OperationalMapView
from .widgets.social_link import GITHUB_URL, LINKEDIN_URL, SocialLinkButton, asset_path


def configure_logging() -> str:
    log_path = log_directory() / "geojardins.log"
    root = logging.getLogger()
    if not any(isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == str(log_path) for handler in root.handlers):
        try:
            handler = RotatingFileHandler(log_path, maxBytes=1_500_000, backupCount=3, encoding="utf-8")
        except OSError:
            # O perfil do usuÃ¡rio pode estar indisponÃ­vel ou o log pode estar em uso.
            try:
                fallback_dir = Path(tempfile.gettempdir()) / "GeoJardins-logs"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                fallback_path = fallback_dir / f"geojardins-{os.getpid()}.log"
                handler = RotatingFileHandler(fallback_path, maxBytes=1_500_000, backupCount=3, encoding="utf-8")
            except OSError:
                root.addHandler(logging.NullHandler())
                root.setLevel(logging.INFO)
                return "logs indisponÃ­veis"
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return str(next((Path(handler.baseFilename) for handler in root.handlers if isinstance(handler, RotatingFileHandler)), log_path))


def validate_runtime_resources() -> None:
    """Verifica recursos do bundle sem acionar rede ou geocodificação."""

    areas = resource_path("data", "processed", "areas_jardins_partition.geojson")
    boundary = resource_path("data", "processed", "ugr_jardins_boundary_partition.geojson")
    settings = resource_path("config", "settings.json")
    fiscais = resource_path("config", "fiscais.json")
    load_geojson(areas)
    load_geojson(boundary)
    load_settings(settings)
    FiscalResolver.from_path(fiscais)
    required_assets = (
        "geojardins.svg", "geojardins.ico", "dashboard.svg", "lookup.svg", "batch.svg",
        "fiscais.svg", "settings.svg", "location.svg", "operational_map.png",
        "operational_map_overlays.json", "github.svg", "linkedin.svg", "copy.svg",
        "search.svg", "zoom-in.svg", "zoom-out.svg", "fit.svg", "chevron-down.svg",
    )
    for asset in required_assets:
        if not asset_path(asset).is_file():
            raise RuntimeError(f"Asset visual ausente no bundle: {asset}")
    operational_map = OperationalMapView()
    if len(operational_map.areas) != 6:
        raise RuntimeError("Mapa operacional incompleto no bundle.")
    operational_map.deleteLater()


def validate_social_links() -> None:
    """Exercita os links empacotados sem abrir o navegador no smoke test."""

    opened: list[str] = []
    original = QDesktopServices.openUrl
    QDesktopServices.openUrl = lambda url: opened.append(url.toString()) or True  # type: ignore[method-assign]
    try:
        github = SocialLinkButton("github", compact=True, dark_surface=True)
        linkedin = SocialLinkButton("linkedin")
        github.click()
        linkedin.click()
        if opened != [GITHUB_URL, LINKEDIN_URL]:
            raise RuntimeError("Os links sociais empacotados não apontam para os perfis esperados.")
        if not github.accessibleName() or not linkedin.accessibleName():
            raise RuntimeError("Os controles sociais empacotados não estão acessíveis.")
    finally:
        QDesktopServices.openUrl = original  # type: ignore[method-assign]


def _show_fatal(log_path: str) -> None:
    QMessageBox.critical(
        None,
        "GeoJardins",
        f"Não foi possível iniciar o GeoJardins.\n\nConsulte o log em:\n{log_path}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    log_path = configure_logging()
    try:
        if args.smoke_test:
            app = QApplication(["GeoJardins", "-platform", "offscreen"])
            validate_runtime_resources()
            validate_social_links()
            app.quit()
            return 0
        app = QApplication(sys.argv if argv is None else ["GeoJardins", *argv])
        app.setApplicationName("GeoJardins")
        apply_theme(app)
        window = MainWindow(UserConfigStore())
        window.show()
        return app.exec()
    except Exception:
        details = traceback.format_exc()
        logging.getLogger(__name__).exception("Falha inesperada ao iniciar o GeoJardins")
        # O executável não tem console: em uma falha precoce, garante diagnóstico
        # mesmo se o handler rotativo do sistema não puder ser inicializado.
        try:
            Path(log_path).write_text(details, encoding="utf-8")
        except OSError:
            pass
        try:
            app = QApplication.instance() or QApplication(["GeoJardins"])
            _show_fatal(log_path)
            app.quit()
        except Exception:
            pass
        return 1
