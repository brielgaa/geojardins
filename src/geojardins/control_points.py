"""Modelo, persistência e diagnóstico espacial de Ground Control Points."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

GcpRole = Literal["FIT", "VALIDATION"]
CSV_COLUMNS = ("name", "pdf_x", "pdf_y", "latitude", "longitude", "role", "notes")
LEGACY_COLUMNS = ("name", "pdf_x", "pdf_y", "latitude", "longitude")


class ControlPointError(ValueError):
    """Um GCP ou arquivo de GCPs é inválido."""


@dataclass(frozen=True)
class ControlPoint:
    """Correspondência entre uma posição do PDF e uma coordenada geográfica real."""

    name: str
    pdf_x: float
    pdf_y: float
    latitude: float
    longitude: float
    role: GcpRole = "FIT"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ControlPointError("O nome do GCP é obrigatório.")
        if self.role not in {"FIT", "VALIDATION"}:
            raise ControlPointError("O papel do GCP deve ser FIT ou VALIDATION.")
        values = (self.pdf_x, self.pdf_y, self.latitude, self.longitude)
        if not all(math.isfinite(value) for value in values):
            raise ControlPointError("As coordenadas do GCP devem ser números finitos.")
        if not -90 <= self.latitude <= 90:
            raise ControlPointError("Latitude fora do intervalo [-90, 90].")
        if not -180 <= self.longitude <= 180:
            raise ControlPointError("Longitude fora do intervalo [-180, 180].")


def _format_number(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def _point_from_row(row: dict[str, str | None], line_number: int) -> ControlPoint:
    try:
        return ControlPoint(
            name=(row.get("name") or "").strip(),
            pdf_x=float(row.get("pdf_x") or ""),
            pdf_y=float(row.get("pdf_y") or ""),
            latitude=float(row.get("latitude") or ""),
            longitude=float(row.get("longitude") or ""),
            role=((row.get("role") or "FIT").strip().upper()),  # type: ignore[arg-type]
            notes=(row.get("notes") or "").strip(),
        )
    except (TypeError, ValueError, ControlPointError) as exc:
        raise ControlPointError(f"Linha {line_number} do CSV é inválida: {exc}") from exc


def load_control_points(csv_path: Path) -> list[ControlPoint]:
    """Lê o CSV atual e também o cabeçalho legado da Fase 1."""

    if not csv_path.is_file():
        raise ControlPointError(f"Arquivo de pontos de controle não encontrado: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        if fieldnames not in {CSV_COLUMNS, LEGACY_COLUMNS}:
            raise ControlPointError(
                "Cabeçalho inválido. Esperado: " + ",".join(CSV_COLUMNS)
            )
        return [_point_from_row(row, line_number) for line_number, row in enumerate(reader, start=2)]


def save_control_points(csv_path: Path, points: Sequence[ControlPoint]) -> None:
    """Grava o CSV de modo atômico para evitar um arquivo parcialmente escrito."""

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for point in points:
            writer.writerow(
                {
                    "name": point.name,
                    "pdf_x": _format_number(point.pdf_x),
                    "pdf_y": _format_number(point.pdf_y),
                    "latitude": _format_number(point.latitude),
                    "longitude": _format_number(point.longitude),
                    "role": point.role,
                    "notes": point.notes,
                }
            )
    temporary_path.replace(csv_path)


class ControlPointStore:
    """Coleção editável que nunca substitui um GCP homônimo silenciosamente."""

    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path
        self.points = load_control_points(csv_path)

    def _assert_unique_name(self, point: ControlPoint, ignored_index: int | None = None) -> None:
        normalized = point.name.casefold().strip()
        for index, existing in enumerate(self.points):
            if index != ignored_index and existing.name.casefold().strip() == normalized:
                raise ControlPointError(
                    f"Já existe um GCP chamado '{existing.name}'. Selecione-o para editar."
                )

    def add(self, point: ControlPoint) -> None:
        self._assert_unique_name(point)
        self.points.append(point)
        save_control_points(self.csv_path, self.points)

    def update(self, index: int, point: ControlPoint) -> None:
        if not 0 <= index < len(self.points):
            raise ControlPointError("GCP selecionado não existe mais.")
        self._assert_unique_name(point, ignored_index=index)
        self.points[index] = point
        save_control_points(self.csv_path, self.points)

    def remove(self, index: int) -> ControlPoint:
        if not 0 <= index < len(self.points):
            raise ControlPointError("GCP selecionado não existe mais.")
        removed = self.points.pop(index)
        save_control_points(self.csv_path, self.points)
        return removed


def assess_spatial_distribution(
    points: Sequence[ControlPoint],
    map_bounds: tuple[float, float, float, float],
    minimum_count: int = 6,
    recommended_minimum: int = 12,
    recommended_maximum: int = 20,
) -> dict[str, object]:
    """Avalia concentração por extensão e quadrantes; emite warnings, não exclusões."""

    min_x, min_y, max_x, max_y = map_bounds
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        raise ControlPointError("Os limites do mapa são inválidos.")

    warnings: list[str] = []
    if not points:
        return {
            "point_count": 0,
            "x_span_ratio": 0.0,
            "y_span_ratio": 0.0,
            "occupied_quadrants": 0,
            "warnings": ["Nenhum GCP cadastrado."],
        }

    xs = [point.pdf_x for point in points]
    ys = [point.pdf_y for point in points]
    x_span_ratio = (max(xs) - min(xs)) / width
    y_span_ratio = (max(ys) - min(ys)) / height
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    quadrants = {
        (0 if point.pdf_x < center_x else 1, 0 if point.pdf_y < center_y else 1)
        for point in points
    }

    if len(points) < minimum_count:
        warnings.append(f"Mínimo operacional: cadastre pelo menos {minimum_count} GCPs.")
    elif len(points) < recommended_minimum:
        warnings.append(
            f"Há pontos suficientes para operação, mas o recomendado é "
            f"{recommended_minimum} a {recommended_maximum} GCPs."
        )
    elif len(points) > recommended_maximum:
        warnings.append(
            f"Há mais de {recommended_maximum} GCPs; confirme se todos foram revisados e são necessários."
        )

    # 60% e três quadrantes são heurísticas transparentes de cobertura, não critérios de precisão.
    if len(points) >= minimum_count and (x_span_ratio < 0.60 or y_span_ratio < 0.60):
        warnings.append(
            "GCPs espacialmente concentrados: a extensão cadastrada cobre menos de 60% "
            "do mapa em pelo menos um eixo."
        )
    if len(points) >= minimum_count and len(quadrants) < 3:
        warnings.append("GCPs concentrados em menos de três quadrantes do mapa.")

    return {
        "point_count": len(points),
        "x_span_ratio": x_span_ratio,
        "y_span_ratio": y_span_ratio,
        "occupied_quadrants": len(quadrants),
        "warnings": warnings,
    }
