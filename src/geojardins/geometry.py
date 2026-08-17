"""Conversão de caminhos vetoriais PDF para geometrias Shapely."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

Point = tuple[float, float]


class GeometryExtractionError(ValueError):
    """Um caminho PDF não pôde ser convertido sem uma aproximação insegura."""


def rgb_to_hex(rgb: Sequence[float]) -> str:
    """Converte uma cor RGB normalizada do PDF para hexadecimal."""

    if len(rgb) != 3:
        raise ValueError("Uma cor RGB precisa ter três componentes.")
    channels = [max(0, min(255, round(float(component) * 255))) for component in rgb]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _as_point(value: Any) -> Point:
    if hasattr(value, "x") and hasattr(value, "y"):
        return (float(value.x), float(value.y))
    return (float(value[0]), float(value[1]))


def _same_point(left: Point, right: Point, tolerance: float = 1e-6) -> bool:
    return abs(left[0] - right[0]) <= tolerance and abs(left[1] - right[1]) <= tolerance


def _rectangle_ring(rect: Any) -> list[Point]:
    x0 = float(rect.x0)
    y0 = float(rect.y0)
    x1 = float(rect.x1)
    y1 = float(rect.y1)
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def _quad_ring(quad: Any) -> list[Point]:
    return [
        _as_point(quad.ul),
        _as_point(quad.ur),
        _as_point(quad.lr),
        _as_point(quad.ll),
        _as_point(quad.ul),
    ]


def rings_from_drawing(drawing: Mapping[str, Any]) -> list[list[Point]]:
    """Recupera subpaths fechados de um desenho retornado por PyMuPDF.

    Preenchimentos PDF fecham implicitamente subpaths abertos. A conversão registra
    essa mesma regra sem simplificar os vértices lineares originais.
    """

    rings: list[list[Point]] = []
    current: list[Point] = []

    def flush_current() -> None:
        nonlocal current
        if len(current) >= 3:
            if not _same_point(current[0], current[-1]):
                current.append(current[0])
            rings.append(current)
        current = []

    for item in drawing.get("items", []):
        operation = item[0]
        if operation == "l":
            start = _as_point(item[1])
            end = _as_point(item[2])
            if not current:
                current = [start, end]
            elif _same_point(current[-1], start):
                current.append(end)
            else:
                flush_current()
                current = [start, end]
        elif operation == "re":
            flush_current()
            rings.append(_rectangle_ring(item[1]))
        elif operation == "qu":
            flush_current()
            rings.append(_quad_ring(item[1]))
        elif operation == "c":
            raise GeometryExtractionError(
                "O preenchimento contém curvas Bézier; a extração foi interrompida "
                "para não aproximar a geometria silenciosamente."
            )
        else:
            raise GeometryExtractionError(f"Operação de caminho PDF não suportada: {operation!r}")

    flush_current()
    return rings


def geometry_from_rings(rings: Iterable[Sequence[Point]]) -> BaseGeometry:
    """Constrói Polygon/MultiPolygon, preservando ilhas e furos por aninhamento.

    Alguns caminhos vetoriais exportados pelo mapa têm auto-interseções. Nesses
    casos, ``make_valid`` divide a linha exatamente nas interseções para obter
    superfícies OGC válidas; não há simplificação nem criação de coordenadas por
    estimativa.
    """

    candidates: list[Polygon] = []
    for ring in rings:
        if len(ring) < 4:
            continue
        polygon = Polygon(ring)
        if polygon.is_empty:
            continue
        if not polygon.is_valid:
            repaired = make_valid(polygon)
            if repaired.geom_type == "Polygon":
                candidates.append(repaired)
            elif repaired.geom_type == "MultiPolygon":
                candidates.extend(repaired.geoms)
            elif repaired.geom_type == "GeometryCollection":
                candidates.extend(
                    component
                    for component in repaired.geoms
                    if component.geom_type == "Polygon" and not component.is_empty and component.area > 0
                )
            else:
                raise GeometryExtractionError(
                    f"A correção topológica do subpath produziu {repaired.geom_type}, não uma superfície."
                )
            continue
        if polygon.area == 0:
            continue
        candidates.append(polygon)

    if not candidates:
        raise GeometryExtractionError("O caminho preenchido não contém um anel poligonal válido.")

    parents: list[int | None] = []
    for index, candidate in enumerate(candidates):
        possible_parents = [
            parent_index
            for parent_index, parent in enumerate(candidates)
            if parent_index != index
            and parent.area > candidate.area
            and parent.contains(candidate.representative_point())
        ]
        parents.append(min(possible_parents, key=lambda parent_index: candidates[parent_index].area, default=None))

    depths: list[int] = []
    for index in range(len(candidates)):
        depth = 0
        parent = parents[index]
        while parent is not None:
            depth += 1
            parent = parents[parent]
        depths.append(depth)

    polygons: list[Polygon] = []
    for index, shell in enumerate(candidates):
        if depths[index] % 2 != 0:
            continue
        holes = [
            list(candidates[child].exterior.coords)
            for child, parent in enumerate(parents)
            if parent == index and depths[child] == depths[index] + 1
        ]
        polygons.append(Polygon(shell.exterior.coords, holes))

    if len(polygons) == 1:
        return polygons[0]
    return MultiPolygon(polygons)


def geometry_from_drawing(drawing: Mapping[str, Any]) -> BaseGeometry:
    """Converte um único desenho preenchido do PyMuPDF em uma geometria."""

    return geometry_from_rings(rings_from_drawing(drawing))


def dissolve_geometries(geometries: Iterable[BaseGeometry]) -> BaseGeometry:
    """Une caminhos da mesma área preservando a topologia, sem simplificação."""

    valid_geometries = [geometry for geometry in geometries if not geometry.is_empty]
    if not valid_geometries:
        raise GeometryExtractionError("Não há geometrias para dissolver.")
    dissolved = unary_union(valid_geometries)
    if dissolved.geom_type not in {"Polygon", "MultiPolygon"}:
        raise GeometryExtractionError(
            f"A união produziu geometria não poligonal: {dissolved.geom_type}."
        )
    return dissolved
