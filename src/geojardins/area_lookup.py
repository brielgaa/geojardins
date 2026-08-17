"""Consulta latitude/longitude nas áreas georreferenciadas da UGR Jardins."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as shapely_transform

from .runtime import resource_path


METRIC_CRS = "EPSG:31983"
PARTITION_AREAS_PATH = resource_path("data", "processed", "areas_jardins_partition.geojson")
ORIGINAL_AREAS_PATH = resource_path("data", "processed", "areas_jardins.geojson")
PARTITION_BOUNDARY_PATH = resource_path("data", "processed", "ugr_jardins_boundary_partition.geojson")
ORIGINAL_BOUNDARY_PATH = resource_path("data", "processed", "ugr_jardins_boundary.geojson")
# Tolerância exclusivamente numérica para reprojeção/serialização. Não é a zona
# de segurança do produto, que permanece deliberadamente sem threshold.
EXACT_BOUNDARY_TOLERANCE_M = 1e-3


class AreaLookupError(ValueError):
    """A consulta não pode ser executada com segurança."""


@dataclass(frozen=True)
class LookupResult:
    inside_ugr: bool
    areas: tuple[str, ...]
    distance_to_boundary_m: float | None = None
    geometric_ambiguity: bool = False

    @property
    def area(self) -> str | None:
        return self.areas[0] if len(self.areas) == 1 else None


def load_geojson(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AreaLookupError(f"Arquivo geográfico não encontrado: {path}")
    try:
        collection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AreaLookupError(f"Não foi possível ler {path}: {exc}") from exc
    if collection.get("type") != "FeatureCollection":
        raise AreaLookupError(f"{path} não é uma FeatureCollection.")
    return collection


def preferred_areas_path() -> Path:
    """Prefere a partição validada e conserva fallback explícito para a base original."""

    return PARTITION_AREAS_PATH if PARTITION_AREAS_PATH.is_file() else ORIGINAL_AREAS_PATH


def preferred_boundary_path() -> Path:
    return (
        PARTITION_BOUNDARY_PATH
        if PARTITION_BOUNDARY_PATH.is_file()
        else ORIGINAL_BOUNDARY_PATH
    )


def _validate_coordinate(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise AreaLookupError("Latitude ou longitude fora dos limites válidos.")


def distance_to_nearest_boundary(
    latitude: float,
    longitude: float,
    areas_collection: dict[str, Any] | None = None,
    *,
    areas_path: Path | None = None,
    metric_crs: str = METRIC_CRS,
) -> float:
    """Retorna a menor distância, em metros, a qualquer limite de área.

    As geometrias EPSG:4326 e o ponto são projetados para SIRGAS 2000 / UTM
    zona 23S (EPSG:31983) por padrão. Nenhum threshold de produto é aplicado.
    """

    _validate_coordinate(latitude, longitude)
    if areas_collection is not None and areas_path is not None:
        raise AreaLookupError("Informe areas_collection ou areas_path, não ambos.")
    if areas_collection is None:
        areas_collection = load_geojson(areas_path or preferred_areas_path())
    features = areas_collection.get("features", [])
    if not features:
        raise AreaLookupError("A coleção de áreas está vazia.")
    source_crs = areas_collection.get("metadata", {}).get("coordinate_system", "EPSG:4326")
    transformer = Transformer.from_crs(source_crs, metric_crs, always_xy=True)
    point_x, point_y = transformer.transform(longitude, latitude)
    projected_point = Point(point_x, point_y)
    distances = []
    source_point = Point(longitude, latitude)
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.boundary.covers(source_point):
            return 0.0
        projected = shapely_transform(transformer.transform, geometry)
        distances.append(projected_point.distance(projected.boundary))
    minimum = min(distances)
    return 0.0 if minimum <= EXACT_BOUNDARY_TOLERANCE_M else minimum


def lookup_coordinate(
    latitude: float,
    longitude: float,
    areas_collection: dict[str, Any],
    boundary_collection: dict[str, Any],
) -> LookupResult:
    """Classifica o interior e expõe explicitamente a semântica de fronteira.

    ``covers`` mantém pontos exatamente na linha no domínio da consulta. Se duas
    áreas cobrem o ponto, a função devolve ambas e marca ambiguidade; não escolhe
    silenciosamente um lado.
    """

    _validate_coordinate(latitude, longitude)
    boundary_features = boundary_collection.get("features", [])
    if len(boundary_features) != 1:
        raise AreaLookupError("O boundary deve conter exatamente uma feature.")
    point = Point(longitude, latitude)
    inside = shape(boundary_features[0]["geometry"]).covers(point)
    if not inside:
        return LookupResult(inside_ugr=False, areas=())
    matched_areas = tuple(
        feature["properties"]["area"]
        for feature in areas_collection.get("features", [])
        if shape(feature["geometry"]).covers(point)
    )
    distance = distance_to_nearest_boundary(
        latitude,
        longitude,
        areas_collection,
    )
    ambiguity = len(matched_areas) != 1 or distance <= EXACT_BOUNDARY_TOLERANCE_M
    return LookupResult(
        inside_ugr=True,
        areas=matched_areas,
        distance_to_boundary_m=distance,
        geometric_ambiguity=ambiguity,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consulta uma coordenada nas áreas da UGR Jardins.")
    parser.add_argument("latitude", type=float)
    parser.add_argument("longitude", type=float)
    parser.add_argument(
        "--areas",
        type=Path,
        help=(
            "GeoJSON de áreas; por padrão usa areas_jardins_partition.geojson quando existe, "
            "com fallback para areas_jardins.geojson."
        ),
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        help=(
            "Boundary; por padrão usa ugr_jardins_boundary_partition.geojson quando existe, "
            "com fallback para o original."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        areas = load_geojson(args.areas or preferred_areas_path())
        boundary = load_geojson(args.boundary or preferred_boundary_path())
        result = lookup_coordinate(args.latitude, args.longitude, areas, boundary)
    except AreaLookupError as exc:
        print(f"Consulta não executada: {exc}")
        return 2

    print(f"Dentro da UGR Jardins: {'SIM' if result.inside_ugr else 'NÃO'}")
    if result.area:
        print(f"Área: {result.area}")
    elif len(result.areas) > 1:
        print("Área: AMBÍGUA - " + ", ".join(result.areas))
        print("Aviso: coordenada sobre sobreposição/divisa; requer conferência.")
    elif result.inside_ugr:
        print("Área: -")
        print("Aviso: ponto dentro do boundary, mas em um hole/gap entre áreas.")
    else:
        print("Área: -")
    if result.distance_to_boundary_m is not None:
        print(f"Distância da divisa: {result.distance_to_boundary_m:.1f} m")
    print(f"Ambiguidade geométrica: {'SIM' if result.geometric_ambiguity else 'NÃO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
