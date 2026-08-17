"""Caminhos de runtime compartilhados por CLI, GUI e PyInstaller.

Os dados canônicos são sempre somente leitura. Configurações de cada usuário
ficam fora do bundle para que a distribuição portátil possa ser copiada sem
permissões administrativas.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "GeoJardins"


def application_root() -> Path:
    """Raiz dos recursos em desenvolvimento ou dentro de um bundle PyInstaller."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Resolve recurso somente leitura sem depender do diretório corrente."""

    return application_root().joinpath(*parts)


def local_app_data_dir() -> Path:
    """Diretório gravável do usuário, sem escrita ao lado do executável."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_path() -> Path:
    return local_app_data_dir() / "config.json"


def log_directory() -> Path:
    path = local_app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def geocoding_cache_path() -> Path:
    """Resolve o cache de geocodifica??o sem depender do diret?rio atual."""

    directory = (local_app_data_dir() / "cache") if getattr(sys, "frozen", False) else resource_path("data", "cache")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "geocoding_cache.sqlite"
