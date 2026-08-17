from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pymupdf
from pyproj import Transformer

from geojardins.control_points import ControlPoint
from geojardins.gcp_diagnose import (
    ProjectionSpec,
    audit_picker,
    cross_validate,
    fit_model,
    robust_affine,
)


class GcpDiagnoseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projection = ProjectionSpec("EPSG:31983", "SIRGAS 2000 / UTM 23S", "EPSG:31983")

    @staticmethod
    def _synthetic_points(outlier_index: int | None = None) -> list[ControlPoint]:
        source = np.asarray(
            [
                [100.0, 100.0], [900.0, 100.0], [100.0, 800.0], [900.0, 800.0],
                [500.0, 450.0], [250.0, 600.0], [720.0, 300.0], [420.0, 720.0],
            ]
        )
        target_x = 330000.0 + 5.2 * source[:, 0] + 0.18 * source[:, 1]
        target_y = 7390000.0 - 0.12 * source[:, 0] - 5.1 * source[:, 1]
        if outlier_index is not None:
            target_x[outlier_index] += 120.0
            target_y[outlier_index] -= 80.0
        inverse = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)
        longitude, latitude = inverse.transform(target_x, target_y)
        return [
            ControlPoint(
                name=f"Ponto {index + 1}", pdf_x=float(pdf_x), pdf_y=float(pdf_y),
                latitude=float(lat), longitude=float(lon),
                role="FIT" if index < 6 else "VALIDATION",
            )
            for index, ((pdf_x, pdf_y), lon, lat) in enumerate(
                zip(source, longitude, latitude, strict=True)
            )
        ]

    def test_models_reproduce_an_exact_affine_mapping(self) -> None:
        points = self._synthetic_points()
        source = np.asarray([(point.pdf_x, point.pdf_y) for point in points])
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
        target = np.asarray([transformer.transform(point.longitude, point.latitude) for point in points])

        for kind in ("affine", "homography", "polynomial2"):
            with self.subTest(kind=kind):
                model = fit_model(kind, source, target)
                self.assertLess(float(np.max(np.abs(model.predict(source) - target))), 1e-5)

    def test_cross_validation_is_near_zero_for_exact_affine_data(self) -> None:
        result = cross_validate("affine", self._synthetic_points(), self.projection)
        self.assertEqual(result["failures"], 0)
        self.assertLess(result["metrics"]["maximum_m"], 1e-4)

    def test_ransac_finds_an_injected_outlier_without_modifying_points(self) -> None:
        points = self._synthetic_points(outlier_index=4)
        result = robust_affine(points, self.projection, thresholds=(5.0,))
        consensus = result["threshold_sensitivity"][0]
        self.assertEqual(consensus["outliers"], ["Ponto 5"])
        self.assertEqual(len(points), 8)

    def test_picker_audit_covers_zoom_pan_and_inverted_y(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.pdf"
            document = pymupdf.open()
            document.new_page(width=1200, height=1800)
            document.save(path)
            document.close()

            result = audit_picker(path)

        self.assertTrue(result["passed"])
        self.assertTrue(result["y_axis_inverted"])
        self.assertEqual(
            [row["scenario"] for row in result["scenarios"]],
            ["100%", "300%", "800%", "300% + pan", "zoom + pan + zoom"],
        )
        self.assertLess(result["maximum_roundtrip_error_pdf_units"], 1e-9)


if __name__ == "__main__":
    unittest.main()
