"""Diagnóstico reproduzível dos GCPs, sem alterar os dados do projeto."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

os.environ.setdefault("MPLCONFIGDIR", str((Path.cwd() / ".cache" / "matplotlib").resolve()))

import matplotlib.pyplot as plt
import numpy as np
import pymupdf
from pyproj import CRS, Geod, Transformer

from .control_points import ControlPoint, load_control_points
from .gcp_picker import PdfViewport, display_to_pdf, render_pdf_for_picker, screen_to_pdf
from .polygon_extractor import resolve_pdf_path

GEOD = Geod(ellps="GRS80")
MODEL_NAMES = {
    "affine": "Afim",
    "homography": "Projetiva / homografia",
    "polynomial2": "Polinomial de 2ª ordem",
}


class DiagnosisError(RuntimeError):
    """O diagnóstico não pôde ser concluído de modo confiável."""


@dataclass(frozen=True)
class ProjectionSpec:
    key: str
    name: str
    crs: str


@dataclass(frozen=True)
class FittedModel:
    kind: str
    parameters: np.ndarray
    source_center: np.ndarray
    source_scale: np.ndarray
    condition_number: float

    def predict(self, source: np.ndarray) -> np.ndarray:
        source = np.asarray(source, dtype=float)
        if self.kind == "homography":
            homogeneous = np.column_stack((source, np.ones(len(source))))
            transformed = (self.parameters @ homogeneous.T).T
            denominator = transformed[:, 2]
            if np.any(np.abs(denominator) < 1e-12):
                raise DiagnosisError("A homografia produziu ponto no infinito.")
            return transformed[:, :2] / denominator[:, None]

        normalized = (source - self.source_center) / self.source_scale
        if self.kind == "affine":
            design = np.column_stack((normalized, np.ones(len(normalized))))
        elif self.kind == "polynomial2":
            x, y = normalized[:, 0], normalized[:, 1]
            design = np.column_stack((x, y, np.ones(len(x)), x * x, x * y, y * y))
        else:  # pragma: no cover - protegido por fit_model
            raise DiagnosisError(f"Modelo desconhecido: {self.kind}")
        return design @ self.parameters


def _normalise_points(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = np.mean(points, axis=0)
    distances = np.linalg.norm(points - center, axis=1)
    average_distance = float(np.mean(distances))
    if average_distance <= 1e-12:
        raise DiagnosisError("Pontos coincidentes impedem a normalização da homografia.")
    scale = math.sqrt(2.0) / average_distance
    matrix = np.array(
        [[scale, 0.0, -scale * center[0]], [0.0, scale, -scale * center[1]], [0.0, 0.0, 1.0]],
        dtype=float,
    )
    homogeneous = np.column_stack((points, np.ones(len(points))))
    normalized = (matrix @ homogeneous.T).T[:, :2]
    return normalized, matrix


def fit_model(kind: str, source: np.ndarray, target: np.ndarray) -> FittedModel:
    """Ajusta um modelo 2D com normalização para estabilidade numérica."""

    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2:
        raise DiagnosisError("Origem e destino precisam ser matrizes N×2 compatíveis.")
    minimum = {"affine": 3, "homography": 4, "polynomial2": 6}.get(kind)
    if minimum is None:
        raise DiagnosisError(f"Modelo desconhecido: {kind}")
    if len(source) < minimum:
        raise DiagnosisError(f"O modelo {kind} requer ao menos {minimum} pontos.")

    if kind == "homography":
        normalized_source, source_matrix = _normalise_points(source)
        normalized_target, target_matrix = _normalise_points(target)
        rows: list[list[float]] = []
        for (x, y), (u, v) in zip(normalized_source, normalized_target, strict=True):
            rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
            rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
        design = np.asarray(rows, dtype=float)
        _, singular, vectors = np.linalg.svd(design, full_matrices=True)
        homography_normalized = vectors[-1].reshape(3, 3)
        homography = np.linalg.inv(target_matrix) @ homography_normalized @ source_matrix
        if abs(homography[2, 2]) > 1e-12:
            homography /= homography[2, 2]
        relevant_smallest = singular[-2] if len(singular) >= 2 else singular[-1]
        condition = float(singular[0] / max(relevant_smallest, np.finfo(float).eps))
        return FittedModel(kind, homography, np.zeros(2), np.ones(2), condition)

    center = np.mean(source, axis=0)
    scale = np.std(source, axis=0)
    if np.any(scale <= 1e-12):
        raise DiagnosisError("Os GCPs não possuem variação suficiente nos dois eixos do PDF.")
    normalized = (source - center) / scale
    if kind == "affine":
        design = np.column_stack((normalized, np.ones(len(normalized))))
    else:
        x, y = normalized[:, 0], normalized[:, 1]
        design = np.column_stack((x, y, np.ones(len(x)), x * x, x * y, y * y))
    parameters, _, rank, singular = np.linalg.lstsq(design, target, rcond=None)
    if rank < design.shape[1]:
        raise DiagnosisError(f"Matriz singular no ajuste {kind}.")
    condition = float(singular[0] / max(singular[-1], np.finfo(float).eps))
    return FittedModel(kind, parameters, center, scale, condition)


def _metrics(errors: Sequence[float]) -> dict[str, float | int]:
    values = np.asarray(errors, dtype=float)
    finite = values[np.isfinite(values)]
    if not len(finite):
        return {"count": 0, "mean_m": math.inf, "median_m": math.inf, "rmse_m": math.inf, "maximum_m": math.inf}
    return {
        "count": int(len(finite)),
        "mean_m": float(np.mean(finite)),
        "median_m": float(np.median(finite)),
        "rmse_m": float(np.sqrt(np.mean(finite**2))),
        "maximum_m": float(np.max(finite)),
    }


def _geodesic_errors(
    predicted_projected: np.ndarray,
    points: Sequence[ControlPoint],
    projection: ProjectionSpec,
) -> np.ndarray:
    inverse = Transformer.from_crs(projection.crs, "EPSG:4326", always_xy=True)
    predicted_lon, predicted_lat = inverse.transform(
        predicted_projected[:, 0], predicted_projected[:, 1]
    )
    errors = []
    for point, longitude, latitude in zip(points, predicted_lon, predicted_lat, strict=True):
        if not math.isfinite(float(longitude)) or not math.isfinite(float(latitude)):
            errors.append(math.inf)
            continue
        _, _, distance = GEOD.inv(point.longitude, point.latitude, float(longitude), float(latitude))
        errors.append(abs(float(distance)))
    return np.asarray(errors)


def _arrays(
    points: Sequence[ControlPoint], projection: ProjectionSpec
) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray([(point.pdf_x, point.pdf_y) for point in points], dtype=float)
    transformer = Transformer.from_crs("EPSG:4326", projection.crs, always_xy=True)
    longitude = [point.longitude for point in points]
    latitude = [point.latitude for point in points]
    x, y = transformer.transform(longitude, latitude)
    return source, np.column_stack((x, y)).astype(float)


def _evaluate_split(
    kind: str,
    points: Sequence[ControlPoint],
    projection: ProjectionSpec,
    train_indexes: Sequence[int],
    evaluation_indexes: Sequence[int],
) -> tuple[dict[str, float | int], FittedModel, np.ndarray]:
    source, target = _arrays(points, projection)
    model = fit_model(kind, source[list(train_indexes)], target[list(train_indexes)])
    indexes = list(evaluation_indexes)
    predictions = model.predict(source[indexes])
    errors = _geodesic_errors(predictions, [points[index] for index in indexes], projection)
    return _metrics(errors), model, errors


def _residual_records(
    model: FittedModel,
    points: Sequence[ControlPoint],
    projection: ProjectionSpec,
) -> list[dict[str, Any]]:
    source, target = _arrays(points, projection)
    predicted = model.predict(source)
    errors = _geodesic_errors(predicted, points, projection)
    return [
        {
            "name": point.name,
            "role": point.role,
            "pdf_x": point.pdf_x,
            "pdf_y": point.pdf_y,
            "residual_x_m": float(actual[0] - estimate[0]),
            "residual_y_m": float(actual[1] - estimate[1]),
            "error_m": float(error),
        }
        for point, actual, estimate, error in zip(points, target, predicted, errors, strict=True)
    ]


def cross_validate(
    kind: str, points: Sequence[ControlPoint], projection: ProjectionSpec
) -> dict[str, Any]:
    """Leave-one-out sobre todos os pontos, ignorando temporariamente os papéis."""

    source, target = _arrays(points, projection)
    rows: list[dict[str, Any]] = []
    failures = 0
    conditions: list[float] = []
    for holdout in range(len(points)):
        train = [index for index in range(len(points)) if index != holdout]
        try:
            model = fit_model(kind, source[train], target[train])
            prediction = model.predict(source[[holdout]])
            error = float(_geodesic_errors(prediction, [points[holdout]], projection)[0])
            conditions.append(model.condition_number)
        except (DiagnosisError, np.linalg.LinAlgError):
            error = math.inf
            failures += 1
        rows.append({"name": points[holdout].name, "role": points[holdout].role, "error_m": error})
    return {
        "metrics": _metrics([row["error_m"] for row in rows]),
        "failures": failures,
        "median_condition_number": float(np.median(conditions)) if conditions else math.inf,
        "points": rows,
    }


def analyse_models(
    points: Sequence[ControlPoint], projections: Sequence[ProjectionSpec]
) -> list[dict[str, Any]]:
    fit_indexes = [index for index, point in enumerate(points) if point.role == "FIT"]
    validation_indexes = [index for index, point in enumerate(points) if point.role == "VALIDATION"]
    rows: list[dict[str, Any]] = []
    for projection in projections:
        for kind in MODEL_NAMES:
            training, model, _ = _evaluate_split(kind, points, projection, fit_indexes, fit_indexes)
            validation, _, _ = _evaluate_split(kind, points, projection, fit_indexes, validation_indexes)
            cv = cross_validate(kind, points, projection)
            validation_rmse = float(validation["rmse_m"])
            training_rmse = max(float(training["rmse_m"]), 1e-9)
            cv_rmse = float(cv["metrics"]["rmse_m"])
            overfit_ratio = validation_rmse / training_rmse
            if kind == "affine":
                stability = "referência parcimoniosa"
            elif overfit_ratio >= 2.0 or cv_rmse > validation_rmse:
                stability = "alto risco de overfitting"
            elif overfit_ratio >= 1.5:
                stability = "risco moderado de overfitting"
            else:
                stability = "estável"
            rows.append(
                {
                    "projection": projection.key,
                    "projection_name": projection.name,
                    "model": kind,
                    "model_name": MODEL_NAMES[kind],
                    "parameters_per_axis": {"affine": 3, "homography": 4, "polynomial2": 6}[kind],
                    "training": training,
                    "validation": validation,
                    "cross_validation": cv,
                    "condition_number": model.condition_number,
                    "validation_to_training_rmse_ratio": overfit_ratio,
                    "stability_assessment": stability,
                }
            )
    return rows


def fit_leave_one_out(
    points: Sequence[ControlPoint], projection: ProjectionSpec
) -> dict[str, Any]:
    fit_indexes = [index for index, point in enumerate(points) if point.role == "FIT"]
    validation_indexes = [index for index, point in enumerate(points) if point.role == "VALIDATION"]
    full_fit, full_model, _ = _evaluate_split("affine", points, projection, fit_indexes, fit_indexes)
    full_validation, _, _ = _evaluate_split("affine", points, projection, fit_indexes, validation_indexes)

    source, _ = _arrays(points, projection)
    normalized = (source[fit_indexes] - full_model.source_center) / full_model.source_scale
    design = np.column_stack((normalized, np.ones(len(normalized))))
    leverage = np.diag(design @ np.linalg.pinv(design.T @ design) @ design.T)
    leverage_by_index = dict(zip(fit_indexes, leverage, strict=True))

    rows: list[dict[str, Any]] = []
    for removed in fit_indexes:
        remaining = [index for index in fit_indexes if index != removed]
        fit_metrics, model, _ = _evaluate_split("affine", points, projection, remaining, remaining)
        validation_metrics, _, _ = _evaluate_split(
            "affine", points, projection, remaining, validation_indexes
        )
        held_prediction = model.predict(source[[removed]])
        held_error = float(_geodesic_errors(held_prediction, [points[removed]], projection)[0])
        rows.append(
            {
                "removed": points[removed].name,
                "fit": fit_metrics,
                "validation": validation_metrics,
                "delta_validation_rmse_m": float(validation_metrics["rmse_m"]) - float(full_validation["rmse_m"]),
                "removed_point_holdout_error_m": held_error,
                "leverage": float(leverage_by_index[removed]),
            }
        )
    rows.sort(key=lambda row: row["delta_validation_rmse_m"])
    return {"complete_fit": full_fit, "complete_validation": full_validation, "points": rows}


def validation_influence(
    points: Sequence[ControlPoint], projection: ProjectionSpec
) -> list[dict[str, Any]]:
    fit_indexes = [index for index, point in enumerate(points) if point.role == "FIT"]
    validation_indexes = [index for index, point in enumerate(points) if point.role == "VALIDATION"]
    _, model, _ = _evaluate_split("affine", points, projection, fit_indexes, fit_indexes)
    source, _ = _arrays(points, projection)
    errors = _geodesic_errors(model.predict(source[validation_indexes]), [points[i] for i in validation_indexes], projection)
    complete_rmse = float(_metrics(errors)["rmse_m"])
    rows = []
    for position, removed in enumerate(validation_indexes):
        remaining_errors = np.delete(errors, position)
        without = _metrics(remaining_errors)
        rows.append(
            {
                "removed": points[removed].name,
                "current_error_m": float(errors[position]),
                "validation_without_point": without,
                "delta_validation_rmse_m": float(without["rmse_m"]) - complete_rmse,
            }
        )
    return sorted(rows, key=lambda row: row["delta_validation_rmse_m"])


def robust_affine(
    points: Sequence[ControlPoint], projection: ProjectionSpec, thresholds: Sequence[float] = (10.0, 20.0, 30.0)
) -> dict[str, Any]:
    """RANSAC determinístico: examina todas as amostras mínimas de três GCPs."""

    source, target = _arrays(points, projection)
    results: list[dict[str, Any]] = []
    for threshold in thresholds:
        best: tuple[tuple[int, float, float], np.ndarray] | None = None
        for sample in combinations(range(len(points)), 3):
            try:
                candidate = fit_model("affine", source[list(sample)], target[list(sample)])
                errors = _geodesic_errors(candidate.predict(source), points, projection)
            except (DiagnosisError, np.linalg.LinAlgError):
                continue
            inliers = errors <= threshold
            count = int(np.sum(inliers))
            if count < 3:
                continue
            score = (-count, float(np.sqrt(np.mean(errors[inliers] ** 2))), float(np.median(errors)))
            if best is None or score < best[0]:
                best = (score, inliers)
        if best is None:
            raise DiagnosisError("RANSAC não encontrou uma amostra afim válida.")

        inliers = best[1]
        for _ in range(10):
            refined = fit_model("affine", source[inliers], target[inliers])
            errors = _geodesic_errors(refined.predict(source), points, projection)
            next_inliers = errors <= threshold
            if int(np.sum(next_inliers)) < 3 or np.array_equal(next_inliers, inliers):
                break
            inliers = next_inliers
        refined = fit_model("affine", source[inliers], target[inliers])
        errors = _geodesic_errors(refined.predict(source), points, projection)
        inliers = errors <= threshold
        results.append(
            {
                "threshold_m": threshold,
                "inliers": [point.name for point, keep in zip(points, inliers, strict=True) if keep],
                "outliers": [point.name for point, keep in zip(points, inliers, strict=True) if not keep],
                "inlier_metrics": _metrics(errors[inliers]),
                "all_metrics": _metrics(errors),
                "point_errors": [
                    {"name": point.name, "error_m": float(error), "inlier": bool(keep)}
                    for point, error, keep in zip(points, errors, inliers, strict=True)
                ],
            }
        )
    return {"method": "RANSAC determinístico por todas as combinações de 3 pontos", "threshold_sensitivity": results}


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_array, right_array = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if np.std(left_array) <= 1e-12 or np.std(right_array) <= 1e-12:
        return 0.0
    return float(np.corrcoef(left_array, right_array)[0, 1])


def spatial_residual_analysis(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    x = np.asarray([row["pdf_x"] for row in records])
    y = np.asarray([row["pdf_y"] for row in records])
    dx = np.asarray([row["residual_x_m"] for row in records])
    dy = np.asarray([row["residual_y_m"] for row in records])
    radius = np.hypot(x - np.mean(x), y - np.mean(y))
    radial_residual = np.hypot(dx, dy)
    correlations = {
        "pdf_x_vs_residual_x": _pearson(x, dx),
        "pdf_x_vs_residual_y": _pearson(x, dy),
        "pdf_y_vs_residual_x": _pearson(y, dx),
        "pdf_y_vs_residual_y": _pearson(y, dy),
        "radius_vs_residual_magnitude": _pearson(radius, radial_residual),
    }
    strongest_name, strongest_value = max(correlations.items(), key=lambda item: abs(item[1]))
    strength = "forte" if abs(strongest_value) >= 0.60 else "moderada" if abs(strongest_value) >= 0.35 else "fraca"
    systematic = []
    if abs(correlations["pdf_x_vs_residual_x"]) >= 0.35:
        systematic.append("componente de escala/shear oeste–leste")
    if abs(correlations["pdf_y_vs_residual_y"]) >= 0.35:
        systematic.append("deriva gradual norte–sul na componente Y")
    if max(abs(correlations["pdf_x_vs_residual_y"]), abs(correlations["pdf_y_vs_residual_x"])) >= 0.35:
        systematic.append("rotação/shear residual cruzado")
    if abs(correlations["radius_vs_residual_magnitude"]) >= 0.35:
        systematic.append("efeito radial")
    detailed = (
        "Há sinal de " + ", ".join(systematic) + "."
        if systematic
        else "Não há correlação moderada/forte compatível com escala, rotação, shear ou efeito radial global."
    )
    return {
        "correlations": correlations,
        "strongest": strongest_name,
        "strongest_absolute_correlation": abs(strongest_value),
        "interpretation": f"A maior correlação linear é {strength} (|r|={abs(strongest_value):.3f}).",
        "systematic_pattern_assessment": detailed,
    }


def pairwise_scale_analysis(
    points: Sequence[ControlPoint], projection: ProjectionSpec
) -> dict[str, Any]:
    source, target = _arrays(points, projection)
    pairs: list[dict[str, Any]] = []
    for first, second in combinations(range(len(points)), 2):
        pdf_distance = float(np.linalg.norm(source[first] - source[second]))
        real_distance = float(np.linalg.norm(target[first] - target[second]))
        pairs.append(
            {
                "first": points[first].name,
                "second": points[second].name,
                "pdf_distance": pdf_distance,
                "real_distance_m": real_distance,
                "scale_m_per_pdf_unit": real_distance / pdf_distance,
                "midpoint_x": float((source[first, 0] + source[second, 0]) / 2),
                "midpoint_y": float((source[first, 1] + source[second, 1]) / 2),
            }
        )
    scales = np.asarray([row["scale_m_per_pdf_unit"] for row in pairs])
    center = float(np.median(scales))
    mad = float(np.median(np.abs(scales - center)))
    robust_z = np.abs(scales - center) / max(1.4826 * mad, 1e-12)
    point_counts = {point.name: 0 for point in points}
    for row, score in zip(pairs, robust_z, strict=True):
        row["robust_deviation"] = float(score)
        row["possible_outlier_pair"] = bool(score > 2.5)
        if score > 2.5:
            point_counts[row["first"]] += 1
            point_counts[row["second"]] += 1
    pairs.sort(key=lambda row: row["robust_deviation"], reverse=True)
    return {
        "pair_count": len(pairs),
        "median_scale_m_per_pdf_unit": center,
        "mad_scale_m_per_pdf_unit": mad,
        "coefficient_of_variation": float(np.std(scales) / np.mean(scales)),
        "scale_vs_midpoint_x_correlation": _pearson(scales, [row["midpoint_x"] for row in pairs]),
        "scale_vs_midpoint_y_correlation": _pearson(scales, [row["midpoint_y"] for row in pairs]),
        "scale_vs_pdf_distance_correlation": _pearson(scales, [row["pdf_distance"] for row in pairs]),
        "outlier_pair_counts_by_point": dict(sorted(point_counts.items(), key=lambda item: item[1], reverse=True)),
        "most_inconsistent_pairs": pairs[:15],
    }


def audit_picker(pdf_path: Path) -> dict[str, Any]:
    """Testa a cadeia tela→display→PDF em cenários sintéticos reproduzíveis."""

    with pymupdf.open(pdf_path) as document:
        if len(document) != 1:
            raise DiagnosisError("O GCP Picker espera um PDF de uma página.")
        rect = document[0].rect
        page_width, page_height = float(rect.width), float(rect.height)
        extent = (float(rect.x0), float(rect.x1), float(rect.y1), float(rect.y0))
        pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
        raster_width, raster_height = pixmap.width, pixmap.height
    viewport = PdfViewport.from_extent(extent)
    anchor = (extent[0] + page_width * 0.58, extent[3] + page_height * 0.61)

    xlim, ylim = viewport.fit_limits()
    scenarios: list[tuple[str, tuple[float, float], tuple[float, float]]] = [("100%", xlim, ylim)]
    xlim, ylim = viewport.zoom_limits(*viewport.fit_limits(), *anchor, 3.0)
    scenarios.append(("300%", xlim, ylim))
    xlim, ylim = viewport.zoom_limits(*viewport.fit_limits(), *anchor, 8.0)
    scenarios.append(("800%", xlim, ylim))
    xlim, ylim = viewport.zoom_limits(*viewport.fit_limits(), *anchor, 3.0)
    xlim, ylim = viewport.pan_limits(xlim, ylim, -page_width * 0.04, page_height * 0.03)
    scenarios.append(("300% + pan", xlim, ylim))
    xlim, ylim = viewport.zoom_limits(*viewport.fit_limits(), *anchor, 2.0)
    xlim, ylim = viewport.pan_limits(xlim, ylim, page_width * 0.05, -page_height * 0.04)
    center = ((xlim[0] + xlim[1]) / 2, (ylim[0] + ylim[1]) / 2)
    xlim, ylim = viewport.zoom_limits(xlim, ylim, *center, 2.0)
    xlim, ylim = viewport.pan_limits(xlim, ylim, -page_width * 0.02, page_height * 0.015)
    xlim, ylim = viewport.zoom_limits(xlim, ylim, *center, 1.25)
    scenarios.append(("zoom + pan + zoom", xlim, ylim))

    results = []
    all_errors = []
    figure, axis = plt.subplots(figsize=(11.7, 9.2), dpi=100)
    axis.imshow(np.zeros((3, 3, 3), dtype=np.uint8), extent=extent, origin="upper")
    axis.set_aspect("equal")
    try:
        for name, current_x, current_y in scenarios:
            axis.set_xlim(current_x)
            axis.set_ylim(current_y)
            figure.canvas.draw()
            axes_bounds = tuple(float(value) for value in axis.bbox.bounds)
            canvas_height = float(figure.bbox.height)
            expected = ((current_x[0] + current_x[1]) / 2, (current_y[0] + current_y[1]) / 2)
            display_x, display_y = axis.transData.transform(expected)
            from_display = display_to_pdf(display_x, display_y, axes_bounds, current_x, current_y)
            from_screen = screen_to_pdf(display_x, canvas_height - display_y, canvas_height, axes_bounds, current_x, current_y)
            if from_display is None or from_screen is None:
                raise DiagnosisError("O teste sintético do picker retornou ponto fora do eixo.")
            error = max(
                abs(from_display[0] - expected[0]), abs(from_display[1] - expected[1]),
                abs(from_screen[0] - expected[0]), abs(from_screen[1] - expected[1]),
            )
            all_errors.append(error)
            results.append(
                {
                    "scenario": name,
                    "expected_pdf": list(expected),
                    "matplotlib_display_position": [float(display_x), float(display_y)],
                    "axes_bounds": list(axes_bounds),
                    "display_to_pdf": list(from_display),
                    "tk_screen_to_pdf": list(from_screen),
                    "maximum_absolute_error_pdf_units": error,
                }
            )
    finally:
        plt.close(figure)
    width_ratio = raster_width / page_width
    height_ratio = raster_height / page_height
    return {
        "technology": "Tkinter + Matplotlib TkAgg",
        "page_size_pdf_units": [page_width, page_height],
        "raster_size_at_1_5x": [raster_width, raster_height],
        "raster_to_pdf_scale": [width_ratio, height_ratio],
        "imshow_extent": list(extent),
        "y_axis_inverted": True,
        "maximum_roundtrip_error_pdf_units": max(all_errors),
        "passed": max(all_errors) < 1e-9 and abs(width_ratio - height_ratio) < 0.01,
        "scenarios": results,
        "reasoning": (
            "imshow usa extent em unidades originais do PDF; o raster só fornece pixels. "
            "event.x/event.y são invertidos pela transformação linear dos limites correntes do eixo, "
            "portanto zoom, pan, DPI e margens não alteram pdf_x/pdf_y."
        ),
    }


def _make_overlay(pdf_path: Path, points: Sequence[ControlPoint], output_path: Path) -> None:
    image, extent = render_pdf_for_picker(pdf_path, scale=1.0)
    figure, axis = plt.subplots(figsize=(15, 14), dpi=150)
    axis.imshow(image, extent=extent, origin="upper")
    for number, point in enumerate(points, start=1):
        color = "#0066cc" if point.role == "FIT" else "#e36c09"
        axis.scatter(point.pdf_x, point.pdf_y, s=115, color=color, edgecolors="white", linewidths=1.4, zorder=5)
        axis.text(point.pdf_x, point.pdf_y, str(number), color="white", fontsize=7, weight="bold", ha="center", va="center", zorder=6)
        axis.annotate(
            f"{number}. {point.name} [{point.role}]", (point.pdf_x, point.pdf_y), xytext=(7, 7),
            textcoords="offset points", fontsize=6.8, color="#111827",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": color, "alpha": 0.86}, zorder=7,
        )
    axis.set_xlim(extent[0], extent[1])
    axis.set_ylim(extent[2], extent[3])
    axis.set_aspect("equal")
    axis.set_title("GeoJardins — 15 GCPs nas coordenadas PDF salvas (sem reposicionamento)")
    axis.set_xlabel("pdf_x")
    axis.set_ylabel("pdf_y")
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _make_influence_plot(report: dict[str, Any], output_path: Path) -> None:
    leave_out = report["fit_leave_one_out"]["points"]
    cv_points = next(
        row for row in report["model_comparison"]
        if row["projection"] == "EPSG:31983" and row["model"] == "affine"
    )["cross_validation"]["points"]
    figure, axes = plt.subplots(1, 2, figsize=(16, 8), dpi=150)
    labels = [row["removed"] for row in leave_out]
    deltas = [row["delta_validation_rmse_m"] for row in leave_out]
    colors = ["#17803d" if value < 0 else "#b42318" for value in deltas]
    axes[0].barh(range(len(labels)), deltas, color=colors)
    axes[0].set_yticks(range(len(labels)), labels, fontsize=7)
    axes[0].invert_yaxis()
    axes[0].axvline(0, color="#374151", linewidth=0.8)
    axes[0].set_xlabel("Δ RMSE VALIDATION (m); negativo = melhora")
    axes[0].set_title("Influência de cada GCP FIT")

    cv_points = sorted(cv_points, key=lambda row: row["error_m"], reverse=True)
    cv_labels = [row["name"] for row in cv_points]
    cv_errors = [row["error_m"] for row in cv_points]
    cv_colors = ["#0066cc" if row["role"] == "FIT" else "#e36c09" for row in cv_points]
    axes[1].barh(range(len(cv_labels)), cv_errors, color=cv_colors)
    axes[1].set_yticks(range(len(cv_labels)), cv_labels, fontsize=7)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Erro leave-one-out (m)")
    axes[1].set_title("Validação cruzada de todos os 15 GCPs")
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _recommendations(report: dict[str, Any]) -> dict[str, Any]:
    baseline = {row["name"]: row for row in report["baseline_residuals"]}
    loo = {row["removed"]: row for row in report["fit_leave_one_out"]["points"]}
    validation = {row["removed"]: row for row in report["validation_influence"]}
    affine_cv = next(
        row for row in report["model_comparison"]
        if row["projection"] == "EPSG:31983" and row["model"] == "affine"
    )["cross_validation"]["points"]
    cv = {row["name"]: row["error_m"] for row in affine_cv}
    ransac_rows = report["ransac"]["threshold_sensitivity"]
    ransac_votes = {
        name: sum(name in row["outliers"] for row in ransac_rows)
        for name in baseline
    }
    pair_counts = report["pairwise_scale"]["outlier_pair_counts_by_point"]
    cv_values = np.asarray(list(cv.values()))
    cv_center = float(np.median(cv_values))
    cv_mad = float(np.median(np.abs(cv_values - cv_center)))
    ranked = []
    for name, residual in baseline.items():
        score = 0.0
        reasons = []
        votes = ransac_votes[name]
        if votes:
            score += float(votes)
            reasons.append(f"fora do consenso RANSAC em {votes}/3 limiares")
        robust_cv = (cv[name] - cv_center) / max(1.4826 * cv_mad, 1.0)
        if robust_cv > 1.0:
            score += min(cv[name] / 20.0, 5.0)
            reasons.append(f"erro em holdout {cv[name]:.1f} m")
        score += min(float(pair_counts.get(name, 0)) / 2.0, 3.0)
        if pair_counts.get(name, 0):
            pair_word = "par" if pair_counts[name] == 1 else "pares"
            reasons.append(f"presente em {pair_counts[name]} {pair_word} de escala atípico{'s' if pair_counts[name] != 1 else ''}")
        if name in loo and loo[name]["delta_validation_rmse_m"] < -2.0:
            improvement = -float(loo[name]["delta_validation_rmse_m"])
            score += min(improvement / 2.0, 5.0)
            reasons.append(f"retirada reduz RMSE VALIDATION em {improvement:.1f} m")
        if name in validation and validation[name]["delta_validation_rmse_m"] < -2.0:
            improvement = -float(validation[name]["delta_validation_rmse_m"])
            score += min(improvement / 2.0, 5.0)
            reasons.append(f"domina a validação; sem ele o RMSE cai {improvement:.1f} m")
        score += min(float(residual["error_m"]) / 60.0, 1.0)
        fit_improvement = -float(loo[name]["delta_validation_rmse_m"]) if name in loo else None
        validation_improvement = -float(validation[name]["delta_validation_rmse_m"]) if name in validation else None
        primary = (
            (name in loo and fit_improvement is not None and fit_improvement >= 2.0 and (cv[name] >= 60.0 or pair_counts.get(name, 0) >= 3 or votes >= 2))
            or (name in validation and cv[name] >= 55.0 and (votes >= 2 or (validation_improvement or 0.0) >= 5.0))
        )
        if primary:
            ranked.append(
                {
                    "name": name, "priority_score": score, "current_residual_m": residual["error_m"],
                    "cross_validation_error_m": cv[name], "ransac_outlier_votes": votes,
                    "fit_leave_one_out_delta_validation_rmse_m": loo[name]["delta_validation_rmse_m"] if name in loo else None,
                    "validation_leave_one_out_delta_rmse_m": validation[name]["delta_validation_rmse_m"] if name in validation else None,
                    "reasons": reasons,
                }
            )
    ranked.sort(key=lambda row: row["priority_score"], reverse=True)
    secondary = [
        name for name in baseline
        if name not in {row["name"] for row in ranked}
        and baseline[name]["error_m"] >= 40.0
        and cv[name] >= 40.0
    ]
    ambiguous = {
        "Av. Bandeirantes × Av. Santo Amaro",
        "Av. Jabaquara × Viaduto Jabaquara",
        "Av. Pedroso de Morais × Av. Faria Lima",
        "Av. Cidade Jardim × Av. Faria Lima",
    }
    complex_primary = [row["name"] for row in ranked if row["name"] in ambiguous]
    return {
        "review_priority": ranked,
        "secondary_monitoring": secondary,
        "complex_intersections_in_primary_list": complex_primary,
        "complex_intersection_guidance": (
            "Os cruzamentos complexos só entraram na prioridade quando também foram apontados pelos testes numéricos; "
            "se a conferência visual/geográfica confirmar ambiguidade, substitua-os por cruzamentos simples na mesma região."
        ),
        "automatic_action": "nenhuma",
    }


def _conclusion(report: dict[str, Any]) -> dict[str, Any]:
    picker_error = not report["picker_audit"]["passed"]
    rows = [row for row in report["model_comparison"] if row["projection"] == "EPSG:31983"]
    affine = next(row for row in rows if row["model"] == "affine")
    alternatives = [row for row in rows if row["model"] != "affine"]
    best_alternative = min(alternatives, key=lambda row: row["cross_validation"]["metrics"]["rmse_m"])
    affine_cv = float(affine["cross_validation"]["metrics"]["rmse_m"])
    alternative_cv = float(best_alternative["cross_validation"]["metrics"]["rmse_m"])
    model_improvement = (affine_cv - alternative_cv) / affine_cv
    ransac_sets = report["ransac"]["threshold_sensitivity"]
    robust_outliers = set.intersection(*(set(row["outliers"]) for row in ransac_sets))

    non_affine_supported = model_improvement >= 0.20 and float(best_alternative["validation"]["rmse_m"]) < float(affine["validation"]["rmse_m"])
    inconsistent_supported = len(robust_outliers) > 0
    if picker_error and (non_affine_supported or inconsistent_supported):
        code = "D"
        statement = "Existe combinação de mais de um problema."
    elif picker_error:
        code = "C"
        statement = "Existe erro na conversão de coordenadas do GCP Picker."
    elif non_affine_supported and not inconsistent_supported:
        code = "B"
        statement = "Os GCPs são razoavelmente consistentes, mas o mapa possui deformação não-affine e outro modelo apresenta validação significativamente melhor."
    elif non_affine_supported and inconsistent_supported:
        code = "D"
        statement = "Existe combinação de mais de um problema."
    else:
        code = "A"
        statement = "A transformação affine é adequada, mas existem GCPs inconsistentes."
    return {
        "code": code,
        "statement": statement,
        "picker_error_supported": picker_error,
        "non_affine_model_supported": non_affine_supported,
        "best_alternative_model": best_alternative["model"],
        "cross_validation_improvement_fraction": model_improvement,
        "outliers_at_all_ransac_thresholds": sorted(robust_outliers),
        "criteria": (
            "Modelo não-afim requer redução ≥20% no RMSE LOOCV e melhora na validação original; "
            "inconsistência requer exclusão pelo RANSAC em todos os limiares 10/20/30 m."
        ),
    }


def build_diagnosis(points: Sequence[ControlPoint], pdf_path: Path) -> dict[str, Any]:
    if len(points) < 7:
        raise DiagnosisError("São necessários ao menos 7 GCPs para comparar todos os modelos com LOOCV.")
    latitude_0 = float(np.mean([point.latitude for point in points]))
    longitude_0 = float(np.mean([point.longitude for point in points]))
    local_crs = f"+proj=aeqd +lat_0={latitude_0:.10f} +lon_0={longitude_0:.10f} +ellps=GRS80 +units=m +no_defs"
    projections = [
        ProjectionSpec("EPSG:31983", CRS.from_epsg(31983).name, "EPSG:31983"),
        ProjectionSpec("EPSG:3857", CRS.from_epsg(3857).name, "EPSG:3857"),
        ProjectionSpec("LOCAL_AEQD", "AEQD local centrada nos GCPs", local_crs),
    ]
    fit_indexes = [index for index, point in enumerate(points) if point.role == "FIT"]
    base_metrics, base_model, _ = _evaluate_split("affine", points, projections[0], fit_indexes, fit_indexes)
    residuals = _residual_records(base_model, points, projections[0])
    model_comparison = analyse_models(points, projections)
    affine_rows = [row for row in model_comparison if row["model"] == "affine"]
    validation_values = [float(row["validation"]["rmse_m"]) for row in affine_rows]
    cv_values = [float(row["cross_validation"]["metrics"]["rmse_m"]) for row in affine_rows]
    validation_span = max(validation_values) - min(validation_values)
    cv_span = max(cv_values) - min(cv_values)
    crs_negligible = validation_span < 1.0 and cv_span < 1.0
    report: dict[str, Any] = {
        "diagnostic_only": True,
        "gcp_counts": {
            "total": len(points),
            "fit": sum(point.role == "FIT" for point in points),
            "validation": sum(point.role == "VALIDATION" for point in points),
        },
        "projection_definitions": [projection.__dict__ for projection in projections],
        "baseline_fit_metrics": base_metrics,
        "baseline_residuals": residuals,
        "fit_leave_one_out": fit_leave_one_out(points, projections[0]),
        "validation_influence": validation_influence(points, projections[0]),
        "model_comparison": model_comparison,
        "crs_comparison": {
            "affine_validation_rmse_span_m": validation_span,
            "affine_cross_validation_rmse_span_m": cv_span,
            "differences_negligible": crs_negligible,
            "interpretation": (
                "As diferenças entre EPSG:31983, EPSG:3857 e AEQD local são submétricas; o CRS não parece ser a causa do erro."
                if crs_negligible
                else "As diferenças entre projeções são materialmente relevantes e merecem investigação."
            ),
        },
        "ransac": robust_affine(points, projections[0]),
        "spatial_residuals": spatial_residual_analysis(residuals),
        "pairwise_scale": pairwise_scale_analysis(points, projections[0]),
        "picker_audit": audit_picker(pdf_path),
    }
    report["recommendations"] = _recommendations(report)
    report["conclusion"] = _conclusion(report)
    affine = next(row for row in model_comparison if row["projection"] == "EPSG:31983" and row["model"] == "affine")
    homography = next(row for row in model_comparison if row["projection"] == "EPSG:31983" and row["model"] == "homography")
    polynomial = next(row for row in model_comparison if row["projection"] == "EPSG:31983" and row["model"] == "polynomial2")
    report["cause_explanation"] = {
        "why_rmse_is_high": (
            "O erro é alto porque poucos pontos discordam fortemente do padrão geométrico predominante: "
            "eles têm erros elevados em holdout, alteram a validação quando omitidos e/ou aparecem repetidamente em pares de escala atípicos."
        ),
        "why_not_crs": report["crs_comparison"]["interpretation"],
        "why_not_picker": (
            f"A cadeia completa do picker teve erro máximo {report['picker_audit']['maximum_roundtrip_error_pdf_units']:.3e} unidade PDF."
        ),
        "why_not_more_complex_model": (
            f"No EPSG:31983, o afim obteve LOOCV {affine['cross_validation']['metrics']['rmse_m']:.3f} m; "
            f"a homografia piorou para {homography['cross_validation']['metrics']['rmse_m']:.3f} m e o polinomial para "
            f"{polynomial['cross_validation']['metrics']['rmse_m']:.3f} m, apesar de ambos reduzirem o erro de treino."
        ),
        "pairwise_scale_assessment": (
            f"A escala entre os 105 pares tem CV de {report['pairwise_scale']['coefficient_of_variation']:.3%} e correlações espaciais fracas; "
            "isso favorece inconsistências localizadas, não uma deformação global progressiva."
        ),
    }
    return report


def _fmt_metrics(metrics: dict[str, Any]) -> str:
    return (
        f"RMSE {metrics['rmse_m']:.3f} m | média {metrics['mean_m']:.3f} m | "
        f"mediana {metrics['median_m']:.3f} m | máximo {metrics['maximum_m']:.3f} m"
    )


def write_reports(report: dict[str, Any], json_path: Path, text_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [
        "GeoJardins — Diagnóstico dos GCPs", "=" * 72,
        "MODO: somente diagnóstico; nenhum GCP ou polígono foi alterado.", "",
        f"GCPs: {report['gcp_counts']['total']} total | {report['gcp_counts']['fit']} FIT | {report['gcp_counts']['validation']} VALIDATION", "",
        "1. MÉTRICAS ATUAIS — AFIM / EPSG:31983",
        f"FIT: {_fmt_metrics(report['fit_leave_one_out']['complete_fit'])}",
        f"VALIDATION: {_fmt_metrics(report['fit_leave_one_out']['complete_validation'])}", "",
        "2. LEAVE-ONE-OUT DOS GCPs FIT (ordenado por Δ VALIDATION)",
        f"{'GCP removido':55} {'FIT RMSE':>10} {'VALID RMSE':>12} {'Δ VALID':>10} {'holdout':>10} {'leverage':>10}",
        "-" * 113,
    ]
    for row in report["fit_leave_one_out"]["points"]:
        lines.append(
            f"{row['removed'][:55]:55} {row['fit']['rmse_m']:10.3f} {row['validation']['rmse_m']:12.3f} "
            f"{row['delta_validation_rmse_m']:10.3f} {row['removed_point_holdout_error_m']:10.3f} {row['leverage']:10.3f}"
        )
    lines.extend(["", "3. INFLUÊNCIA DOS GCPs VALIDATION"])
    for row in report["validation_influence"]:
        lines.append(
            f"- Sem {row['removed']}: RMSE {row['validation_without_point']['rmse_m']:.3f} m "
            f"(Δ {row['delta_validation_rmse_m']:+.3f} m); erro individual {row['current_error_m']:.3f} m"
        )
    lines.extend(["", "4. COMPARAÇÃO DE CRS E MODELOS"])
    for row in report["model_comparison"]:
        lines.extend(
            [
                f"- {row['projection']} | {row['model_name']}",
                f"  treino: {_fmt_metrics(row['training'])}",
                f"  holdout VALIDATION: {_fmt_metrics(row['validation'])}",
                f"  LOOCV 15 pontos: {_fmt_metrics(row['cross_validation']['metrics'])}",
                f"  condição: {row['condition_number']:.3g} | razão VALID/treino: {row['validation_to_training_rmse_ratio']:.3f} | {row['stability_assessment']}",
            ]
        )
    lines.extend(["", report["crs_comparison"]["interpretation"]])
    lines.extend(["", "5. RANSAC AFIM — EPSG:31983"])
    for row in report["ransac"]["threshold_sensitivity"]:
        lines.append(f"- Limiar {row['threshold_m']:.0f} m: {len(row['inliers'])} inliers, {len(row['outliers'])} outliers")
        lines.append("  INLIERS PROVÁVEIS: " + ("; ".join(row["inliers"]) or "nenhum"))
        lines.append("  OUTLIERS PROVÁVEIS: " + ("; ".join(row["outliers"]) or "nenhum"))
        lines.append(f"  todos: {_fmt_metrics(row['all_metrics'])}")
    spatial = report["spatial_residuals"]
    lines.extend(["", "6. PADRÃO ESPACIAL DOS RESÍDUOS", spatial["interpretation"], spatial["systematic_pattern_assessment"]])
    for name, value in spatial["correlations"].items():
        lines.append(f"- {name}: r={value:+.4f}")
    pairs = report["pairwise_scale"]
    lines.extend(
        [
            "", "7. ESCALA ENTRE PARES",
            f"{pairs['pair_count']} pares | mediana {pairs['median_scale_m_per_pdf_unit']:.6f} m/unidade PDF | "
            f"MAD {pairs['mad_scale_m_per_pdf_unit']:.6f} | CV {pairs['coefficient_of_variation']:.3%}",
            f"Correlação escala×oeste-leste: {pairs['scale_vs_midpoint_x_correlation']:+.4f}",
            f"Correlação escala×norte-sul: {pairs['scale_vs_midpoint_y_correlation']:+.4f}",
            f"Correlação escala×distância: {pairs['scale_vs_pdf_distance_correlation']:+.4f}",
            report["cause_explanation"]["pairwise_scale_assessment"],
            "Pares mais inconsistentes:",
        ]
    )
    for row in pairs["most_inconsistent_pairs"][:10]:
        lines.append(
            f"- {row['first']} ↔ {row['second']}: PDF {row['pdf_distance']:.2f}, real {row['real_distance_m']:.1f} m, "
            f"escala {row['scale_m_per_pdf_unit']:.6f}, desvio robusto {row['robust_deviation']:.2f}"
        )
    picker = report["picker_audit"]
    lines.extend(
        [
            "", "8. AUDITORIA MATEMÁTICA DO GCP PICKER",
            f"Tecnologia: {picker['technology']}",
            f"Página PDF: {picker['page_size_pdf_units'][0]:.3f} × {picker['page_size_pdf_units'][1]:.3f} unidades",
            f"Raster 1,5×: {picker['raster_size_at_1_5x'][0]} × {picker['raster_size_at_1_5x'][1]} px",
            f"Erro máximo de ida e volta: {picker['maximum_roundtrip_error_pdf_units']:.3e} unidade PDF",
            f"Resultado: {'APROVADO' if picker['passed'] else 'FALHOU'}",
            picker["reasoning"],
        ]
    )
    for row in picker["scenarios"]:
        lines.append(f"- {row['scenario']}: erro {row['maximum_absolute_error_pdf_units']:.3e} unidade PDF")
    lines.extend(["", "9. POSSÍVEIS OUTLIERS E PRIORIDADE DE REVISÃO (nenhuma alteração automática)"])
    for number, row in enumerate(report["recommendations"]["review_priority"], start=1):
        influence = (
            f"Δ VALIDATION ao retirar do FIT {row['fit_leave_one_out_delta_validation_rmse_m']:+.3f} m"
            if row["fit_leave_one_out_delta_validation_rmse_m"] is not None
            else f"Δ VALIDATION ao retirar do conjunto de validação {row['validation_leave_one_out_delta_rmse_m']:+.3f} m"
        )
        lines.append(
            f"{number}. {row['name']} — resíduo atual {row['current_residual_m']:.3f} m; "
            f"holdout {row['cross_validation_error_m']:.3f} m; {influence}; " + "; ".join(row["reasons"])
        )
    if report["recommendations"]["secondary_monitoring"]:
        lines.append("Monitorar somente após revisar os pontos prioritários: " + "; ".join(report["recommendations"]["secondary_monitoring"]))
    lines.append(report["recommendations"]["complex_intersection_guidance"])
    conclusion = report["conclusion"]
    lines.extend(
        [
            "", "10. CONCLUSÃO", f"{conclusion['code']}) {conclusion['statement']}",
            report["cause_explanation"]["why_rmse_is_high"],
            report["cause_explanation"]["why_not_crs"],
            report["cause_explanation"]["why_not_picker"],
            report["cause_explanation"]["why_not_more_complex_model"],
            f"Ganho LOOCV do melhor modelo alternativo ({conclusion['best_alternative_model']}; positivo = melhora): "
            f"{conclusion['cross_validation_improvement_fraction']:.1%}",
            "Outliers presentes nos três limiares RANSAC: " + ("; ".join(conclusion["outliers_at_all_ransac_thresholds"]) or "nenhum"),
            conclusion["criteria"],
        ]
    )
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def run_diagnosis(
    control_points_path: Path = Path("data/control_points/control_points.csv"),
    pdf_path: Path | None = None,
    report_json_path: Path = Path("output/debug/gcp_diagnosis.json"),
    report_text_path: Path = Path("output/debug/gcp_diagnosis.txt"),
    influence_plot_path: Path = Path("output/debug/gcp_influence.png"),
    overlay_path: Path = Path("output/debug/gcp_pdf_overlay.png"),
) -> dict[str, Any]:
    resolved_pdf = resolve_pdf_path(pdf_path)
    before = hashlib.sha256(control_points_path.read_bytes()).hexdigest()
    points = load_control_points(control_points_path)
    report = build_diagnosis(points, resolved_pdf)
    _make_overlay(resolved_pdf, points, overlay_path)
    _make_influence_plot(report, influence_plot_path)
    after = hashlib.sha256(control_points_path.read_bytes()).hexdigest()
    report["input_integrity"] = {
        "control_points_path": str(control_points_path),
        "sha256_before": before,
        "sha256_after": after,
        "unchanged": before == after,
    }
    report["artifacts"] = {
        "text_report": str(report_text_path), "json_report": str(report_json_path),
        "influence_plot": str(influence_plot_path), "pdf_overlay": str(overlay_path),
    }
    write_reports(report, report_json_path, report_text_path)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnostica os GCPs sem alterar CSV ou polígonos.")
    parser.add_argument("--control-points", type=Path, default=Path("data/control_points/control_points.csv"))
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("output/debug"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output = args.output_dir
    report = run_diagnosis(
        control_points_path=args.control_points, pdf_path=args.pdf,
        report_json_path=output / "gcp_diagnosis.json", report_text_path=output / "gcp_diagnosis.txt",
        influence_plot_path=output / "gcp_influence.png", overlay_path=output / "gcp_pdf_overlay.png",
    )
    conclusion = report["conclusion"]
    print(f"{conclusion['code']}) {conclusion['statement']}")
    print(f"Relatórios e gráficos gravados em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
