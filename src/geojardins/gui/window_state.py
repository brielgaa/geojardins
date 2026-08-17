"""Persistência de janela no perfil do usuário, fora do bundle portátil."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow

from ..runtime import local_app_data_dir


def state_path() -> Path:
    return local_app_data_dir() / "window_state.json"


def _safe_rect(rect: QRect) -> bool:
    return any(rect.intersects(screen.availableGeometry()) for screen in QGuiApplication.screens())


def restore_window(window: QMainWindow, path: Path | None = None) -> bool:
    target = path or state_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        position = raw["position"]
        size = raw["size"]
        rect = QRect(QPoint(int(position[0]), int(position[1])), QSize(int(size[0]), int(size[1])))
        if not _safe_rect(rect):
            return False
        window.resize(rect.size())
        window.move(rect.topLeft())
        if bool(raw.get("maximized")):
            window.showMaximized()
        return True
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False


def save_window(window: QMainWindow, path: Path | None = None) -> None:
    target = path or state_path()
    geometry = window.normalGeometry() if window.isMaximized() else window.geometry()
    data = {
        "position": [geometry.x(), geometry.y()],
        "size": [geometry.width(), geometry.height()],
        "maximized": window.isMaximized(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(target)
