"""Extração das áreas Tomé a partir dos preenchimentos vetoriais do PDF."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

# Mantém o cache de fontes dentro do projeto, inclusive em ambientes restritos.
os.environ.setdefault("MPLCONFIGDIR", str((Path.cwd() / ".cache" / "matplotlib").resolve()))

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MatplotlibPolygon
import pymupdf
from shapely.geometry import MultiPolygon, Polygon, mapping, shape

from .geometry import GeometryExtractionError, dissolve_geometries, geometry_from_drawing, rgb_to_hex
from .pdf_inspector import (
    LegendEntry,
    detect_legend_entries,
    inspect_pdf,
    render_source_pdf,
    write_inspection_report,
)

LOGGER = logging.getLogger(__name__)


class AreaExtractionError(RuntimeError):
    """Não foi possível recuperar as áreas do PDF com confiança."""


def resolve_pdf_path(requested_path: Path | None = None) -> Path:
    """Resolve o PDF solicitado e aceita o nome acentuado fornecido no repositório."""

    if requested_path is not None:
        if not requested_path.is_file():
            raise FileNotFoundError(f"PDF não encontrado: {requested_path}")
        return requested_path

    raw_directory = Path("data/raw")
    canonical_path = raw_directory / "AREA_UGR_JARDINS.pdf"
    if canonical_path.is_file():
        return canonical_path

    pdf_files = sorted({path.resolve() for path in [*raw_directory.glob("*.pdf"), *raw_directory.glob("*.PDF")]})
    if len(pdf_files) == 1:
        return pdf_files[0]
    if not pdf_files:
        raise FileNotFoundError("Nenhum PDF encontrado em data/raw/.")
    raise AreaExtractionError(
        "Há mais de um PDF em data/raw/. Informe explicitamente --pdf para evitar uma extração ambígua."
    )


def _area_mapping_from_legend(entries: list[LegendEntry]) -> tuple[dict[str, str], str]:
    area_names = {entry.area for entry in entries}
    colors = {entry.color_hex for entry in entries}
    expected_names = {f"Tomé_{index}" for index in range(1, 7)}
    if area_names == expected_names and len(colors) == 6:
        return ({entry.color_hex: entry.area for entry in entries}, "legend_vector_text")
    return ({}, "unavailable")


def _generic_color_mapping(drawings: list[dict[str, Any]], legend_indexes: set[int]) -> dict[str, str]:
    colors = sorted(
        {
            rgb_to_hex(drawing["fill"])
            for index, drawing in enumerate(drawings)
            if drawing.get("fill") and index not in legend_indexes
        }
    )
    return {color: f"area_color_{index:02d}" for index, color in enumerate(colors, start=1)}


def _polygon_parts(geometry: Polygon | MultiPolygon) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    return list(geometry.geoms)


def extract_areas(pdf_path: Path) -> dict[str, Any]:
    """Extrai áreas coloridas e retorna uma FeatureCollection em coordenadas PDF."""

    document = pymupdf.open(pdf_path)
    if len(document) != 1:
        raise AreaExtractionError(f"Esperado PDF de uma página; encontrado {len(document)} páginas.")
    page = document[0]
    drawings = page.get_drawings(extended=True)
    legend_entries = detect_legend_entries(page, drawings)
    legend_indexes = {entry.drawing_index for entry in legend_entries}
    area_by_color, association_method = _area_mapping_from_legend(legend_entries)
    if not area_by_color:
        LOGGER.warning("Legenda vetorial incompleta; áreas serão identificadas somente por cor.")
        area_by_color = _generic_color_mapping(drawings, legend_indexes)

    paths_by_area: defaultdict[str, list[tuple[int, Any]]] = defaultdict(list)
    colors_by_area: dict[str, str] = {}
    skipped_paths: list[dict[str, Any]] = []
    for index, drawing in enumerate(drawings):
        if not drawing.get("fill") or index in legend_indexes:
            continue
        color_hex = rgb_to_hex(drawing["fill"])
        area = area_by_color.get(color_hex)
        if area is None:
            continue
        try:
            geometry = geometry_from_drawing(drawing)
        except GeometryExtractionError as exc:
            skipped_paths.append({"drawing_index": index, "reason": str(exc)})
            continue
        paths_by_area[area].append((index, geometry))
        colors_by_area[area] = color_hex

    if skipped_paths:
        raise AreaExtractionError(
            "Há caminhos preenchidos que não puderam ser convertidos sem alteração geométrica: "
            + "; ".join(f"#{item['drawing_index']}: {item['reason']}" for item in skipped_paths)
        )

    features: list[dict[str, Any]] = []
    for area in sorted(paths_by_area, key=lambda value: (value.startswith("area_color"), value)):
        source_paths = paths_by_area[area]
        try:
            geometry = dissolve_geometries(path_geometry for _, path_geometry in source_paths)
        except GeometryExtractionError as exc:
            raise AreaExtractionError(f"Falha ao dissolver {area}: {exc}") from exc
        features.append(
            {
                "type": "Feature",
                "id": area.lower().replace("é", "e"),
                "properties": {
                    "area": area,
                    "source": pdf_path.name,
                    "coordinate_system": "pdf",
                    "pdf_coordinate_convention": "points; origin at top-left; x right; y down",
                    "fill_color": colors_by_area[area],
                    "extraction_method": (
                        "PyMuPDF vector filled paths grouped by legend color; "
                        "make_valid topology repair and topology-preserving union without simplification"
                    ),
                    "source_path_count": len(source_paths),
                    "source_drawing_indexes": [index for index, _ in source_paths],
                    "geometry_type_before_dissolve": sorted(
                        {path_geometry.geom_type for _, path_geometry in source_paths}
                    ),
                    "area_pdf_points_squared": geometry.area,
                },
                "geometry": mapping(geometry),
            }
        )

    expected_area_count = 6 if association_method == "legend_vector_text" else None
    if expected_area_count is not None and len(features) != expected_area_count:
        raise AreaExtractionError(
            f"A legenda indica seis áreas, mas foram recuperadas {len(features)} geometrias."
        )

    return {
        "type": "FeatureCollection",
        "name": "areas_pdf_coordinates",
        "metadata": {
            "source": pdf_path.name,
            "coordinate_system": "pdf",
            "pdf_coordinate_convention": "points; origin at top-left; x right; y down",
            "area_color_association": association_method,
            "legend_entries_detected": len(legend_entries),
            "georeferenced": False,
        },
        "features": features,
    }


def write_geojson(collection: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _add_geometry(ax: Any, geometry: Polygon | MultiPolygon, color: str) -> None:
    for polygon in _polygon_parts(geometry):
        exterior = MatplotlibPolygon(
            list(polygon.exterior.coords), closed=True, facecolor=color, edgecolor="#27313d", alpha=0.45, linewidth=1.2
        )
        ax.add_patch(exterior)
        for interior in polygon.interiors:
            ax.add_patch(
                MatplotlibPolygon(
                    list(interior.coords), closed=True, facecolor="white", edgecolor="#27313d", alpha=1.0, linewidth=0.8
                )
            )


def render_area_debug_image(collection: dict[str, Any], output_path: Path) -> None:
    """Desenha somente as áreas extraídas, suas bordas e seus rótulos."""

    features = collection["features"]
    if not features:
        raise AreaExtractionError("Não há features para desenhar.")

    figure, axis = plt.subplots(figsize=(10, 14), dpi=180)
    all_bounds = []
    for feature in features:
        geometry = shape(feature["geometry"])
        color = feature["properties"]["fill_color"]
        _add_geometry(axis, geometry, color)
        all_bounds.append(geometry.bounds)
        point = geometry.representative_point()
        axis.text(
            point.x,
            point.y,
            feature["properties"]["area"],
            ha="center",
            va="center",
            fontsize=9,
            color="#111827",
            weight="bold",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "none", "alpha": 0.75},
        )

    min_x = min(bounds[0] for bounds in all_bounds)
    min_y = min(bounds[1] for bounds in all_bounds)
    max_x = max(bounds[2] for bounds in all_bounds)
    max_y = max(bounds[3] for bounds in all_bounds)
    padding_x = (max_x - min_x) * 0.03
    padding_y = (max_y - min_y) * 0.03
    axis.set_xlim(min_x - padding_x, max_x + padding_x)
    axis.set_ylim(max_y + padding_y, min_y - padding_y)
    axis.set_aspect("equal")
    axis.axis("off")
    figure.tight_layout(pad=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight", pad_inches=0.02, facecolor="white")
    plt.close(figure)


def run_extraction(
    pdf_path: Path | None = None,
    geojson_path: Path = Path("data/interim/areas_pdf_coordinates.geojson"),
    report_path: Path = Path("output/debug/pdf_inspection.json"),
    area_image_path: Path = Path("output/debug/areas_extracted.png"),
    source_image_path: Path = Path("output/debug/pdf_elements.png"),
) -> dict[str, Any]:
    """Executa inspeção, extração e geração de visualizações de validação."""

    source_pdf = resolve_pdf_path(pdf_path)
    report = inspect_pdf(source_pdf)
    write_inspection_report(report, report_path)
    collection = extract_areas(source_pdf)
    write_geojson(collection, geojson_path)
    render_area_debug_image(collection, area_image_path)
    render_source_pdf(source_pdf, source_image_path)
    LOGGER.info("%s áreas extraídas de %s", len(collection["features"]), source_pdf)
    return collection


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extrai áreas Tomé vetoriais de um PDF UGR Jardins.")
    parser.add_argument("--pdf", type=Path, help="PDF-fonte; por padrão, detectado em data/raw/.")
    parser.add_argument(
        "--output", type=Path, default=Path("data/interim/areas_pdf_coordinates.geojson"), help="GeoJSON de saída."
    )
    parser.add_argument(
        "--report", type=Path, default=Path("output/debug/pdf_inspection.json"), help="Relatório de inspeção."
    )
    parser.add_argument(
        "--areas-image", type=Path, default=Path("output/debug/areas_extracted.png"), help="Imagem das áreas."
    )
    parser.add_argument(
        "--pdf-image", type=Path, default=Path("output/debug/pdf_elements.png"), help="Renderização do PDF-fonte."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        run_extraction(args.pdf, args.output, args.report, args.areas_image, args.pdf_image)
    except (AreaExtractionError, FileNotFoundError, ValueError) as exc:
        LOGGER.error("Extração interrompida: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
