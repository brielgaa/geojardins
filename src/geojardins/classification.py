"""Camada GIS independente de geocoding providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .area_lookup import (
    ORIGINAL_AREAS_PATH,
    ORIGINAL_BOUNDARY_PATH,
    PARTITION_AREAS_PATH,
    PARTITION_BOUNDARY_PATH,
    load_geojson,
    lookup_coordinate,
)


@dataclass(frozen=True)
class GeographicClassification:
    inside_ugr: bool
    area: str | None
    areas: tuple[str, ...]
    distance_to_boundary_m: float | None
    geometric_ambiguity: bool


class GeographicClassifier:
    """Carrega a partição canônica uma vez e reutiliza point-in-polygon existente."""

    def __init__(self, areas_collection: dict, boundary_collection: dict) -> None:
        self.areas_collection = areas_collection
        self.boundary_collection = boundary_collection

    @classmethod
    def from_paths(
        cls,
        areas_path: Path | None = None,
        boundary_path: Path | None = None,
    ) -> "GeographicClassifier":
        resolved_areas = areas_path or (
            PARTITION_AREAS_PATH if PARTITION_AREAS_PATH.is_file() else ORIGINAL_AREAS_PATH
        )
        resolved_boundary = boundary_path or (
            PARTITION_BOUNDARY_PATH if PARTITION_BOUNDARY_PATH.is_file() else ORIGINAL_BOUNDARY_PATH
        )
        return cls(load_geojson(resolved_areas), load_geojson(resolved_boundary))

    def classify(self, latitude: float, longitude: float) -> GeographicClassification:
        result = lookup_coordinate(
            latitude,
            longitude,
            self.areas_collection,
            self.boundary_collection,
        )
        return GeographicClassification(
            inside_ugr=result.inside_ugr,
            area=result.area,
            areas=result.areas,
            distance_to_boundary_m=result.distance_to_boundary_m,
            geometric_ambiguity=result.geometric_ambiguity,
        )


def locationiq_viewbox_from_boundary(
    boundary_collection: dict,
    margin_deg: float,
) -> str:
    """Monta a viewbox da API a partir da base canônica, sem coordenadas fixas."""

    if margin_deg < 0:
        raise ValueError("A margem da viewbox não pode ser negativa.")
    features = boundary_collection.get("features", [])
    if len(features) != 1:
        raise ValueError("O boundary da UGR deve conter exatamente uma feature.")
    from shapely.geometry import shape

    min_lon, min_lat, max_lon, max_lat = shape(features[0]["geometry"]).bounds
    return ",".join(
        f"{value:.7f}"
        for value in (
            min_lon - margin_deg,
            min_lat - margin_deg,
            max_lon + margin_deg,
            max_lat + margin_deg,
        )
    )


def geoapify_rect_from_boundary(boundary_collection: dict, margin_m: float) -> str:
    """Retorna ``min_lon,min_lat,max_lon,max_lat`` com margem métrica real.

    A margem é aplicada em SIRGAS 2000 / UTM 23S antes de voltar a EPSG:4326;
    isto evita tratar metros como graus e não modifica a geometria canônica.
    """

    if margin_m < 0:
        raise ValueError("A margem Geoapify não pode ser negativa.")
    features = boundary_collection.get("features", [])
    if len(features) != 1:
        raise ValueError("O boundary da UGR deve conter exatamente uma feature.")
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform

    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True).transform
    to_wgs84 = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True).transform
    buffered = transform(to_metric, shape(features[0]["geometry"])).buffer(margin_m)
    min_lon, min_lat, max_lon, max_lat = transform(to_wgs84, buffered).bounds
    return ",".join(f"{value:.7f}" for value in (min_lon, min_lat, max_lon, max_lat))
