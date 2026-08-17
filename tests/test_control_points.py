from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geojardins.control_points import (
    ControlPoint,
    ControlPointError,
    ControlPointStore,
    assess_spatial_distribution,
)


class ControlPointStoreTests(unittest.TestCase):
    def test_add_update_remove_without_silent_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "points.csv"
            path.write_text(
                "name,pdf_x,pdf_y,latitude,longitude,role,notes\n",
                encoding="utf-8",
            )
            store = ControlPointStore(path)
            point = ControlPoint("A", 1, 2, -23.5, -46.6)
            store.add(point)
            with self.assertRaisesRegex(ControlPointError, "Já existe"):
                store.add(point)
            store.update(0, ControlPoint("A", 3, 4, -23.51, -46.61, "VALIDATION"))
            self.assertEqual(store.points[0].role, "VALIDATION")
            removed = store.remove(0)
            self.assertEqual(removed.name, "A")
            self.assertEqual(store.points, [])

    def test_concentration_warning(self) -> None:
        points = [
            ControlPoint(f"P{index}", 10 + index, 10 + index, -23.5, -46.6 - index * 0.001)
            for index in range(6)
        ]
        result = assess_spatial_distribution(points, (0, 0, 100, 100))
        self.assertTrue(any("concentrados" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
