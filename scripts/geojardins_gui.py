"""Launcher explícito para PyInstaller; evita depender de diretório corrente."""

from geojardins.gui.app import main


if __name__ == "__main__":
    raise SystemExit(main())
