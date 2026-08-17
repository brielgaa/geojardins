"""Gera os assets offline do mapa operacional a partir das fontes já validadas.

O raster preserva a cartografia e os logradouros do PDF original. Os overlays
permanecem em coordenadas PDF extraídas, que compartilham exatamente a origem e
escala da página renderizada; não há ajuste manual ou alteração do GIS canônico.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "geojardins" / "gui" / "assets"
PDF = next((ROOT / "data" / "raw").glob("*.pdf"))
OVERLAYS = ROOT / "data" / "interim" / "areas_pdf_coordinates.geojson"
SCALE = 0.7


def generate_map() -> None:
    document = pymupdf.open(PDF)
    try:
        if len(document) != 1:
            raise RuntimeError("O mapa operacional exige um PDF de uma página.")
        page = document[0]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=False)
        map_path = ASSETS / "operational_map.png"
        pixmap.save(map_path)
        collection = json.loads(OVERLAYS.read_text(encoding="utf-8"))
        metadata = {
            "source": PDF.name,
            "coordinate_system": "pdf",
            "pdf_coordinate_convention": "points; origin at top-left; x right; y down",
            "page_bounds": [page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1],
            "image_size": [pixmap.width, pixmap.height],
            "scale_x": pixmap.width / page.rect.width,
            "scale_y": pixmap.height / page.rect.height,
            "features": [
                {
                    "area": feature["properties"]["area"],
                    "fill_color": feature["properties"].get("fill_color", "#DDE5DE"),
                    "geometry": feature["geometry"],
                }
                for feature in collection["features"]
            ],
        }
        (ASSETS / "operational_map_overlays.json").write_text(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    finally:
        document.close()


def generate_icon() -> None:
    source = (ASSETS / "geojardins.svg").read_text(encoding="utf-8")
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save(str(ASSETS / "geojardins.ico"), "ICO"):
        raise RuntimeError("Não foi possível gerar o ícone ICO do GeoJardins.")


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    generate_map()
    generate_icon()
