from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Polygon

from geojardins.control_points import ControlPoint, load_control_points, save_control_points
from geojardins.georef import (
    GeoReferenceError,
    build_boundary_collection,
    calculate_residuals,
    compare_candidate_crs,
    fit_affine_transform,
    georeference_collection,
    infer_expected_adjacencies,
    project_control_points,
    run_georeferencing,
    validate_area_geometries,
    validate_operational_counts,
)


def synthetic_gcps(validation_offset_m: float = 0.0) -> list[ControlPoint]:
    inverse = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)
    pdf_positions = [
        (0.0, 0.0),
        (2000.0, 0.0),
        (0.0, 2500.0),
        (2000.0, 2500.0),
        (500.0, 1800.0),
        (1700.0, 700.0),
    ]
    points = []
    for index, (pdf_x, pdf_y) in enumerate(pdf_positions):
        projected_x = 330000.0 + 2.2 * pdf_x + 0.15 * pdf_y
        projected_y = 7390000.0 - 0.10 * pdf_x - 2.0 * pdf_y
        role = "FIT" if index < 4 else "VALIDATION"
        if role == "VALIDATION":
            projected_x += validation_offset_m
        longitude, latitude = inverse.transform(projected_x, projected_y)
        points.append(
            ControlPoint(
                name=f"gcp_{index + 1}",
                pdf_x=pdf_x,
                pdf_y=pdf_y,
                latitude=latitude,
                longitude=longitude,
                role=role,
            )
        )
    return points


def six_area_collection() -> dict:
    features = []
    for index in range(6):
        polygon = Polygon([(index, 0), (index + 1, 0), (index + 1, 1), (index, 1), (index, 0)])
        features.append(
            {
                "type": "Feature",
                "id": f"tome_{index + 1}",
                "properties": {"area": f"Tomé_{index + 1}", "coordinate_system": "pdf"},
                "geometry": polygon.__geo_interface__,
            }
        )
    return {
        "type": "FeatureCollection",
        "metadata": {"coordinate_system": "pdf"},
        "features": features,
    }


class GeoReferenceTests(unittest.TestCase):
    def test_empty_control_point_file_is_loaded_but_rejected_operationally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "control_points.csv"
            path.write_text(
                "name,pdf_x,pdf_y,latitude,longitude,role,notes\n",
                encoding="utf-8",
            )
            points = load_control_points(path)
            self.assertEqual(points, [])
            with self.assertRaisesRegex(GeoReferenceError, "Pontos de controle insuficientes"):
                validate_operational_counts(points)

    def test_insufficient_fit_and_validation_are_rejected(self) -> None:
        points = synthetic_gcps()[:5]
        with self.assertRaisesRegex(GeoReferenceError, "mínimo operacional"):
            validate_operational_counts(points)

    def test_crs_conversion_round_trip(self) -> None:
        point = synthetic_gcps()[0]
        projected = project_control_points([point], "EPSG:31983")[0]
        inverse = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)
        longitude, latitude = inverse.transform(projected.x, projected.y)
        self.assertAlmostEqual(longitude, point.longitude, places=8)
        self.assertAlmostEqual(latitude, point.latitude, places=8)

    def test_affine_fit_and_validation_residuals_are_separate(self) -> None:
        points = synthetic_gcps(validation_offset_m=20.0)
        transform = fit_affine_transform(points, "EPSG:31983")
        residuals = calculate_residuals(points, transform)
        fit_errors = [record.error_m for record in residuals if record.role == "FIT"]
        validation_errors = [record.error_m for record in residuals if record.role == "VALIDATION"]
        self.assertLess(max(fit_errors), 0.001)
        self.assertAlmostEqual(min(validation_errors), 20.0, delta=0.05)

    def test_empirical_crs_comparison_prefers_source_projection(self) -> None:
        evaluations, selected = compare_candidate_crs(synthetic_gcps())
        self.assertEqual(len(evaluations), 2)
        self.assertEqual(selected.crs, "EPSG:31983")
        self.assertLess(selected.validation_metrics.rmse_m, 0.001)

    def test_georeferenced_collection_is_valid_epsg4326(self) -> None:
        transform = fit_affine_transform(synthetic_gcps(), "EPSG:31983")
        transformed = georeference_collection(six_area_collection(), transform)
        self.assertEqual(transformed["metadata"]["coordinate_system"], "EPSG:4326")
        self.assertEqual(len(transformed["features"]), 6)
        self.assertTrue(all(Polygon(feature["geometry"]["coordinates"][0]).is_valid for feature in transformed["features"]))

    def test_boundary_generation_and_geometry_validation(self) -> None:
        collection = six_area_collection()
        geometries = {
            feature["properties"]["area"]: Polygon(feature["geometry"]["coordinates"][0])
            for feature in collection["features"]
        }
        validation = validate_area_geometries(geometries)
        self.assertTrue(validation["valid_for_output"])
        self.assertEqual(validation["overlaps"], [])

        collection["metadata"].update(
            {"coordinate_system": "EPSG:4326", "georeferenced_at": "test", "rmse_m": 1.0}
        )
        boundary = build_boundary_collection(collection)
        self.assertEqual(len(boundary["features"]), 1)
        self.assertAlmostEqual(shape_area(boundary["features"][0]["geometry"]), 6.0)

        expected_adjacencies = infer_expected_adjacencies(geometries, tolerance_pdf_points=0.0)
        validation_with_adjacencies = validate_area_geometries(geometries, expected_adjacencies)
        self.assertEqual(len(expected_adjacencies), 5)
        self.assertEqual(validation_with_adjacencies["gaps_between_expected_adjacencies"], [])

    def test_end_to_end_synthetic_writes_reports_and_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.geojson"
            gcps = root / "control_points.csv"
            areas = root / "areas.geojson"
            boundary = root / "boundary.geojson"
            report_json = root / "report.json"
            report_txt = root / "report.txt"
            residual_plot = root / "residuals.png"
            source.write_text(json.dumps(six_area_collection()), encoding="utf-8")
            save_control_points(gcps, synthetic_gcps())

            report = run_georeferencing(
                source_path=source,
                control_points_path=gcps,
                output_path=areas,
                boundary_path=boundary,
                report_json_path=report_json,
                report_text_path=report_txt,
                residual_plot_path=residual_plot,
            )

            self.assertEqual(report["status"], "ACCEPTED")
            self.assertTrue(all(path.is_file() for path in (areas, boundary, report_json, report_txt, residual_plot)))


def shape_area(geometry: dict) -> float:
    from shapely.geometry import shape

    return shape(geometry).area


if __name__ == "__main__":
    unittest.main()
