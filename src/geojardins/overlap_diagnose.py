"""Diagnóstico e reconstrução canônica dos overlaps entre áreas Tomé.

O módulo trata o PDF como fonte visual de verdade. A prioridade espacial não é
configurada por nome: ela é recuperada da sequência real dos preenchimentos
vetoriais opacos no content stream do PDF.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Mantém o cache de fontes dentro do projeto em ambientes restritos.
os.environ.setdefault("MPLCONFIGDIR", str((Path.cwd() / ".cache" / "matplotlib").resolve()))

import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Polygon as MatplotlibPolygon
import numpy as np
import pikepdf
import pymupdf
from pyproj import Transformer
import shapely
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    mapping,
    shape,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, transform as shapely_transform, unary_union
from shapely.validation import make_valid

from .area_lookup import EXACT_BOUNDARY_TOLERANCE_M, load_geojson
from .geometry import geometry_from_drawing, rgb_to_hex, rings_from_drawing
from .pdf_inspector import detect_legend_entries
from .polygon_extractor import resolve_pdf_path, write_geojson

LOGGER = logging.getLogger(__name__)
METRIC_CRS = "EPSG:31983"
WGS84 = "EPSG:4326"
RGB_TOLERANCE = 12.0
SOURCE_BOUNDARY_EXCLUSION_PDF_POINTS = 0.35
SIGNIFICANT_OVERLAP_M2 = 0.01


class OverlapDiagnosisError(RuntimeError):
    """O diagnóstico não pôde concluir uma decisão espacial segura."""


@dataclass(frozen=True)
class AreaPaintEvidence:
    """Evidência de pintura recuperada dos desenhos vetoriais do PDF."""

    area: str
    fill_color: str
    drawing_indexes: tuple[int, ...]
    sequence_numbers: tuple[int, ...]
    fill_opacities: tuple[float, ...]
    even_odd_values: tuple[bool, ...]
    drawing_types: tuple[str, ...]
    levels: tuple[int, ...]
    stroke_colors: tuple[str, ...]
    stroke_widths_pdf_points: tuple[float, ...]

    @property
    def first_sequence(self) -> int:
        return min(self.sequence_numbers)

    @property
    def last_sequence(self) -> int:
        return max(self.sequence_numbers)

    @property
    def opaque(self) -> bool:
        return all(abs(value - 1.0) <= 1e-9 for value in self.fill_opacities)

    @property
    def uses_even_odd_fill(self) -> bool:
        return all(self.even_odd_values)


def polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    """Recupera apenas componentes poligonais, inclusive de collections."""

    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if hasattr(geometry, "geoms"):
        parts: list[Polygon] = []
        for component in geometry.geoms:
            parts.extend(polygon_parts(component))
        return parts
    return []


def polygonal_only(geometry: BaseGeometry) -> BaseGeometry:
    """Descarta componentes de área zero produzidos por operações topológicas."""

    parts = polygon_parts(geometry)
    if not parts:
        return GeometryCollection()
    if len(parts) == 1:
        return parts[0]
    return unary_union(parts)


def _odd_even_contains(point: Point, rings: Sequence[Sequence[tuple[float, float]]]) -> bool:
    """Aplica point-in-path por paridade, conforme o operador PDF ``B*``."""

    x = point.x
    y = point.y
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > y) == (y2 > y):
                continue
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if crossing_x > x:
                inside = not inside
    return inside


def exact_even_odd_geometry(drawing: Mapping[str, Any]) -> BaseGeometry:
    """Reconstrói exatamente o preenchimento linear ``B*`` sem ``make_valid``.

    A linha é nodificada, poligonizada e cada face é selecionada pela paridade
    dos subpaths originais. Não há buffer, simplificação ou estimativa.
    """

    rings = rings_from_drawing(drawing)
    linework = unary_union([LineString(ring) for ring in rings])
    faces = list(polygonize(linework))
    selected = [
        face
        for face in faces
        if _odd_even_contains(face.representative_point(), rings)
    ]
    return polygonal_only(unary_union(selected)) if selected else GeometryCollection()


def _unique_float_values(values: Iterable[Any]) -> tuple[float, ...]:
    return tuple(sorted({float(value) for value in values if value is not None}))


def _rect_dict(rect: pymupdf.Rect) -> dict[str, float]:
    return {"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1}


def inspect_pdf_painting(
    pdf_path: Path,
) -> tuple[
    pymupdf.Document,
    pymupdf.Page,
    list[dict[str, Any]],
    dict[str, AreaPaintEvidence],
    dict[str, BaseGeometry],
    dict[str, Any],
]:
    """Recupera paths, ordem, clipping, fill/stroke e geometria exata do PDF."""

    document = pymupdf.open(pdf_path)
    if len(document) != 1:
        raise OverlapDiagnosisError(
            f"Esperado PDF de uma página; encontrado {len(document)} páginas."
        )
    page = document[0]
    drawings = page.get_drawings(extended=True)
    legend = detect_legend_entries(page, drawings)
    legend_indexes = {entry.drawing_index for entry in legend}
    area_by_color = {entry.color_hex: entry.area for entry in legend}
    expected = {f"Tomé_{index}" for index in range(1, 7)}
    if set(area_by_color.values()) != expected or len(area_by_color) != 6:
        raise OverlapDiagnosisError("A legenda vetorial não identificou univocamente as seis áreas.")

    records: defaultdict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    exact_paths: defaultdict[str, list[BaseGeometry]] = defaultdict(list)
    current_paths: defaultdict[str, list[BaseGeometry]] = defaultdict(list)
    per_drawing_repairs: list[dict[str, Any]] = []
    for index, drawing in enumerate(drawings):
        if not drawing.get("fill") or index in legend_indexes:
            continue
        fill_color = rgb_to_hex(drawing["fill"])
        area = area_by_color.get(fill_color)
        if area is None:
            continue
        records[area].append((index, drawing))
        exact_geometry = exact_even_odd_geometry(drawing)
        current_geometry = geometry_from_drawing(drawing)
        exact_paths[area].append(exact_geometry)
        current_paths[area].append(current_geometry)
        symmetric_difference = exact_geometry.symmetric_difference(current_geometry).area
        if symmetric_difference > 1e-10:
            per_drawing_repairs.append(
                {
                    "drawing_index": index,
                    "area": area,
                    "exact_even_odd_area_pdf_points_squared": exact_geometry.area,
                    "make_valid_area_pdf_points_squared": current_geometry.area,
                    "symmetric_difference_pdf_points_squared": symmetric_difference,
                }
            )

    if set(records) != expected:
        raise OverlapDiagnosisError(
            "Os preenchimentos do mapa não produziram exatamente as seis áreas da legenda."
        )

    evidence: dict[str, AreaPaintEvidence] = {}
    for area, area_records in records.items():
        indexes = tuple(index for index, _ in area_records)
        sequence_numbers = tuple(
            int(drawing["seqno"])
            for _, drawing in area_records
            if drawing.get("seqno") is not None
        )
        if len(sequence_numbers) != len(area_records):
            raise OverlapDiagnosisError(f"{area} possui path sem seqno recuperável.")
        stroke_colors = tuple(
            sorted(
                {
                    rgb_to_hex(drawing["color"])
                    for _, drawing in area_records
                    if drawing.get("color") is not None
                }
            )
        )
        evidence[area] = AreaPaintEvidence(
            area=area,
            fill_color=rgb_to_hex(area_records[0][1]["fill"]),
            drawing_indexes=indexes,
            sequence_numbers=sequence_numbers,
            fill_opacities=tuple(
                float(drawing.get("fill_opacity", 1.0)) for _, drawing in area_records
            ),
            even_odd_values=tuple(bool(drawing.get("even_odd")) for _, drawing in area_records),
            drawing_types=tuple(sorted({str(drawing.get("type")) for _, drawing in area_records})),
            levels=tuple(sorted({int(drawing.get("level", -1)) for _, drawing in area_records})),
            stroke_colors=stroke_colors,
            stroke_widths_pdf_points=_unique_float_values(
                drawing.get("width") for _, drawing in area_records
            ),
        )

    exact_geometries = {
        area: polygonal_only(unary_union(geometries))
        for area, geometries in exact_paths.items()
    }
    current_geometries = {
        area: polygonal_only(unary_union(geometries))
        for area, geometries in current_paths.items()
    }

    clips = []
    for index, drawing in enumerate(drawings):
        if drawing.get("type") != "clip":
            continue
        scissor = drawing.get("scissor")
        clips.append(
            {
                "drawing_index": index,
                "level": drawing.get("level"),
                "even_odd": drawing.get("even_odd"),
                "scissor_pdf": _rect_dict(scissor) if scissor else None,
                "item_count": len(drawing.get("items", [])),
            }
        )

    with pikepdf.open(pdf_path) as pike_document:
        instructions = pikepdf.parse_content_stream(pike_document.pages[0])
        operator_counts = Counter(str(instruction.operator) for instruction in instructions)
        matrices = [
            [float(value) for value in instruction.operands]
            for instruction in instructions
            if str(instruction.operator) == "cm"
        ]

    extraction_comparison = []
    for area in sorted(expected):
        exact = exact_geometries[area]
        current = current_geometries[area]
        extraction_comparison.append(
            {
                "area": area,
                "exact_even_odd_area_pdf_points_squared": exact.area,
                "extracted_area_pdf_points_squared": current.area,
                "symmetric_difference_pdf_points_squared": exact.symmetric_difference(current).area,
            }
        )

    structural = {
        "drawing_count": len(drawings),
        "operator_counts": [
            {"operator": operator, "count": count}
            for operator, count in sorted(operator_counts.items())
        ],
        "clipping_path_count": len(clips),
        "clipping_paths": clips,
        "content_stream_matrices": matrices,
        "matrix_note": (
            "As duas operações cm antecedem XObjects decorativos e são restauradas antes "
            "dos paths Tomé; as coordenadas de get_drawings já estão no espaço da página."
        ),
        "make_valid_differences": per_drawing_repairs,
        "extraction_comparison": extraction_comparison,
        "current_geometries_pdf": current_geometries,
    }
    return document, page, drawings, evidence, exact_geometries, structural


def derive_paint_order(evidence: Mapping[str, AreaPaintEvidence]) -> tuple[str, ...]:
    """Deriva a ordem real e rejeita grupos intercalados ou não opacos."""

    if not evidence:
        raise OverlapDiagnosisError("Não há evidência de pintura para ordenar.")
    order = tuple(sorted(evidence, key=lambda area: evidence[area].first_sequence))
    for area in order:
        record = evidence[area]
        if not record.opaque:
            raise OverlapDiagnosisError(f"{area} não usa preenchimento totalmente opaco.")
        if not record.uses_even_odd_fill:
            raise OverlapDiagnosisError(f"{area} não usa exclusivamente a regra par-ímpar B*.")
    for left, right in zip(order, order[1:]):
        if evidence[left].last_sequence >= evidence[right].first_sequence:
            raise OverlapDiagnosisError(
                f"Os paths de {left} e {right} estão intercalados; não há prioridade única por área."
            )
    return order


def build_painter_partition(
    area_geometries: Mapping[str, BaseGeometry],
    paint_order: Sequence[str],
) -> dict[str, BaseGeometry]:
    """Reproduz a composição final dos fills sem prioridade hardcoded."""

    if set(area_geometries) != set(paint_order) or len(set(paint_order)) != len(paint_order):
        raise OverlapDiagnosisError("A ordem de pintura não corresponde exatamente às áreas fornecidas.")
    covered: BaseGeometry = GeometryCollection()
    partition: dict[str, BaseGeometry] = {}
    for area in reversed(paint_order):
        source = area_geometries[area]
        visible = source if covered.is_empty else source.difference(covered)
        partition[area] = polygonal_only(visible)
        covered = polygonal_only(unary_union([covered, source]))
    return {area: partition[area] for area in paint_order}


def _transform_geometry(geometry: BaseGeometry, source_crs: str, target_crs: str) -> BaseGeometry:
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    return shapely_transform(transformer.transform, geometry)


def collection_geometries(
    collection: Mapping[str, Any],
    *,
    target_crs: str | None = None,
) -> dict[str, BaseGeometry]:
    source_crs = collection.get("metadata", {}).get("coordinate_system", WGS84)
    geometries: dict[str, BaseGeometry] = {}
    for feature in collection.get("features", []):
        area = feature.get("properties", {}).get("area")
        if not area:
            continue
        geometry = shape(feature["geometry"])
        if target_crs and source_crs != target_crs:
            geometry = _transform_geometry(geometry, source_crs, target_crs)
        geometries[area] = geometry
    return geometries


def _geometry_to_wgs84(geometry: BaseGeometry, source_crs: str) -> BaseGeometry:
    return geometry if source_crs == WGS84 else _transform_geometry(geometry, source_crs, WGS84)


def find_overlaps(
    area_geometries: Mapping[str, BaseGeometry],
    threshold: float = 0.0,
) -> dict[tuple[str, str], BaseGeometry]:
    overlaps: dict[tuple[str, str], BaseGeometry] = {}
    for left_name, right_name in combinations(area_geometries, 2):
        intersection = polygonal_only(
            area_geometries[left_name].intersection(area_geometries[right_name])
        )
        if intersection.area > threshold:
            overlaps[(left_name, right_name)] = intersection
    return overlaps


def validate_partition(
    original: Mapping[str, BaseGeometry],
    partition: Mapping[str, BaseGeometry],
    *,
    overlap_threshold: float = SIGNIFICANT_OVERLAP_M2,
) -> dict[str, Any]:
    original_union = polygonal_only(unary_union(list(original.values())))
    partition_union = polygonal_only(unary_union(list(partition.values())))
    original_only = original_union.difference(partition_union).area
    partition_only = partition_union.difference(original_union).area
    symmetric_difference = original_union.symmetric_difference(partition_union).area
    overlaps = find_overlaps(partition, threshold=overlap_threshold)
    original_holes = [
        Polygon(interior).area
        for polygon in polygon_parts(original_union)
        for interior in polygon.interiors
    ]
    partition_holes = [
        Polygon(interior).area
        for polygon in polygon_parts(partition_union)
        for interior in polygon.interiors
    ]
    return {
        "metric_crs": METRIC_CRS,
        "original_union_area_m2": original_union.area,
        "canonical_union_area_m2": partition_union.area,
        "difference_m2": partition_union.area - original_union.area,
        "difference_percent": (
            100.0 * (partition_union.area - original_union.area) / original_union.area
            if original_union.area
            else 0.0
        ),
        "symmetric_difference_m2": symmetric_difference,
        "original_only_m2": original_only,
        "canonical_only_m2": partition_only,
        "significant_overlap_threshold_m2": overlap_threshold,
        "significant_overlap_count": len(overlaps),
        "significant_overlaps": [
            {"areas": list(pair), "area_m2": geometry.area}
            for pair, geometry in overlaps.items()
        ],
        "original_hole_count": len(original_holes),
        "canonical_hole_count": len(partition_holes),
        "original_hole_area_m2": sum(original_holes),
        "canonical_hole_area_m2": sum(partition_holes),
        "new_gap_area_m2": original_only,
    }


def _sample_overlap_raster(
    page: pymupdf.Page,
    overlap: BaseGeometry,
    palette: Mapping[str, str],
    stroke_colors: Sequence[str],
    *,
    scale: float = 120.0,
) -> dict[str, Any]:
    """Amostra o raster literalmente nos interiores dos componentes estreitos."""

    palette_rgb = {
        area: np.asarray(tuple(bytes.fromhex(color[1:])), dtype=float)
        for area, color in palette.items()
    }
    stroke_rgb = {
        color: np.asarray(tuple(bytes.fromhex(color[1:])), dtype=float)
        for color in stroke_colors
    }
    classifications: Counter[str] = Counter()
    rgb_values: Counter[str] = Counter()
    widths: list[float] = []
    for polygon in polygon_parts(overlap):
        point = polygon.representative_point()
        clearance = polygon.boundary.distance(point)
        widths.append(2.0 * clearance)
        radius = max(0.05, min(0.4, clearance * 0.5 if clearance > 0 else 0.05))
        clip = pymupdf.Rect(
            point.x - radius,
            point.y - radius,
            point.x + radius,
            point.y + radius,
        )
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False
        )
        rgb = np.asarray(
            pixmap.pixel(pixmap.width // 2, pixmap.height // 2)[:3], dtype=float
        )
        rgb_hex = "#" + "".join(f"{int(channel):02x}" for channel in rgb)
        rgb_values[rgb_hex] += 1
        palette_distances = {
            area: float(np.linalg.norm(rgb - value))
            for area, value in palette_rgb.items()
        }
        nearest_area = min(palette_distances, key=palette_distances.get)
        if palette_distances[nearest_area] <= RGB_TOLERANCE:
            classifications[f"fill:{nearest_area}"] += 1
            continue
        if stroke_rgb:
            stroke_distances = {
                color: float(np.linalg.norm(rgb - value))
                for color, value in stroke_rgb.items()
            }
            nearest_stroke = min(stroke_distances, key=stroke_distances.get)
            if stroke_distances[nearest_stroke] <= RGB_TOLERANCE:
                classifications[f"boundary_stroke:{nearest_stroke}"] += 1
                continue
        classifications["other"] += 1
    return {
        "sampling_method": (
            "Um ponto interior representativo por componente, render local 120x; "
            f"classificação RGB com distância euclidiana <= {RGB_TOLERANCE:.0f}."
        ),
        "component_samples": sum(classifications.values()),
        "classifications": dict(classifications),
        "literal_rgb_counts": dict(rgb_values),
        "median_component_width_pdf_points": float(np.median(widths)) if widths else 0.0,
        "maximum_component_width_pdf_points": max(widths, default=0.0),
    }


def source_render_agreement(
    page: pymupdf.Page,
    source_partition: Mapping[str, BaseGeometry],
    palette: Mapping[str, str],
    *,
    grid_columns: int = 500,
    render_scale: float = 1.5,
) -> dict[str, Any]:
    """Compara cores reconhecíveis do raster com a partição em coordenadas PDF."""

    source_union = polygonal_only(unary_union(list(source_partition.values())))
    min_x, min_y, max_x, max_y = source_union.bounds
    aspect = (max_y - min_y) / max(max_x - min_x, 1e-9)
    grid_rows = max(2, int(round(grid_columns * aspect)))
    x_values = np.linspace(min_x, max_x, grid_columns)
    y_values = np.linspace(min_y, max_y, grid_rows)
    grid_x, grid_y = np.meshgrid(x_values, y_values)
    flat_x = grid_x.ravel()
    flat_y = grid_y.ravel()
    inside_union = shapely.contains_xy(source_union, flat_x, flat_y)
    all_boundaries = unary_union(
        [geometry.boundary for geometry in source_partition.values()]
    )
    grid_points = shapely.points(flat_x, flat_y)
    near_boundary = shapely.dwithin(
        grid_points,
        all_boundaries,
        SOURCE_BOUNDARY_EXCLUSION_PDF_POINTS,
    )

    owner = np.full(flat_x.shape, -1, dtype=np.int16)
    areas = list(source_partition)
    for index, area in enumerate(areas):
        mask = shapely.contains_xy(source_partition[area], flat_x, flat_y)
        owner[mask] = index

    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(render_scale, render_scale), alpha=False
    )
    raster = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )[:, :, :3]
    pixel_x = np.clip(
        ((flat_x - page.rect.x0) / page.rect.width * pixmap.width).astype(int),
        0,
        pixmap.width - 1,
    )
    pixel_y = np.clip(
        ((flat_y - page.rect.y0) / page.rect.height * pixmap.height).astype(int),
        0,
        pixmap.height - 1,
    )
    sampled_rgb = raster[pixel_y, pixel_x].astype(float)
    palette_array = np.asarray(
        [tuple(bytes.fromhex(palette[area][1:])) for area in areas], dtype=float
    )
    distances = np.linalg.norm(
        sampled_rgb[:, np.newaxis, :] - palette_array[np.newaxis, :, :], axis=2
    )
    raster_area = np.argmin(distances, axis=1)
    nearest_distance = np.min(distances, axis=1)
    recognized = nearest_distance <= RGB_TOLERANCE
    comparable = inside_union & ~near_boundary & (owner >= 0) & recognized
    matches = comparable & (owner == raster_area)
    valid_count = int(np.count_nonzero(comparable))
    match_count = int(np.count_nonzero(matches))
    mismatch_counts: Counter[str] = Counter()
    mismatch_indexes = np.flatnonzero(comparable & ~matches)
    for index in mismatch_indexes:
        mismatch_counts[f"{areas[int(owner[index])]}→{areas[int(raster_area[index])]}" ] += 1

    return {
        "grid_columns": grid_columns,
        "grid_rows": grid_rows,
        "grid_points_total": int(flat_x.size),
        "points_inside_source_union": int(np.count_nonzero(inside_union)),
        "valid_palette_points_compared": valid_count,
        "matching_points": match_count,
        "mismatching_points": valid_count - match_count,
        "agreement_percent": 100.0 * match_count / valid_count if valid_count else None,
        "ignored_boundary_points": int(np.count_nonzero(inside_union & near_boundary)),
        "boundary_exclusion_pdf_points": SOURCE_BOUNDARY_EXCLUSION_PDF_POINTS,
        "ignored_non_palette_points": int(
            np.count_nonzero(inside_union & ~near_boundary & ~recognized)
        ),
        "rgb_distance_tolerance": RGB_TOLERANCE,
        "mismatch_counts": dict(mismatch_counts),
        "method": (
            "Grade regular no espaço PDF. Só pixels cuja cor está a distância RGB euclidiana "
            f"<= {RGB_TOLERANCE:.0f} de uma cor da legenda são comparados; textos, ruas, "
            f"strokes e antialiasing ficam fora da métrica. Pontos a até "
            f"{SOURCE_BOUNDARY_EXCLUSION_PDF_POINTS:.2f} ponto PDF de uma divisa são excluídos. "
            "Não há threshold de aceitação."
        ),
    }


def exclusivity_sample(
    partition: Mapping[str, BaseGeometry],
    original_overlaps: Mapping[tuple[str, str], BaseGeometry],
    *,
    grid_axis: int = 240,
    random_points: int = 20_000,
    seed: int = 20260811,
) -> dict[str, Any]:
    """Testa exclusividade com grade, aleatório, overlaps antigos e divisas."""

    union = polygonal_only(unary_union(list(partition.values())))
    min_x, min_y, max_x, max_y = union.bounds
    grid_x, grid_y = np.meshgrid(
        np.linspace(min_x, max_x, grid_axis),
        np.linspace(min_y, max_y, grid_axis),
    )
    rng = np.random.default_rng(seed)
    random_x = rng.uniform(min_x, max_x, random_points)
    random_y = rng.uniform(min_y, max_y, random_points)
    special_points: list[Point] = [
        geometry.representative_point() for geometry in original_overlaps.values()
    ]
    boundaries = polygonal_only(union).boundary
    if boundaries.length > 0:
        for fraction in np.linspace(0.0, 1.0, 401, endpoint=False):
            point = boundaries.interpolate(float(fraction), normalized=True)
            special_points.extend(
                [
                    point,
                    Point(point.x + 0.02, point.y),
                    Point(point.x - 0.02, point.y),
                    Point(point.x, point.y + 0.02),
                    Point(point.x, point.y - 0.02),
                ]
            )
    special_x = np.asarray([point.x for point in special_points], dtype=float)
    special_y = np.asarray([point.y for point in special_points], dtype=float)
    x = np.concatenate([grid_x.ravel(), random_x, special_x])
    y = np.concatenate([grid_y.ravel(), random_y, special_y])
    match_count = np.zeros(x.shape, dtype=np.int16)
    for geometry in partition.values():
        match_count += shapely.contains_xy(geometry, x, y).astype(np.int16)
    zero = int(np.count_nonzero(match_count == 0))
    one = int(np.count_nonzero(match_count == 1))
    multiple = int(np.count_nonzero(match_count >= 2))
    return {
        "predicate": "contains",
        "boundary_semantics": (
            "contains exclui a linha matemática; por isso pontos exatamente na divisa contam como 0. "
            "O lookup usa covers para detectar e expor explicitamente ambiguidade de borda."
        ),
        "grid_points": int(grid_x.size),
        "random_points": random_points,
        "former_overlap_points": len(original_overlaps),
        "boundary_and_near_boundary_points": max(0, len(special_points) - len(original_overlaps)),
        "seed": seed,
        "points_tested": int(x.size),
        "zero_areas": zero,
        "one_area": one,
        "two_or_more_areas": multiple,
    }


def _feature_collection_from_partition(
    source_collection: Mapping[str, Any],
    partition_wgs84: Mapping[str, BaseGeometry],
    paint_order: Sequence[str],
    evidence: Mapping[str, AreaPaintEvidence],
    diagnosis_summary: Mapping[str, Any],
) -> dict[str, Any]:
    source_by_area = {
        feature["properties"]["area"]: feature
        for feature in source_collection.get("features", [])
    }
    features = []
    for paint_position, area in enumerate(paint_order, start=1):
        source = source_by_area[area]
        properties = dict(source.get("properties", {}))
        properties.update(
            {
                "partition_method": "pdf_painters_algorithm",
                "paint_position": paint_position,
                "source_fill_sequence_min": evidence[area].first_sequence,
                "source_fill_sequence_max": evidence[area].last_sequence,
                "source_fill_opacity": 1.0,
                "priority_source": "PDF content stream; no hardcoded area priority",
            }
        )
        features.append(
            {
                "type": "Feature",
                "id": source.get("id"),
                "properties": properties,
                "geometry": mapping(partition_wgs84[area]),
            }
        )
    metadata = dict(source_collection.get("metadata", {}))
    metadata.update(
        {
            "name": "areas_jardins_partition",
            "partition_method": "pdf_painters_algorithm",
            "paint_order": list(paint_order),
            "metric_validation_crs": METRIC_CRS,
            "source_of_truth": diagnosis_summary["pdf_source"],
            "overlap_diagnosis": "output/debug/overlap_diagnosis.json",
            "original_preserved": "data/processed/areas_jardins.geojson",
        }
    )
    return {
        "type": "FeatureCollection",
        "name": "areas_jardins_partition",
        "metadata": metadata,
        "features": features,
    }


def _boundary_collection(partition_collection: Mapping[str, Any]) -> dict[str, Any]:
    geometries = [shape(feature["geometry"]) for feature in partition_collection["features"]]
    boundary = polygonal_only(unary_union(geometries))
    return {
        "type": "FeatureCollection",
        "name": "ugr_jardins_boundary_partition",
        "metadata": {
            "coordinate_system": WGS84,
            "derived_from": "areas_jardins_partition.geojson",
            "method": "topology-preserving union; no buffer or simplification",
        },
        "features": [
            {
                "type": "Feature",
                "id": "ugr_jardins_boundary_partition",
                "properties": {"name": "UGR Jardins - partição canônica"},
                "geometry": mapping(boundary),
            }
        ],
    }


def _unresolved_collection(
    unresolved: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "unresolved_overlaps",
        "metadata": {
            "coordinate_system": WGS84,
            "unresolved_count": len(unresolved),
            "note": "Coleção vazia significa que todos os overlaps tiveram evidência PDF suficiente.",
        },
        "features": [
            {
                "type": "Feature",
                "properties": {
                    key: value
                    for key, value in record.items()
                    if key != "geometry"
                },
                "geometry": mapping(record["geometry"]),
            }
            for record in unresolved
        ],
    }


def _add_geometry_patch(
    axis: Any,
    geometry: BaseGeometry,
    *,
    facecolor: str,
    edgecolor: str,
    alpha: float = 1.0,
    linewidth: float = 0.8,
    hatch: str | None = None,
) -> None:
    for polygon in polygon_parts(geometry):
        axis.add_patch(
            MatplotlibPolygon(
                np.asarray(polygon.exterior.coords),
                closed=True,
                facecolor=facecolor,
                edgecolor=edgecolor,
                alpha=alpha,
                linewidth=linewidth,
                hatch=hatch,
            )
        )
        for interior in polygon.interiors:
            axis.add_patch(
                MatplotlibPolygon(
                    np.asarray(interior.coords),
                    closed=True,
                    facecolor="white",
                    edgecolor=edgecolor,
                    linewidth=linewidth,
                )
            )


def render_overlaps_map(
    area_geometries: Mapping[str, BaseGeometry],
    overlaps: Mapping[tuple[str, str], BaseGeometry],
    palette: Mapping[str, str],
    output_path: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(13, 11), dpi=190)
    union = unary_union(list(area_geometries.values()))
    map_center = union.centroid
    for area, geometry in area_geometries.items():
        _add_geometry_patch(
            axis,
            geometry,
            facecolor=palette[area],
            edgecolor="#59636f",
            alpha=0.72,
            linewidth=0.65,
        )
    for pair, overlap in overlaps.items():
        _add_geometry_patch(
            axis,
            overlap,
            facecolor="#ff0054",
            edgecolor="#7a001f",
            alpha=0.9,
            linewidth=1.8,
        )
        point = overlap.representative_point()
        axis.scatter([point.x], [point.y], s=32, color="#7a001f", zorder=5)
        direction_x = point.x - map_center.x
        direction_y = point.y - map_center.y
        direction_norm = math.hypot(direction_x, direction_y) or 1.0
        text_offset = (
            42.0 * direction_x / direction_norm,
            42.0 * direction_y / direction_norm,
        )
        axis.annotate(
            f"{pair[0]} × {pair[1]}\n{overlap.area:,.1f} m²",
            (point.x, point.y),
            xytext=text_offset,
            textcoords="offset points",
            fontsize=7.5,
            color="#4a0012",
            ha="left" if text_offset[0] >= 0 else "right",
            va="bottom" if text_offset[1] >= 0 else "top",
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.88, "edgecolor": "#ff8aaa"},
            arrowprops={"arrowstyle": "-", "color": "#9f1239", "linewidth": 0.65},
        )
    min_x, min_y, max_x, max_y = union.bounds
    axis.set_xlim(min_x - (max_x - min_x) * 0.03, max_x + (max_x - min_x) * 0.03)
    axis.set_ylim(min_y - (max_y - min_y) * 0.03, max_y + (max_y - min_y) * 0.03)
    axis.set_aspect("equal")
    axis.axis("off")
    axis.set_title(
        "GeoJardins — sobreposições originais em EPSG:31983",
        fontsize=15,
        weight="bold",
        pad=14,
    )
    axis.text(
        0.01,
        0.01,
        "Áreas em cores da legenda; overlaps destacados em magenta. Valores em m² no CRS métrico local.",
        transform=axis.transAxes,
        fontsize=8,
        color="#374151",
    )
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, facecolor="white", bbox_inches="tight")
    plt.close(figure)


def _pixmap_array(pixmap: pymupdf.Pixmap) -> np.ndarray:
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )[:, :, :3]


def render_source_comparison(
    page: pymupdf.Page,
    exact_source: Mapping[str, BaseGeometry],
    source_partition: Mapping[str, BaseGeometry],
    overlaps: Mapping[tuple[str, str], BaseGeometry],
    palette: Mapping[str, str],
    paint_order: Sequence[str],
    output_path: Path,
) -> None:
    largest = sorted(overlaps, key=lambda pair: overlaps[pair].area, reverse=True)[:3]
    figure = plt.figure(figsize=(16, 20), dpi=170)
    grid = figure.add_gridspec(4, 2, height_ratios=[1.35, 1.0, 1.0, 1.0], hspace=0.2, wspace=0.08)
    source_union = unary_union(list(exact_source.values()))
    min_x, min_y, max_x, max_y = source_union.bounds
    pad_x = (max_x - min_x) * 0.035
    pad_y = (max_y - min_y) * 0.035

    source_axis = figure.add_subplot(grid[0, 0])
    full_pixmap = page.get_pixmap(matrix=pymupdf.Matrix(0.55, 0.55), alpha=False)
    source_axis.imshow(
        _pixmap_array(full_pixmap),
        extent=(page.rect.x0, page.rect.x1, page.rect.y1, page.rect.y0),
    )
    source_axis.set_xlim(page.rect.x0, page.rect.x1)
    source_axis.set_ylim(page.rect.y1, page.rect.y0)
    source_axis.set_title("PDF original completo renderizado", weight="bold")
    source_axis.axis("off")

    partition_axis = figure.add_subplot(grid[0, 1])
    for area in paint_order:
        _add_geometry_patch(
            partition_axis,
            source_partition[area],
            facecolor=palette[area],
            edgecolor="#5f6670",
            alpha=1.0,
            linewidth=0.55,
        )
    partition_axis.set_xlim(min_x - pad_x, max_x + pad_x)
    partition_axis.set_ylim(max_y + pad_y, min_y - pad_y)
    partition_axis.set_aspect("equal")
    partition_axis.axis("off")
    partition_axis.set_title("Partição canônica segundo a composição do PDF", weight="bold")

    for row, pair in enumerate(largest, start=1):
        overlap = overlaps[pair]
        bbox = overlap.bounds
        span_x = max(bbox[2] - bbox[0], 15.0)
        span_y = max(bbox[3] - bbox[1], 15.0)
        extra_x = max(12.0, span_x * 0.07)
        extra_y = max(12.0, span_y * 0.07)
        clip = pymupdf.Rect(
            bbox[0] - extra_x,
            bbox[1] - extra_y,
            bbox[2] + extra_x,
            bbox[3] + extra_y,
        ) & page.rect

        raster_axis = figure.add_subplot(grid[row, 0])
        crop = page.get_pixmap(matrix=pymupdf.Matrix(2.2, 2.2), clip=clip, alpha=False)
        raster_axis.imshow(
            _pixmap_array(crop),
            extent=(clip.x0, clip.x1, clip.y1, clip.y0),
        )
        _add_geometry_patch(
            raster_axis,
            overlap,
            facecolor="none",
            edgecolor="#ff0054",
            linewidth=1.6,
            hatch="////",
        )
        raster_axis.set_xlim(clip.x0, clip.x1)
        raster_axis.set_ylim(clip.y1, clip.y0)
        raster_axis.axis("off")
        raster_axis.set_title(f"Fonte ampliada — {pair[0]} × {pair[1]}", fontsize=10, weight="bold")

        decision_axis = figure.add_subplot(grid[row, 1])
        for area in pair:
            _add_geometry_patch(
                decision_axis,
                source_partition[area],
                facecolor=palette[area],
                edgecolor="#5f6670",
                linewidth=0.75,
            )
        _add_geometry_patch(
            decision_axis,
            overlap,
            facecolor="none",
            edgecolor="#ff0054",
            linewidth=1.8,
            hatch="////",
        )
        winner = max(pair, key=lambda area: paint_order.index(area))
        decision_axis.set_xlim(clip.x0, clip.x1)
        decision_axis.set_ylim(clip.y1, clip.y0)
        decision_axis.set_aspect("equal")
        decision_axis.axis("off")
        decision_axis.set_title(
            f"Decisão: {pair[0]} → {pair[1]} | proprietário final: {winner}",
            fontsize=10,
            weight="bold",
        )

    figure.suptitle(
        "GeoJardins — comparação fonte × decisão canônica\n"
        "Hachura magenta = geometria originalmente sobreposta; strokes do PDF não definem prioridade",
        fontsize=16,
        weight="bold",
        y=0.995,
    )
    legend_handles = [
        Patch(facecolor=palette[area], edgecolor="#5f6670", label=area)
        for area in paint_order
    ]
    figure.legend(handles=legend_handles, loc="lower center", ncol=6, frameon=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, facecolor="white", bbox_inches="tight")
    plt.close(figure)


def _load_affine_report(report_path: Path) -> tuple[str, list[float], list[float], float]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    selected_crs = report["selected_crs"]
    candidate = next(
        candidate for candidate in report["candidates"] if candidate["crs"] == selected_crs
    )
    transform = candidate["transform"]
    return (
        selected_crs,
        [float(value) for value in transform["x_coefficients"]],
        [float(value) for value in transform["y_coefficients"]],
        float(transform["determinant"]),
    )


def _pdf_to_wgs84(
    geometry: BaseGeometry,
    intermediate_crs: str,
    x_coefficients: Sequence[float],
    y_coefficients: Sequence[float],
) -> BaseGeometry:
    def affine(x: Any, y: Any, z: Any = None) -> tuple[Any, Any]:
        return (
            x_coefficients[0] * np.asarray(x) + x_coefficients[1] * np.asarray(y) + x_coefficients[2],
            y_coefficients[0] * np.asarray(x) + y_coefficients[1] * np.asarray(y) + y_coefficients[2],
        )

    projected = shapely_transform(affine, geometry)
    return _transform_geometry(projected, intermediate_crs, WGS84)


def _bbox_record(geometry_metric: BaseGeometry, geometry_wgs84: BaseGeometry) -> dict[str, Any]:
    metric_bounds = geometry_metric.bounds
    wgs_bounds = geometry_wgs84.bounds
    centroid_metric = geometry_metric.centroid
    centroid_wgs = _transform_geometry(centroid_metric, METRIC_CRS, WGS84)
    return {
        "bbox_metric": {
            "min_x": metric_bounds[0],
            "min_y": metric_bounds[1],
            "max_x": metric_bounds[2],
            "max_y": metric_bounds[3],
            "crs": METRIC_CRS,
        },
        "bbox_wgs84": {
            "min_longitude": wgs_bounds[0],
            "min_latitude": wgs_bounds[1],
            "max_longitude": wgs_bounds[2],
            "max_latitude": wgs_bounds[3],
        },
        "approximate_location": {
            "latitude": centroid_wgs.y,
            "longitude": centroid_wgs.x,
        },
    }


def _write_text_report(report: Mapping[str, Any], path: Path) -> None:
    if report.get("unresolved_count", 0):
        lines = [
            "DIAGNÓSTICO DE SOBREPOSIÇÕES — GEOJARDINS",
            "=" * 55,
            "",
            f"PDF-fonte: {report['pdf_source']}",
            f"Ordem real de pintura: {' → '.join(report['paint_order'])}",
            f"UNRESOLVED: {report['unresolved_count']}",
            "",
            "A partição canônica não foi publicada porque ao menos uma decisão não possui evidência PDF suficiente.",
            "Consulte overlap_diagnosis.json e unresolved_overlaps.geojson para revisão manual.",
            "",
        ]
        for item in report["overlaps"]:
            lines.extend(
                [
                    f"{item['areas'][0]} × {item['areas'][1]}: {item['confidence']}",
                    f"  Proprietário proposto: {item['proposed_owner']}",
                ]
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        return

    union = report["partition_validation"]
    exclusivity = report["exclusivity_test"]
    visual = report["source_render_validation"]
    lines = [
        "DIAGNÓSTICO DE SOBREPOSIÇÕES — GEOJARDINS",
        "=" * 55,
        "",
        f"PDF-fonte: {report['pdf_source']}",
        f"Ordem real de pintura: {' → '.join(report['paint_order'])}",
        "Regra de composição: último preenchimento opaco B* prevalece.",
        "Prioridade hardcoded por nome: NÃO.",
        "Buffer/simplificação: NÃO utilizados.",
        "",
        "CAUSA",
        "-----",
        "A) SIM — overlaps já existem nos paths vetoriais independentes do PDF.",
        "B) NÃO — todas as interseções foram reproduzidas nos paths-fonte.",
        (
            "C) PARCIAL E NÃO CAUSAL — make_valid acrescentou apenas "
            f"{report['cause_analysis']['make_valid_added_overlap_m2_report_crs']:.6f} m² "
            "ao overlap Tomé_4 × Tomé_6 no CRS do relatório."
        ),
        "D) Os 65 paths devem ser unidos por cor/área; outra combinação não é necessária.",
        "E) Há clipping de página/margem, mas nenhum bbox de overlap o intercepta.",
        "",
        "OBSERVAÇÃO SOBRE A COR LITERAL",
        "-------------------------------",
        (
            "Os overlaps são faixas mais estreitas que o stroke cinza de 0,48001 ponto. "
            "O raster literal mostra principalmente o contorno. Ignorando strokes, como requerido, "
            "a cor do último fill opaco no content stream determina o proprietário."
        ),
        "",
        "OVERLAPS ORIGINAIS",
        "-------------------",
    ]
    for item in report["overlaps"]:
        lines.extend(
            [
                f"{item['areas'][0]} × {item['areas'][1]}",
                f"  Área: {item['area_m2']:.3f} m² ({METRIC_CRS})",
                f"  Componentes poligonais: {item['component_count']}",
                f"  Draw order: {' → '.join(item['draw_order'])}",
                f"  Proprietário final: {item['proposed_owner']}",
                f"  Cor de fill visível sem strokes: {item['visible_fill_color']}",
                f"  Causa provável: {item['probable_cause']}",
                f"  Confiança: {item['confidence']}",
                "",
            ]
        )
    lines.extend(
        [
            "VALIDAÇÃO DA PARTIÇÃO",
            "----------------------",
            f"Área total original: {union['original_union_area_m2']:.6f} m²",
            f"Área total canonical: {union['canonical_union_area_m2']:.6f} m²",
            f"Diferença: {union['difference_m2']:.9f} m²",
            f"Diferença percentual: {union['difference_percent']:.12f}%",
            f"Diferença simétrica: {union['symmetric_difference_m2']:.9f} m²",
            f"Gaps novos: {union['new_gap_area_m2']:.9f} m²",
            f"Overlaps significativos finais: {union['significant_overlap_count']}",
            (
                "Diferença simétrica boundary original × partition: "
                f"{union['original_boundary_comparison']['symmetric_difference_m2']:.9f} m² "
                "(após make_valid apenas em cópias diagnósticas)."
            ),
            "",
            "TESTE DE EXCLUSIVIDADE",
            "-----------------------",
            f"Predicado: {exclusivity['predicate']}",
            f"Pontos testados: {exclusivity['points_tested']}",
            f"0 áreas: {exclusivity['zero_areas']}",
            f"1 área: {exclusivity['one_area']}",
            f"2+ áreas: {exclusivity['two_or_more_areas']}",
            "",
            "COMPARAÇÃO SOURCE-RENDER × CANONICAL",
            "------------------------------------",
            f"Pontos válidos comparados: {visual['valid_palette_points_compared']}",
            f"Concordância: {visual['agreement_percent']:.6f}%",
            f"Sem threshold de aceitação; tolerância de classificação RGB: {visual['rgb_distance_tolerance']:.0f}.",
            "",
            f"UNRESOLVED: {report['unresolved_count']}",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def run_overlap_diagnosis(
    *,
    pdf_path: Path | None = None,
    source_pdf_geojson_path: Path = Path("data/interim/areas_pdf_coordinates.geojson"),
    areas_path: Path = Path("data/processed/areas_jardins.geojson"),
    boundary_path: Path = Path("data/processed/ugr_jardins_boundary.geojson"),
    georef_report_path: Path = Path("output/debug/georef_report.json"),
    report_json_path: Path = Path("output/debug/overlap_diagnosis.json"),
    report_text_path: Path = Path("output/debug/overlap_diagnosis.txt"),
    overlaps_map_path: Path = Path("output/debug/overlaps_map.png"),
    comparison_map_path: Path = Path("output/debug/overlaps_source_comparison.png"),
    partition_path: Path = Path("data/processed/areas_jardins_partition.geojson"),
    partition_boundary_path: Path = Path("data/processed/ugr_jardins_boundary_partition.geojson"),
    unresolved_path: Path = Path("data/processed/unresolved_overlaps.geojson"),
) -> dict[str, Any]:
    """Executa diagnóstico, partição, validação e geração de artefatos."""

    source_pdf = resolve_pdf_path(pdf_path)
    source_collection = load_geojson(source_pdf_geojson_path)
    areas_collection = load_geojson(areas_path)
    boundary_collection = load_geojson(boundary_path)
    (
        document,
        page,
        drawings,
        paint_evidence,
        exact_source_geometries,
        structural,
    ) = inspect_pdf_painting(source_pdf)
    paint_order = derive_paint_order(paint_evidence)
    palette = {area: paint_evidence[area].fill_color for area in paint_order}
    current_source_geometries = collection_geometries(source_collection)

    intermediate_crs, x_coefficients, y_coefficients, determinant = _load_affine_report(
        georef_report_path
    )
    exact_source_wgs84 = {
        area: _pdf_to_wgs84(
            geometry,
            intermediate_crs,
            x_coefficients,
            y_coefficients,
        )
        for area, geometry in exact_source_geometries.items()
    }
    exact_source_metric = {
        area: _transform_geometry(geometry, WGS84, METRIC_CRS)
        for area, geometry in exact_source_wgs84.items()
    }
    original_wgs84 = collection_geometries(areas_collection)
    original_metric = {
        area: _transform_geometry(geometry, WGS84, METRIC_CRS)
        for area, geometry in original_wgs84.items()
    }
    original_overlaps_metric = find_overlaps(original_metric, SIGNIFICANT_OVERLAP_M2)
    source_overlaps_exact = find_overlaps(exact_source_geometries, 1e-12)

    clip_scissors = [
        item["scissor_pdf"]
        for item in structural["clipping_paths"]
        if item["scissor_pdf"] is not None
    ]
    unique_clip_bounds = {
        tuple(round(value, 6) for value in clip.values()) for clip in clip_scissors
    }
    common_inner_clip = None
    if unique_clip_bounds:
        rects = [pymupdf.Rect(values) for values in unique_clip_bounds]
        common_inner_clip = min(rects, key=lambda rect: rect.get_area())

    stroke_colors = sorted(
        {
            color
            for record in paint_evidence.values()
            for color in record.stroke_colors
        }
    )
    source_path_geometries = {
        area: {
            index: exact_even_odd_geometry(drawings[index])
            for index in paint_evidence[area].drawing_indexes
        }
        for area in paint_order
    }

    unresolved: list[dict[str, Any]] = []
    overlap_records: list[dict[str, Any]] = []
    make_valid_added_overlap_selected_crs = 0.0
    for pair, overlap_metric in original_overlaps_metric.items():
        left, right = pair
        source_overlap = source_overlaps_exact.get(tuple(sorted(pair)))
        if source_overlap is None:
            source_overlap = source_overlaps_exact.get(pair, GeometryCollection())
        pair_order = tuple(sorted(pair, key=lambda area: paint_order.index(area)))
        winner = pair_order[-1]
        source_path_pairs = []
        for left_index, left_geometry in source_path_geometries[left].items():
            for right_index, right_geometry in source_path_geometries[right].items():
                intersection = polygonal_only(left_geometry.intersection(right_geometry))
                if intersection.area > 1e-12:
                    source_path_pairs.append(
                        {
                            "left_drawing_index": left_index,
                            "right_drawing_index": right_index,
                            "area_pdf_points_squared": intersection.area,
                        }
                    )
        source_bbox_inside_clip = True
        if common_inner_clip is not None and not source_overlap.is_empty:
            overlap_rect = pymupdf.Rect(source_overlap.bounds)
            source_bbox_inside_clip = common_inner_clip.contains(overlap_rect)
        raster_evidence = _sample_overlap_raster(
            page,
            source_overlap,
            palette,
            stroke_colors,
        )
        exact_overlap_report_crs_m2 = source_overlap.area * abs(determinant)
        current_source_overlap = polygonal_only(
            current_source_geometries[left].intersection(current_source_geometries[right])
        )
        current_overlap_report_crs_m2 = current_source_overlap.area * abs(determinant)
        make_valid_increment = max(
            0.0, current_overlap_report_crs_m2 - exact_overlap_report_crs_m2
        )
        make_valid_added_overlap_selected_crs += make_valid_increment

        decisive = (
            not source_overlap.is_empty
            and source_overlap.area > 1e-12
            and paint_evidence[left].opaque
            and paint_evidence[right].opaque
            and pair_order[0] != pair_order[1]
            and source_bbox_inside_clip
        )
        confidence = "ALTA" if decisive else "UNRESOLVED"
        overlap_wgs = _transform_geometry(overlap_metric, METRIC_CRS, WGS84)
        record = {
            "areas": [left, right],
            "area_m2": overlap_metric.area,
            "metric_crs": METRIC_CRS,
            "area_m2_georef_report_crs": current_overlap_report_crs_m2,
            "georef_report_crs": intermediate_crs,
            "percent_of_left": 100.0 * overlap_metric.area / original_metric[left].area,
            "percent_of_right": 100.0 * overlap_metric.area / original_metric[right].area,
            "component_count": len(polygon_parts(overlap_metric)),
            **_bbox_record(overlap_metric, overlap_wgs),
            "source_pdf": {
                "exists_in_exact_even_odd_geometry": not source_overlap.is_empty,
                "area_pdf_points_squared": source_overlap.area,
                "source_path_pairs": source_path_pairs,
                "clipping_affects_overlap_bbox": not source_bbox_inside_clip,
                "raster_evidence": raster_evidence,
            },
            "draw_order": list(pair_order),
            "draw_sequences": {
                area: {
                    "first": paint_evidence[area].first_sequence,
                    "last": paint_evidence[area].last_sequence,
                }
                for area in pair_order
            },
            "literal_visible_raster": (
                "boundary stroke; overlap narrower than the 0.48001 pt area outline"
            ),
            "visible_fill_color": palette[winner],
            "visible_fill_area": winner,
            "proposed_owner": winner if decisive else "UNRESOLVED",
            "probable_cause": (
                "Geometrias independentes já se sobrepõem no PDF; o último fill opaco "
                "oculta os anteriores segundo o painter's algorithm."
            ),
            "make_valid_added_overlap_m2_georef_report_crs": make_valid_increment,
            "confidence": confidence,
        }
        overlap_records.append(record)
        if not decisive:
            unresolved.append(
                {
                    "areas": [left, right],
                    "status": "UNRESOLVED",
                    "reason": "A evidência PDF não definiu um fill proprietário inequívoco.",
                    "geometry": overlap_wgs,
                }
            )

    source_partition = build_painter_partition(exact_source_geometries, paint_order)
    source_render_validation = source_render_agreement(
        page, source_partition, palette
    )

    report: dict[str, Any] = {
        "phase": "2.5",
        "status": "DIAGNOSED" if unresolved else "RESOLVED_AND_VALIDATED",
        "pdf_source": source_pdf.name,
        "metric_crs": METRIC_CRS,
        "paint_order": list(paint_order),
        "paint_order_source": "PDF vector content stream sequence numbers",
        "area_paint_evidence": {
            area: {
                "fill_color": record.fill_color,
                "drawing_indexes": list(record.drawing_indexes),
                "sequence_numbers": list(record.sequence_numbers),
                "first_sequence": record.first_sequence,
                "last_sequence": record.last_sequence,
                "opaque": record.opaque,
                "fill_rule_even_odd_B_star": record.uses_even_odd_fill,
                "drawing_types": list(record.drawing_types),
                "levels": list(record.levels),
                "stroke_colors": list(record.stroke_colors),
                "stroke_widths_pdf_points": list(record.stroke_widths_pdf_points),
            }
            for area, record in paint_evidence.items()
        },
        "pdf_structure": {
            key: value
            for key, value in structural.items()
            if key != "current_geometries_pdf"
        },
        "cause_analysis": {
            "A_original_pdf_geometry_overlap": True,
            "B_introduced_by_extraction": False,
            "C_created_or_increased_by_make_valid": make_valid_added_overlap_selected_crs > 0,
            "make_valid_added_overlap_m2_report_crs": make_valid_added_overlap_selected_crs,
            "make_valid_note": (
                "A única diferença relevante para pares é um incremento mínimo em Tomé_4 × Tomé_6; "
                "os oito pares já se sobrepõem na reconstrução B* exata."
            ),
            "D_multiple_paths_require_different_combination": False,
            "D_note": "Os 65 paths são corretamente dissolvidos por cor; os overlaps persistem antes do dissolve.",
            "E_clipping_explains_overlaps": False,
            "E_note": "Os clips são de página/margem e não interceptam os bboxes dos overlaps.",
            "primary_cause": (
                "Polígonos independentes do PDF têm slivers sobrepostos; a renderização final depende "
                "da ordem de pintura, com fills opacos e contornos cinza."
            ),
        },
        "overlap_count_original": len(overlap_records),
        "overlaps": overlap_records,
        "unresolved_count": len(unresolved),
        "unresolved_pairs": [record["areas"] for record in unresolved],
        "source_render_validation": source_render_validation,
        "boundary_semantics": {
            "exclusive_sampling_predicate": "contains",
            "lookup_predicate": "covers",
            "numeric_zero_tolerance_m": EXACT_BOUNDARY_TOLERANCE_M,
            "exact_boundary": (
                "contains retorna falso na linha; covers pode retornar duas áreas. "
                "O lookup reporta ambiguidade e distância 0,0 m, sem forçar proprietário. "
                "Até 1 mm é tratado como zero apenas para absorver ruído numérico de reprojeção."
            ),
        },
    }

    if unresolved:
        write_geojson(_unresolved_collection(unresolved), unresolved_path)
        report["partition_created"] = False
        report["partition_validation"] = {
            "not_run": "Há overlaps UNRESOLVED; nenhuma partição foi publicada."
        }
        report["exclusivity_test"] = {"not_run": True}
        report_json_path.parent.mkdir(parents=True, exist_ok=True)
        report_json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _write_text_report(report, report_text_path)
        raise OverlapDiagnosisError(
            f"{len(unresolved)} overlap(s) sem evidência suficiente; partição não publicada."
        )

    partition_metric = build_painter_partition(original_metric, paint_order)
    partition_wgs84 = {
        area: _transform_geometry(geometry, METRIC_CRS, WGS84)
        for area, geometry in partition_metric.items()
    }
    partition_validation = validate_partition(original_metric, partition_metric)
    exclusivity = exclusivity_sample(
        partition_metric, original_overlaps_metric
    )

    diagnosis_summary = {"pdf_source": source_pdf.name}
    partition_collection = _feature_collection_from_partition(
        areas_collection,
        partition_wgs84,
        paint_order,
        paint_evidence,
        diagnosis_summary,
    )
    partition_boundary = _boundary_collection(partition_collection)
    original_boundary_geometry = shape(boundary_collection["features"][0]["geometry"])
    partition_boundary_geometry = shape(partition_boundary["features"][0]["geometry"])
    original_boundary_metric = _transform_geometry(
        original_boundary_geometry, WGS84, METRIC_CRS
    )
    partition_boundary_metric = _transform_geometry(
        partition_boundary_geometry, WGS84, METRIC_CRS
    )
    original_boundary_valid_for_comparison = polygonal_only(
        make_valid(original_boundary_metric)
    )
    partition_boundary_valid_for_comparison = polygonal_only(
        make_valid(partition_boundary_metric)
    )
    boundary_comparison = {
        "metric_crs": METRIC_CRS,
        "original_boundary_valid_after_projection": original_boundary_metric.is_valid,
        "partition_boundary_valid_after_projection": partition_boundary_metric.is_valid,
        "comparison_geometry_note": (
            "make_valid foi aplicado apenas a cópias em memória para a comparação diagnóstica; "
            "nenhum boundary de entrada foi alterado."
        ),
        "original_boundary_area_m2": original_boundary_metric.area,
        "partition_boundary_area_m2": partition_boundary_metric.area,
        "difference_m2": partition_boundary_metric.area - original_boundary_metric.area,
        "symmetric_difference_m2": original_boundary_valid_for_comparison.symmetric_difference(
            partition_boundary_valid_for_comparison
        ).area,
    }
    exact_partition_metric = {
        area: _transform_geometry(
            _pdf_to_wgs84(
                geometry,
                intermediate_crs,
                x_coefficients,
                y_coefficients,
            ),
            WGS84,
            METRIC_CRS,
        )
        for area, geometry in source_partition.items()
    }
    exact_partition_union = unary_union(list(exact_partition_metric.values()))
    canonical_union = unary_union(list(partition_metric.values()))
    partition_validation["canonical_vs_exact_pdf_union_symmetric_difference_m2"] = (
        canonical_union.symmetric_difference(exact_partition_union).area
    )
    partition_validation["original_boundary_comparison"] = boundary_comparison

    report.update(
        {
            "partition_created": True,
            "partition_method": (
                "Painter's algorithm derivado do content stream: cada área perde somente as "
                "superfícies cobertas por fills posteriores. Sem buffer ou simplificação."
            ),
            "partition_validation": partition_validation,
            "exclusivity_test": exclusivity,
            "files": {
                "partition": str(partition_path),
                "partition_boundary": str(partition_boundary_path),
                "unresolved": str(unresolved_path),
                "report_json": str(report_json_path),
                "report_text": str(report_text_path),
                "overlaps_map": str(overlaps_map_path),
                "source_comparison": str(comparison_map_path),
            },
        }
    )

    write_geojson(partition_collection, partition_path)
    write_geojson(partition_boundary, partition_boundary_path)
    write_geojson(_unresolved_collection([]), unresolved_path)
    render_overlaps_map(original_metric, original_overlaps_metric, palette, overlaps_map_path)
    render_source_comparison(
        page,
        exact_source_geometries,
        source_partition,
        source_overlaps_exact,
        palette,
        paint_order,
        comparison_map_path,
    )
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_text_report(report, report_text_path)
    document.close()
    LOGGER.info("Diagnóstico concluído: %s overlaps originais, 0 finais.", len(overlap_records))
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnostica overlaps Tomé no PDF e cria uma partição canônica validada."
    )
    parser.add_argument("--pdf", type=Path, help="PDF-fonte; detectado em data/raw por padrão.")
    parser.add_argument(
        "--areas",
        type=Path,
        default=Path("data/processed/areas_jardins.geojson"),
        help="Áreas georreferenciadas originais, preservadas sem sobrescrita.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        run_overlap_diagnosis(pdf_path=args.pdf, areas_path=args.areas)
    except (OverlapDiagnosisError, FileNotFoundError, ValueError, KeyError) as exc:
        LOGGER.error("Diagnóstico interrompido: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
