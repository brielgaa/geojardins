"""Georreferenciamento métrico, avaliação independente e validação das áreas."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

os.environ.setdefault("MPLCONFIGDIR", str((Path.cwd() / ".cache" / "matplotlib").resolve()))

import matplotlib.pyplot as plt
import numpy as np
from pyproj import CRS, Geod, Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.ops import unary_union

from .control_points import (
    ControlPoint,
    ControlPointError,
    assess_spatial_distribution,
    load_control_points,
)

LOGGER = logging.getLogger(__name__)
CANDIDATE_CRS = ("EPSG:31983", "EPSG:3857")
MINIMUM_TOTAL_GCPS = 6
MINIMUM_FIT_GCPS = 4
MINIMUM_VALIDATION_GCPS = 2
DEFAULT_MAX_VALIDATION_RMSE_M = 10.0
DEFAULT_MAX_VALIDATION_ERROR_M = 25.0
GEOD = Geod(ellps="GRS80")


class GeoReferenceError(ValueError):
    """Os pontos de controle ou a transformação não são confiáveis."""


@dataclass(frozen=True)
class ProjectedControlPoint:
    point: ControlPoint
    x: float
    y: float


@dataclass(frozen=True)
class AffineTransform:
    """Transformação PDF(x,y) para um CRS projetado intermediário."""

    intermediate_crs: str
    x_coefficients: np.ndarray
    y_coefficients: np.ndarray
    rank: int
    determinant: float

    def apply(self, pdf_x: Any, pdf_y: Any) -> tuple[Any, Any]:
        x_array = np.asarray(pdf_x, dtype=float)
        y_array = np.asarray(pdf_y, dtype=float)
        projected_x = (
            self.x_coefficients[0] * x_array
            + self.x_coefficients[1] * y_array
            + self.x_coefficients[2]
        )
        projected_y = (
            self.y_coefficients[0] * x_array
            + self.y_coefficients[1] * y_array
            + self.y_coefficients[2]
        )
        if x_array.ndim == 0:
            return float(projected_x), float(projected_y)
        return projected_x, projected_y


@dataclass(frozen=True)
class ResidualRecord:
    name: str
    role: str
    actual_x: float
    actual_y: float
    predicted_x: float
    predicted_y: float
    delta_x_projected_m: float
    delta_y_projected_m: float
    projected_error_m: float
    error_m: float
    possible_outlier: bool = False


@dataclass(frozen=True)
class ErrorMetrics:
    count: int
    mean_m: float
    median_m: float
    rmse_m: float
    maximum_m: float


@dataclass(frozen=True)
class CandidateEvaluation:
    crs: str
    crs_name: str
    transform: AffineTransform
    residuals: tuple[ResidualRecord, ...]
    fit_metrics: ErrorMetrics
    validation_metrics: ErrorMetrics
    outlier_threshold_m: float


def project_control_points(
    points: Sequence[ControlPoint],
    target_crs: str,
) -> list[ProjectedControlPoint]:
    """Converte GCPs EPSG:4326 para o CRS candidato usando ordem lon/lat explícita."""

    try:
        transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
        coordinates = [transformer.transform(point.longitude, point.latitude) for point in points]
    except Exception as exc:  # pyproj expõe subclasses diferentes conforme a falha de CRS
        raise GeoReferenceError(f"Não foi possível projetar GCPs para {target_crs}: {exc}") from exc
    return [
        ProjectedControlPoint(point=point, x=float(x), y=float(y))
        for point, (x, y) in zip(points, coordinates, strict=True)
    ]


def fit_affine_transform(
    points: Sequence[ControlPoint],
    intermediate_crs: str = "EPSG:31983",
) -> AffineTransform:
    """Ajusta a transformação afim somente com pontos marcados como FIT."""

    fit_points = [point for point in points if point.role == "FIT"]
    if len(fit_points) < 3:
        raise GeoReferenceError("Uma transformação afim requer ao menos três GCPs FIT não colineares.")
    projected = project_control_points(fit_points, intermediate_crs)
    source = np.array([[item.point.pdf_x, item.point.pdf_y, 1.0] for item in projected], dtype=float)
    target = np.array([[item.x, item.y] for item in projected], dtype=float)
    coefficients, _, rank, _ = np.linalg.lstsq(source, target, rcond=None)
    if rank < 3:
        raise GeoReferenceError("Os GCPs FIT no PDF são colineares; distribua-os por todo o mapa.")
    x_coefficients = coefficients[:, 0]
    y_coefficients = coefficients[:, 1]
    determinant = float(
        x_coefficients[0] * y_coefficients[1]
        - x_coefficients[1] * y_coefficients[0]
    )
    if math.isclose(determinant, 0.0, abs_tol=1e-12):
        raise GeoReferenceError("A transformação afim calculada é singular.")
    return AffineTransform(
        intermediate_crs=CRS.from_user_input(intermediate_crs).to_string(),
        x_coefficients=x_coefficients,
        y_coefficients=y_coefficients,
        rank=int(rank),
        determinant=determinant,
    )


def _geodesic_distance_m(
    actual_longitude: float,
    actual_latitude: float,
    predicted_longitude: float,
    predicted_latitude: float,
) -> float:
    _, _, distance = GEOD.inv(
        actual_longitude,
        actual_latitude,
        predicted_longitude,
        predicted_latitude,
    )
    return abs(float(distance))


def calculate_residuals(
    points: Sequence[ControlPoint],
    transform: AffineTransform,
) -> list[ResidualRecord]:
    """Mede resíduos projetados e distância geodésica real, em metros."""

    projected = project_control_points(points, transform.intermediate_crs)
    inverse = Transformer.from_crs(transform.intermediate_crs, "EPSG:4326", always_xy=True)
    records: list[ResidualRecord] = []
    for item in projected:
        predicted_x, predicted_y = transform.apply(item.point.pdf_x, item.point.pdf_y)
        predicted_longitude, predicted_latitude = inverse.transform(predicted_x, predicted_y)
        delta_x = item.x - predicted_x
        delta_y = item.y - predicted_y
        records.append(
            ResidualRecord(
                name=item.point.name,
                role=item.point.role,
                actual_x=item.x,
                actual_y=item.y,
                predicted_x=predicted_x,
                predicted_y=predicted_y,
                delta_x_projected_m=delta_x,
                delta_y_projected_m=delta_y,
                projected_error_m=float(math.hypot(delta_x, delta_y)),
                error_m=_geodesic_distance_m(
                    item.point.longitude,
                    item.point.latitude,
                    predicted_longitude,
                    predicted_latitude,
                ),
            )
        )
    return records


def calculate_error_metrics(records: Sequence[ResidualRecord]) -> ErrorMetrics:
    if not records:
        raise GeoReferenceError("Não há resíduos para calcular métricas.")
    errors = [record.error_m for record in records]
    return ErrorMetrics(
        count=len(errors),
        mean_m=float(mean(errors)),
        median_m=float(median(errors)),
        rmse_m=float(math.sqrt(mean(error * error for error in errors))),
        maximum_m=float(max(errors)),
    )


def flag_possible_outliers(
    records: Sequence[ResidualRecord],
) -> tuple[list[ResidualRecord], float]:
    """Marca erros robustamente por mediana + 3×MAD, sem excluir nenhum GCP."""

    errors = np.asarray([record.error_m for record in records], dtype=float)
    center = float(np.median(errors))
    mad = float(np.median(np.abs(errors - center)))
    if mad > 1e-9:
        threshold = center + 3.0 * 1.4826 * mad
    else:
        threshold = max(center * 3.0, center + 1.0)
    return [replace(record, possible_outlier=record.error_m > threshold) for record in records], threshold


def evaluate_candidate(
    points: Sequence[ControlPoint],
    candidate_crs: str,
) -> CandidateEvaluation:
    transform = fit_affine_transform(points, candidate_crs)
    residuals, outlier_threshold = flag_possible_outliers(calculate_residuals(points, transform))
    fit_records = [record for record in residuals if record.role == "FIT"]
    validation_records = [record for record in residuals if record.role == "VALIDATION"]
    return CandidateEvaluation(
        crs=transform.intermediate_crs,
        crs_name=CRS.from_user_input(candidate_crs).name,
        transform=transform,
        residuals=tuple(residuals),
        fit_metrics=calculate_error_metrics(fit_records),
        validation_metrics=calculate_error_metrics(validation_records),
        outlier_threshold_m=outlier_threshold,
    )


def compare_candidate_crs(
    points: Sequence[ControlPoint],
    candidate_crs: Sequence[str] = CANDIDATE_CRS,
) -> tuple[list[CandidateEvaluation], CandidateEvaluation]:
    if len(candidate_crs) < 2:
        raise GeoReferenceError("Informe ao menos dois CRSs candidatos para comparação empírica.")
    evaluations = [evaluate_candidate(points, crs) for crs in candidate_crs]
    selected = min(
        evaluations,
        key=lambda evaluation: (
            evaluation.validation_metrics.rmse_m,
            evaluation.validation_metrics.maximum_m,
            evaluation.fit_metrics.rmse_m,
        ),
    )
    return evaluations, selected


def validate_operational_counts(points: Sequence[ControlPoint]) -> None:
    total = len(points)
    fit_count = sum(point.role == "FIT" for point in points)
    validation_count = sum(point.role == "VALIDATION" for point in points)
    problems: list[str] = []
    if total < MINIMUM_TOTAL_GCPS:
        problems.append(f"mínimo operacional de {MINIMUM_TOTAL_GCPS} GCPs no total")
    if fit_count < MINIMUM_FIT_GCPS:
        problems.append(f"ao menos {MINIMUM_FIT_GCPS} GCPs FIT")
    if validation_count < MINIMUM_VALIDATION_GCPS:
        problems.append(f"ao menos {MINIMUM_VALIDATION_GCPS} GCPs VALIDATION independentes")
    if problems:
        raise GeoReferenceError(
            "Pontos de controle insuficientes: é necessário " + ", ".join(problems) + "."
        )

    pdf_coordinates = {(point.pdf_x, point.pdf_y) for point in points}
    geographic_coordinates = {(point.latitude, point.longitude) for point in points}
    names = {point.name.casefold().strip() for point in points}
    if len(pdf_coordinates) != total:
        raise GeoReferenceError("Existem posições PDF duplicadas entre os GCPs.")
    if len(geographic_coordinates) != total:
        raise GeoReferenceError("Existem coordenadas geográficas duplicadas entre os GCPs.")
    if len(names) != total:
        raise GeoReferenceError("Existem nomes de GCP duplicados.")


def classify_quality(
    metrics: ErrorMetrics,
    maximum_rmse_m: float = DEFAULT_MAX_VALIDATION_RMSE_M,
    maximum_error_m: float = DEFAULT_MAX_VALIDATION_ERROR_M,
) -> tuple[str, bool, str]:
    """Classifica pela validação independente; limites são guardrails urbanos documentados."""

    if (
        metrics.rmse_m <= min(5.0, maximum_rmse_m)
        and metrics.maximum_m <= min(10.0, maximum_error_m)
    ):
        return (
            "EXCELENTE",
            True,
            "RMSE ≤ 5 m e máximo ≤ 10 m nos pontos VALIDATION.",
        )
    if metrics.rmse_m <= maximum_rmse_m and metrics.maximum_m <= maximum_error_m:
        return (
            "ACEITAVEL",
            True,
            f"RMSE ≤ {maximum_rmse_m:g} m e máximo ≤ {maximum_error_m:g} m nos pontos VALIDATION.",
        )
    if metrics.rmse_m <= 25.0:
        return (
            "REVISAR",
            False,
            "Erro comparável a uma parcela relevante de quadra urbana; revisar GCPs e outliers.",
        )
    return (
        "INADEQUADO",
        False,
        "RMSE de validação acima de 25 m pode deslocar uma demanda para o lado errado de uma divisa.",
    )


def _affine_callable(transform: AffineTransform):
    def apply(x: Any, y: Any, z: Any = None) -> tuple[Any, Any]:
        return transform.apply(x, y)

    return apply


def _projected_to_wgs84_callable(intermediate_crs: str):
    transformer = Transformer.from_crs(intermediate_crs, "EPSG:4326", always_xy=True)

    def apply(x: Any, y: Any, z: Any = None) -> tuple[Any, Any]:
        return transformer.transform(x, y)

    return apply


def georeference_collection(
    collection: dict[str, Any],
    transform: AffineTransform,
    *,
    georeferenced_at: str | None = None,
    rmse_m: float | None = None,
) -> dict[str, Any]:
    """Aplica PDF→CRS métrico→EPSG:4326 e preserva as propriedades existentes."""

    if collection.get("metadata", {}).get("coordinate_system") != "pdf":
        raise GeoReferenceError("O GeoJSON de entrada não está identificado como coordenadas PDF.")
    timestamp = georeferenced_at or datetime.now(timezone.utc).isoformat()
    inverse = _projected_to_wgs84_callable(transform.intermediate_crs)
    transformed_features: list[dict[str, Any]] = []
    for feature in collection.get("features", []):
        original_geometry = shape(feature["geometry"])
        projected_geometry = shapely_transform(_affine_callable(transform), original_geometry)
        wgs84_geometry = shapely_transform(inverse, projected_geometry)
        properties = dict(feature.get("properties", {}))
        properties.update(
            {
                "coordinate_system": "EPSG:4326",
                "source_pdf_coordinate_convention": properties.pop(
                    "pdf_coordinate_convention",
                    "points; origin at top-left; x right; y down",
                ),
                "georeferenced_at": timestamp,
                "transform_type": "affine",
                "intermediate_crs": transform.intermediate_crs,
                "rmse_m": rmse_m,
            }
        )
        transformed_features.append(
            {
                "type": "Feature",
                "id": feature.get("id"),
                "properties": properties,
                "geometry": mapping(wgs84_geometry),
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "areas_jardins",
        "metadata": {
            "source_coordinate_system": "pdf",
            "coordinate_system": "EPSG:4326",
            "transform_type": "affine",
            "intermediate_crs": transform.intermediate_crs,
            "georeferenced_at": timestamp,
            "rmse_m": rmse_m,
        },
        "features": transformed_features,
    }


def _project_area_geometries(
    collection: dict[str, Any],
    transform: AffineTransform,
) -> dict[str, BaseGeometry]:
    return {
        feature["properties"]["area"]: shapely_transform(
            _affine_callable(transform), shape(feature["geometry"])
        )
        for feature in collection.get("features", [])
    }


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return []


def infer_expected_adjacencies(
    source_geometries: dict[str, BaseGeometry],
    tolerance_pdf_points: float = 0.5,
) -> set[tuple[str, str]]:
    """Infere adjacência do próprio GeoJSON PDF, sem inventar relações espaciais."""

    return {
        tuple(sorted((left_name, right_name)))
        for (left_name, left), (right_name, right) in combinations(source_geometries.items(), 2)
        if left.distance(right) <= tolerance_pdf_points
    }


def validate_area_geometries(
    area_geometries: dict[str, BaseGeometry],
    expected_adjacencies: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Relata validade, overlaps, adjacências, componentes e holes sem corrigir divisas."""

    expected = {f"Tomé_{index}" for index in range(1, 7)}
    per_area: list[dict[str, Any]] = []
    errors: list[str] = []
    for area in sorted(area_geometries):
        geometry = area_geometries[area]
        parts = _polygon_parts(geometry)
        record = {
            "area": area,
            "geometry_type": geometry.geom_type,
            "is_valid": geometry.is_valid,
            "is_empty": geometry.is_empty,
            "component_count": len(parts),
            "hole_count": sum(len(part.interiors) for part in parts),
            "area_m2": geometry.area,
        }
        per_area.append(record)
        if not geometry.is_valid:
            errors.append(f"{area} possui geometria inválida.")
        if geometry.is_empty:
            errors.append(f"{area} desapareceu após a transformação.")
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            errors.append(f"{area} deixou de ser Polygon/MultiPolygon.")

    missing = sorted(expected - set(area_geometries))
    unexpected = sorted(set(area_geometries) - expected)
    if missing:
        errors.append("Áreas ausentes: " + ", ".join(missing))
    if unexpected:
        errors.append("Áreas inesperadas: " + ", ".join(unexpected))

    expected_adjacencies = expected_adjacencies or set()
    overlaps = []
    gaps = []
    pair_relationships = []
    for (left_name, left), (right_name, right) in combinations(area_geometries.items(), 2):
        intersection_area = left.intersection(right).area
        distance = left.distance(right)
        pair_key = tuple(sorted((left_name, right_name)))
        relationship = {
            "areas": [left_name, right_name],
            "expected_adjacent_from_pdf": pair_key in expected_adjacencies,
            "touches": left.touches(right),
            "distance_m": distance,
            "overlap_m2": intersection_area,
        }
        pair_relationships.append(relationship)
        if intersection_area > 0.01:
            overlaps.append(relationship)
        if pair_key in expected_adjacencies and distance > 0.05:
            gaps.append(relationship)

    boundary = unary_union(list(area_geometries.values()))
    boundary_parts = _polygon_parts(boundary)
    hole_areas = [Polygon(interior).area for part in boundary_parts for interior in part.interiors]
    return {
        "valid_for_output": not errors,
        "errors": errors,
        "areas": per_area,
        "overlaps": overlaps,
        "gaps_between_expected_adjacencies": gaps,
        "expected_adjacencies_from_pdf": [list(pair) for pair in sorted(expected_adjacencies)],
        "gap_detection_method": (
            "Pares separados por até 0,5 ponto no GeoJSON PDF são considerados adjacentes; "
            "distância projetada acima de 0,05 m é reportada, nunca corrigida."
        ),
        "pair_relationships": pair_relationships,
        "boundary": {
            "geometry_type": boundary.geom_type,
            "component_count": len(boundary_parts),
            "hole_count": len(hole_areas),
            "hole_area_m2": sum(hole_areas),
            "largest_hole_m2": max(hole_areas, default=0.0),
            "note": "Holes são apenas relatados; podem ser reais e não são preenchidos automaticamente.",
        },
    }


def validate_wgs84_collection(collection: dict[str, Any]) -> dict[str, Any]:
    """Confirma que nenhuma área se perdeu ou invalidou na conversão final para EPSG:4326."""

    expected = {f"Tomé_{index}" for index in range(1, 7)}
    areas: list[dict[str, Any]] = []
    errors: list[str] = []
    found: set[str] = set()
    for feature in collection.get("features", []):
        area = feature.get("properties", {}).get("area")
        geometry = shape(feature["geometry"])
        found.add(area)
        record = {
            "area": area,
            "geometry_type": geometry.geom_type,
            "is_valid": geometry.is_valid,
            "is_empty": geometry.is_empty,
        }
        areas.append(record)
        if not geometry.is_valid or geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            errors.append(f"{area} é inválida ou não poligonal em EPSG:4326.")
    if found != expected:
        errors.append("O conjunto de áreas em EPSG:4326 não corresponde a Tomé_1...Tomé_6.")
    return {"valid": not errors, "errors": errors, "areas": areas}


def build_boundary_collection(
    areas_collection: dict[str, Any],
) -> dict[str, Any]:
    """Gera a união espacial das seis áreas já em EPSG:4326."""

    geometries = [shape(feature["geometry"]) for feature in areas_collection.get("features", [])]
    if len(geometries) != 6:
        raise GeoReferenceError("O boundary requer exatamente as seis áreas Tomé.")
    boundary = unary_union(geometries)
    if boundary.is_empty or boundary.geom_type not in {"Polygon", "MultiPolygon"}:
        raise GeoReferenceError("A união das áreas não gerou um boundary poligonal válido.")
    if not boundary.is_valid:
        raise GeoReferenceError("O boundary resultante é inválido.")
    metadata = areas_collection.get("metadata", {})
    return {
        "type": "FeatureCollection",
        "name": "ugr_jardins_boundary",
        "metadata": {
            "coordinate_system": "EPSG:4326",
            "source": "areas_jardins.geojson",
            "georeferenced_at": metadata.get("georeferenced_at"),
            "transform_type": metadata.get("transform_type"),
            "intermediate_crs": metadata.get("intermediate_crs"),
            "rmse_m": metadata.get("rmse_m"),
        },
        "features": [
            {
                "type": "Feature",
                "id": "ugr_jardins",
                "properties": {
                    "name": "UGR Jardins",
                    "source_areas": [f"Tomé_{index}" for index in range(1, 7)],
                },
                "geometry": mapping(boundary),
            }
        ],
    }


def _candidate_to_dict(evaluation: CandidateEvaluation) -> dict[str, Any]:
    return {
        "crs": evaluation.crs,
        "crs_name": evaluation.crs_name,
        "transform": {
            "type": "affine",
            "x_coefficients": evaluation.transform.x_coefficients.tolist(),
            "y_coefficients": evaluation.transform.y_coefficients.tolist(),
            "rank": evaluation.transform.rank,
            "determinant": evaluation.transform.determinant,
            "orientation": "axis_orientation_reversed" if evaluation.transform.determinant < 0 else "axis_orientation_preserved",
        },
        "fit_metrics": asdict(evaluation.fit_metrics),
        "validation_metrics": asdict(evaluation.validation_metrics),
        "outlier_rule": {
            "method": "median + 3 * 1.4826 * MAD; fallback max(3*median, median+1m)",
            "threshold_m": evaluation.outlier_threshold_m,
            "automatic_exclusion": False,
        },
        "residuals": [asdict(record) for record in evaluation.residuals],
        "possible_outliers": [
            {"name": record.name, "role": record.role, "error_m": record.error_m}
            for record in evaluation.residuals
            if record.possible_outlier
        ],
    }


def render_residual_plot(evaluation: CandidateEvaluation, output_path: Path) -> None:
    """Mostra posições reais/preditas e um painel métrico dos vetores residuais."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure, (map_axis, vector_axis) = plt.subplots(1, 2, figsize=(15, 7), dpi=170)
    colors = {"FIT": "#1f77b4", "VALIDATION": "#e67e22"}
    for record in evaluation.residuals:
        color = colors[record.role]
        map_axis.plot(record.actual_x, record.actual_y, "o", color=color, markersize=5)
        map_axis.plot(record.predicted_x, record.predicted_y, "+", color="#111827", markersize=8)
        map_axis.annotate(
            "",
            xy=(record.actual_x, record.actual_y),
            xytext=(record.predicted_x, record.predicted_y),
            arrowprops={"arrowstyle": "->", "color": color, "lw": 1.0},
        )
        map_axis.annotate(
            f"{record.name}\n{record.error_m:.1f} m",
            (record.actual_x, record.actual_y),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=7,
        )
        vector_axis.arrow(
            0,
            0,
            record.delta_x_projected_m,
            record.delta_y_projected_m,
            color=color,
            width=0.0,
            head_width=max(0.15, evaluation.validation_metrics.rmse_m * 0.04),
            length_includes_head=True,
            alpha=0.8,
        )
        vector_axis.annotate(
            record.name,
            (record.delta_x_projected_m, record.delta_y_projected_m),
            fontsize=7,
        )

    map_axis.set_title(f"Posição real (●) e transformada (+) - {evaluation.crs}")
    map_axis.set_xlabel("X projetado (m)")
    map_axis.set_ylabel("Y projetado (m)")
    map_axis.set_aspect("equal", adjustable="datalim")
    map_axis.grid(alpha=0.25)
    vector_axis.axhline(0, color="#9ca3af", linewidth=0.8)
    vector_axis.axvline(0, color="#9ca3af", linewidth=0.8)
    vector_axis.set_title("Vetores residuais projetados, origem comum")
    vector_axis.set_xlabel("ΔX (m no CRS projetado)")
    vector_axis.set_ylabel("ΔY (m no CRS projetado)")
    vector_axis.set_aspect("equal", adjustable="datalim")
    vector_axis.grid(alpha=0.25)
    figure.suptitle(
        f"GeoJardins - RMSE FIT {evaluation.fit_metrics.rmse_m:.2f} m | "
        f"VALIDATION {evaluation.validation_metrics.rmse_m:.2f} m",
        fontsize=12,
    )
    figure.tight_layout()
    figure.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def write_georef_reports(report: dict[str, Any], json_path: Path, text_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "GeoJardins - Relatório de georreferenciamento",
        "=" * 48,
        f"Status: {report['status']}",
        f"Qualidade: {report['quality']['classification']}",
        f"Critério: {report['quality']['reason']}",
        f"CRS escolhido: {report['selected_crs']} ({report['selected_crs_name']})",
        f"Transformação: {report['transform_type']}",
        f"GCPs: {report['gcp_counts']['total']} total, {report['gcp_counts']['fit']} FIT, "
        f"{report['gcp_counts']['validation']} VALIDATION",
        "",
    ]
    for candidate in report["candidates"]:
        lines.extend(
            [
                f"Candidato {candidate['crs']} - {candidate['crs_name']}",
                f"  FIT: RMSE {candidate['fit_metrics']['rmse_m']:.3f} m | "
                f"média {candidate['fit_metrics']['mean_m']:.3f} m | "
                f"mediana {candidate['fit_metrics']['median_m']:.3f} m | "
                f"máximo {candidate['fit_metrics']['maximum_m']:.3f} m",
                f"  VALIDATION: RMSE {candidate['validation_metrics']['rmse_m']:.3f} m | "
                f"média {candidate['validation_metrics']['mean_m']:.3f} m | "
                f"mediana {candidate['validation_metrics']['median_m']:.3f} m | "
                f"máximo {candidate['validation_metrics']['maximum_m']:.3f} m",
                "",
            ]
        )
    lines.append("Resíduos do CRS escolhido:")
    selected_candidate = next(
        candidate for candidate in report["candidates"] if candidate["crs"] == report["selected_crs"]
    )
    for residual in selected_candidate["residuals"]:
        flag = "  <-- POSSÍVEL OUTLIER" if residual["possible_outlier"] else ""
        lines.append(
            f"  [{residual['role']:<10}] {residual['name']}: {residual['error_m']:.3f} m{flag}"
        )
    if report["distribution_warnings"]:
        lines.extend(["", "Warnings de distribuição:"])
        lines.extend(f"  - {warning}" for warning in report["distribution_warnings"])
    if report["geometry_validation"]["overlaps"]:
        lines.extend(["", "Sobreposições detectadas:"])
        for overlap in report["geometry_validation"]["overlaps"]:
            lines.append(
                f"  - {' x '.join(overlap['areas'])}: {overlap['overlap_m2']:.3f} m²"
            )
    if report["geometry_validation"]["gaps_between_expected_adjacencies"]:
        lines.extend(["", "Gaps entre áreas adjacentes no PDF:"])
        for gap in report["geometry_validation"]["gaps_between_expected_adjacencies"]:
            lines.append(f"  - {' x '.join(gap['areas'])}: {gap['distance_m']:.3f} m")
    # O BOM permite que Windows PowerShell 5.1 e o Bloco de Notas detectem UTF-8
    # sem depender da página de código ativa. JSON permanece UTF-8 sem BOM.
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def _write_geojson(collection: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_georeferencing(
    source_path: Path = Path("data/interim/areas_pdf_coordinates.geojson"),
    control_points_path: Path = Path("data/control_points/control_points.csv"),
    output_path: Path = Path("data/processed/areas_jardins.geojson"),
    boundary_path: Path = Path("data/processed/ugr_jardins_boundary.geojson"),
    report_json_path: Path = Path("output/debug/georef_report.json"),
    report_text_path: Path = Path("output/debug/georef_report.txt"),
    residual_plot_path: Path = Path("output/debug/gcp_residuals.png"),
    candidate_crs: Sequence[str] = CANDIDATE_CRS,
    maximum_validation_rmse_m: float = DEFAULT_MAX_VALIDATION_RMSE_M,
    maximum_validation_error_m: float = DEFAULT_MAX_VALIDATION_ERROR_M,
) -> dict[str, Any]:
    """Executa a Fase 2 e só publica GeoJSON quando validação independente for aceita."""

    if maximum_validation_rmse_m <= 0 or maximum_validation_error_m <= 0:
        raise GeoReferenceError("Os limites de qualidade precisam ser positivos.")

    try:
        points = load_control_points(control_points_path)
    except ControlPointError as exc:
        raise GeoReferenceError(str(exc)) from exc
    validate_operational_counts(points)
    if not source_path.is_file():
        raise GeoReferenceError(f"GeoJSON PDF não encontrado: {source_path}")
    collection = json.loads(source_path.read_text(encoding="utf-8"))
    source_area_geometries = {
        feature["properties"]["area"]: shape(feature["geometry"])
        for feature in collection.get("features", [])
    }
    if not source_area_geometries:
        raise GeoReferenceError("O GeoJSON PDF não contém geometrias.")
    map_bounds = unary_union(list(source_area_geometries.values())).bounds

    distribution_all = assess_spatial_distribution(points, map_bounds)
    fit_points = [point for point in points if point.role == "FIT"]
    distribution_fit = assess_spatial_distribution(
        fit_points,
        map_bounds,
        minimum_count=MINIMUM_FIT_GCPS,
        recommended_minimum=MINIMUM_FIT_GCPS,
    )
    evaluations, selected = compare_candidate_crs(points, candidate_crs)
    quality_class, quality_accepted, quality_reason = classify_quality(
        selected.validation_metrics,
        maximum_validation_rmse_m,
        maximum_validation_error_m,
    )
    projected_areas = _project_area_geometries(collection, selected.transform)
    timestamp = datetime.now(timezone.utc).isoformat()
    expected_adjacencies = infer_expected_adjacencies(source_area_geometries)
    geometry_validation = validate_area_geometries(projected_areas, expected_adjacencies)
    areas_wgs84 = georeference_collection(
        collection,
        selected.transform,
        georeferenced_at=timestamp,
        rmse_m=selected.validation_metrics.rmse_m,
    )
    final_validation = validate_wgs84_collection(areas_wgs84)
    geometry_validation["epsg4326"] = final_validation
    geometry_validation["valid_for_output"] = (
        geometry_validation["valid_for_output"] and final_validation["valid"]
    )
    status = "ACCEPTED" if quality_accepted and geometry_validation["valid_for_output"] else "REJECTED"
    distribution_warnings = [
        *(f"Todos os GCPs: {warning}" for warning in distribution_all["warnings"]),
        *(f"GCPs FIT: {warning}" for warning in distribution_fit["warnings"]),
    ]
    report = {
        "status": status,
        "generated_at": timestamp,
        "source": str(source_path),
        "control_points": str(control_points_path),
        "transform_type": "affine",
        "selected_crs": selected.crs,
        "selected_crs_name": selected.crs_name,
        "selection_method": "lowest independent VALIDATION geodesic RMSE, then maximum error, then FIT RMSE",
        "gcp_counts": {
            "total": len(points),
            "fit": len(fit_points),
            "validation": sum(point.role == "VALIDATION" for point in points),
        },
        "distribution": {"all": distribution_all, "fit": distribution_fit},
        "distribution_warnings": distribution_warnings,
        "quality": {
            "classification": quality_class,
            "accepted": quality_accepted,
            "reason": quality_reason,
            "operational_limits": {
                "validation_rmse_m": maximum_validation_rmse_m,
                "validation_maximum_m": maximum_validation_error_m,
                "basis": (
                    "Guardrails para classificação urbana junto a divisas; podem ser alterados "
                    "explicitamente por CLI, mas sempre são registrados."
                ),
            },
        },
        "candidates": [_candidate_to_dict(evaluation) for evaluation in evaluations],
        "geometry_validation": geometry_validation,
    }
    write_georef_reports(report, report_json_path, report_text_path)
    render_residual_plot(selected, residual_plot_path)

    if not quality_accepted:
        raise GeoReferenceError(
            f"Transformação rejeitada ({quality_class}): {quality_reason} "
            "Relatórios foram gerados, mas os GeoJSONs finais não foram sobrescritos."
        )
    if not geometry_validation["valid_for_output"]:
        raise GeoReferenceError(
            "Transformação não publicada porque a validação geométrica falhou: "
            + "; ".join(geometry_validation["errors"])
        )

    areas_wgs84["metadata"].update(
        {
            "gcp_count": len(points),
            "fit_gcp_count": len(fit_points),
            "validation_gcp_count": len(points) - len(fit_points),
            "validation_rmse_m": selected.validation_metrics.rmse_m,
            "validation_maximum_error_m": selected.validation_metrics.maximum_m,
            "quality_classification": quality_class,
        }
    )
    boundary = build_boundary_collection(areas_wgs84)
    _write_geojson(areas_wgs84, output_path)
    _write_geojson(boundary, boundary_path)
    LOGGER.info(
        "Georreferenciamento aceito: %s, RMSE VALIDATION %.3f m, máximo %.3f m",
        selected.crs,
        selected.validation_metrics.rmse_m,
        selected.validation_metrics.maximum_m,
    )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Georreferencia áreas PDF com ajuste e validação independentes.")
    parser.add_argument("--input", type=Path, default=Path("data/interim/areas_pdf_coordinates.geojson"))
    parser.add_argument("--control-points", type=Path, default=Path("data/control_points/control_points.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/areas_jardins.geojson"))
    parser.add_argument("--boundary", type=Path, default=Path("data/processed/ugr_jardins_boundary.geojson"))
    parser.add_argument("--report-json", type=Path, default=Path("output/debug/georef_report.json"))
    parser.add_argument("--report-txt", type=Path, default=Path("output/debug/georef_report.txt"))
    parser.add_argument("--residual-plot", type=Path, default=Path("output/debug/gcp_residuals.png"))
    parser.add_argument("--candidate-crs", nargs="+", default=list(CANDIDATE_CRS))
    parser.add_argument("--max-validation-rmse-m", type=float, default=DEFAULT_MAX_VALIDATION_RMSE_M)
    parser.add_argument("--max-validation-error-m", type=float, default=DEFAULT_MAX_VALIDATION_ERROR_M)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        run_georeferencing(
            source_path=args.input,
            control_points_path=args.control_points,
            output_path=args.output,
            boundary_path=args.boundary,
            report_json_path=args.report_json,
            report_text_path=args.report_txt,
            residual_plot_path=args.residual_plot,
            candidate_crs=args.candidate_crs,
            maximum_validation_rmse_m=args.max_validation_rmse_m,
            maximum_validation_error_m=args.max_validation_error_m,
        )
    except GeoReferenceError as exc:
        LOGGER.error("Georreferenciamento não executado: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
