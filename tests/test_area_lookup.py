from __future__ import annotations

import unittest

from shapely.geometry import Polygon

from geojardins.area_lookup import distance_to_nearest_boundary, lookup_coordinate
from geojardins.georef import build_boundary_collection


def lookup_layers() -> tuple[dict, dict]:
    areas = {
        "type": "FeatureCollection",
        "metadata": {"coordinate_system": "EPSG:4326"},
        "features": [],
    }
    for index in range(6):
        polygon = Polygon(
            [
                (-46.70 + index * 0.01, -23.60),
                (-46.69 + index * 0.01, -23.60),
                (-46.69 + index * 0.01, -23.59),
                (-46.70 + index * 0.01, -23.59),
                (-46.70 + index * 0.01, -23.60),
            ]
        )
        areas["features"].append(
            {
                "type": "Feature",
                "properties": {"area": f"Tomé_{index + 1}"},
                "geometry": polygon.__geo_interface__,
            }
        )
    return areas, build_boundary_collection(areas)


class AreaLookupTests(unittest.TestCase):
    def test_point_inside_area(self) -> None:
        areas, boundary = lookup_layers()
        result = lookup_coordinate(-23.595, -46.695, areas, boundary)
        self.assertTrue(result.inside_ugr)
        self.assertEqual(result.area, "Tomé_1")

    def test_point_outside_ugr(self) -> None:
        areas, boundary = lookup_layers()
        result = lookup_coordinate(-23.50, -46.50, areas, boundary)
        self.assertFalse(result.inside_ugr)
        self.assertEqual(result.areas, ())

    def test_point_exactly_on_shared_boundary_is_explicitly_ambiguous(self) -> None:
        areas, boundary = lookup_layers()
        result = lookup_coordinate(-23.595, -46.69, areas, boundary)

        self.assertTrue(result.inside_ugr)
        self.assertEqual(result.areas, ("Tomé_1", "Tomé_2"))
        self.assertTrue(result.geometric_ambiguity)
        self.assertAlmostEqual(result.distance_to_boundary_m or 0.0, 0.0, places=5)

    def test_distance_to_boundary_is_metric_and_positive_inside(self) -> None:
        areas, _ = lookup_layers()
        distance = distance_to_nearest_boundary(-23.595, -46.695, areas)

        self.assertGreater(distance, 400.0)
        self.assertLess(distance, 600.0)


if __name__ == "__main__":
    unittest.main()
