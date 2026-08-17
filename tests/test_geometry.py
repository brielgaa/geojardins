from __future__ import annotations

import unittest

from geojardins.geometry import geometry_from_drawing, rgb_to_hex


class GeometryTests(unittest.TestCase):
    def test_closed_line_path_becomes_polygon(self) -> None:
        drawing = {
            "items": [
                ("l", (0, 0), (10, 0)),
                ("l", (10, 0), (10, 5)),
                ("l", (10, 5), (0, 5)),
                ("l", (0, 5), (0, 0)),
            ]
        }

        geometry = geometry_from_drawing(drawing)

        self.assertEqual(geometry.geom_type, "Polygon")
        self.assertAlmostEqual(geometry.area, 50.0)

    def test_rgb_conversion_matches_pdf_palette(self) -> None:
        self.assertEqual(rgb_to_hex((1.0, 0.7451, 0.7451)), "#ffbebe")

    def test_self_intersection_is_repaired_without_dropping_area(self) -> None:
        drawing = {
            "items": [
                ("l", (0, 0), (10, 10)),
                ("l", (10, 10), (0, 10)),
                ("l", (0, 10), (10, 0)),
                ("l", (10, 0), (0, 0)),
            ]
        }

        geometry = geometry_from_drawing(drawing)

        self.assertTrue(geometry.is_valid)
        self.assertAlmostEqual(geometry.area, 50.0)


if __name__ == "__main__":
    unittest.main()
