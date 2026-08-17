"""Inspeção estrutural do PDF de áreas da UGR Jardins."""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf

from .geometry import GeometryExtractionError, geometry_from_drawing, rgb_to_hex

LOGGER = logging.getLogger(__name__)
AREA_LABEL_PATTERN = re.compile(r"^Tomé_(\d+)$")


@dataclass(frozen=True)
class LegendEntry:
    """Uma amostra de cor da legenda ligada a uma área Tomé."""

    area: str
    color_hex: str
    drawing_index: int
    bbox: tuple[float, float, float, float]


def _bbox_dict(rect: pymupdf.Rect) -> dict[str, float]:
    return {"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1}


def _iter_text_spans(page: pymupdf.Page) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                spans.append(span)
    return spans


def detect_legend_entries(page: pymupdf.Page, drawings: list[dict[str, Any]]) -> list[LegendEntry]:
    """Associa amostras retangulares da legenda aos rótulos Tomé, sem heurística de mapa."""

    label_spans = []
    for span in _iter_text_spans(page):
        text = span["text"].strip()
        if AREA_LABEL_PATTERN.fullmatch(text):
            label_spans.append((text, pymupdf.Rect(span["bbox"])))

    swatches: list[tuple[int, dict[str, Any]]] = []
    for index, drawing in enumerate(drawings):
        rect = drawing.get("rect")
        if (
            drawing.get("type") == "f"
            and drawing.get("fill")
            and rect is not None
            and rect.x0 < page.rect.width * 0.25
            and rect.y0 > page.rect.height * 0.75
            and len(drawing.get("items", [])) == 4
        ):
            swatches.append((index, drawing))

    entries: list[LegendEntry] = []
    used_labels: set[str] = set()
    for drawing_index, drawing in swatches:
        rect = drawing["rect"]
        matching_labels = [
            (abs(label_rect.y0 + label_rect.height / 2 - (rect.y0 + rect.height / 2)), text, label_rect)
            for text, label_rect in label_spans
            if text not in used_labels and label_rect.x0 >= rect.x1
        ]
        if not matching_labels:
            continue
        distance, area, label_rect = min(matching_labels, key=lambda value: value[0])
        if distance > max(rect.height, label_rect.height):
            continue
        used_labels.add(area)
        entries.append(
            LegendEntry(
                area=area,
                color_hex=rgb_to_hex(drawing["fill"]),
                drawing_index=drawing_index,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
            )
        )

    return sorted(entries, key=lambda entry: int(entry.area.split("_")[1]))


def _summary_for_color(
    drawings: list[dict[str, Any]],
    color_hex: str,
    legend_indexes: set[int],
) -> dict[str, Any]:
    matching = [
        (index, drawing)
        for index, drawing in enumerate(drawings)
        if drawing.get("fill") and rgb_to_hex(drawing["fill"]) == color_hex
    ]
    non_legend = [(index, drawing) for index, drawing in matching if index not in legend_indexes]
    rects = [drawing["rect"] for _, drawing in non_legend]
    bbox = None
    if rects:
        bbox = pymupdf.Rect(rects[0])
        for rect in rects[1:]:
            bbox.include_rect(rect)

    approximate_area = 0.0
    extraction_errors: list[str] = []
    type_counts: Counter[str] = Counter()
    for index, drawing in non_legend:
        type_counts[drawing.get("type", "unknown")] += 1
        try:
            approximate_area += geometry_from_drawing(drawing).area
        except GeometryExtractionError as exc:
            extraction_errors.append(f"drawing {index}: {exc}")

    return {
        "fill_color": color_hex,
        "element_count": len(non_legend),
        "legend_swatch_count": len(matching) - len(non_legend),
        "bbox_pdf": _bbox_dict(bbox) if bbox else None,
        "approximate_area_pdf_points_squared": approximate_area,
        "graphic_types": dict(type_counts),
        "geometry_errors": extraction_errors,
    }


def inspect_pdf(pdf_path: Path) -> dict[str, Any]:
    """Retorna um relatório JSON-serializável da estrutura e dos preenchimentos do PDF."""

    document = pymupdf.open(pdf_path)
    if len(document) != 1:
        raise ValueError(f"Esperado PDF de uma página; encontrado {len(document)} páginas.")

    page = document[0]
    drawings = page.get_drawings(extended=True)
    legend_entries = detect_legend_entries(page, drawings)
    legend_indexes = {entry.drawing_index for entry in legend_entries}
    fill_colors = sorted(
        {rgb_to_hex(drawing["fill"]) for drawing in drawings if drawing.get("fill")}
    )
    drawing_types = Counter(drawing.get("type", "unknown") for drawing in drawings)
    path_operations = Counter(
        item[0] for drawing in drawings for item in drawing.get("items", [])
    )
    text_spans = _iter_text_spans(page)

    with pikepdf.open(pdf_path) as pike_document:
        resources = pike_document.pages[0].obj.get("/Resources", {})
        xobjects = resources.get("/XObject", {})
        resource_keys = [str(key) for key in resources.keys()]
        xobject_names = [str(key) for key in xobjects.keys()]
        font_names = [str(key) for key in resources.get("/Font", {}).keys()]

    area_by_color = {entry.color_hex: entry.area for entry in legend_entries}
    fill_report = []
    for color_hex in fill_colors:
        color_summary = _summary_for_color(drawings, color_hex, legend_indexes)
        color_summary["area_from_legend"] = area_by_color.get(color_hex)
        fill_report.append(color_summary)

    return {
        "source": pdf_path.name,
        "page_count": len(document),
        "page_bbox_pdf": _bbox_dict(page.rect),
        "pdf_object_count": document.xref_length(),
        "structural_summary": {
            "vector_drawing_count": len(drawings),
            "drawing_types": dict(drawing_types),
            "path_operations": dict(path_operations),
            "vector_text_span_count": len(text_spans),
            "raster_image_count": len(page.get_images(full=True)),
            "has_clipping_paths": drawing_types.get("clip", 0) > 0,
            "clipping_path_count": drawing_types.get("clip", 0),
            "resource_keys": resource_keys,
            "xobject_names": xobject_names,
            "font_names": font_names,
        },
        "legend": [
            {
                "area": entry.area,
                "fill_color": entry.color_hex,
                "drawing_index": entry.drawing_index,
                "bbox_pdf": {
                    "x0": entry.bbox[0],
                    "y0": entry.bbox[1],
                    "x1": entry.bbox[2],
                    "y1": entry.bbox[3],
                },
            }
            for entry in legend_entries
        ],
        "fill_colors": fill_report,
    }


def write_inspection_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_source_pdf(pdf_path: Path, output_path: Path, scale: float = 0.7) -> None:
    """Renderiza a página original para comparação visual do diagnóstico."""

    document = pymupdf.open(pdf_path)
    pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(output_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspeciona a estrutura vetorial de um PDF GeoJardins.")
    parser.add_argument("pdf", type=Path, help="PDF a inspecionar")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output/debug/pdf_inspection.json"),
        help="Arquivo JSON do relatório.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    report = inspect_pdf(args.pdf)
    write_inspection_report(report, args.report)
    LOGGER.info("Relatório salvo em %s", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
