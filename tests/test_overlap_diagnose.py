from __future__ import annotations

import unittest

import pymupdf
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from geojardins.overlap_diagnose import (
    AreaPaintEvidence,
    build_painter_partition,
    derive_paint_order,
    exact_even_odd_geometry,
    exclusivity_sample,
    find_overlaps,
    source_render_agreement,
    validate_partition,
)


def evidence(area: str, sequence: int, color: str) -> AreaPaintEvidence:
    return AreaPaintEvidence(
        area=area,
        fill_color=color,
        drawing_indexes=(sequence,),
        sequence_numbers=(sequence,),
        fill_opacities=(1.0,),
        even_odd_values=(True,),
        drawing_types=("fs",),
        levels=(2,),
        stroke_colors=("#6e6e6e",),
        stroke_widths_pdf_points=(0.48,),
    )


class OverlapDiagnosisTests(unittest.TestCase):
    def test_even_odd_reconstruction_matches_self_intersecting_pdf_fill(self) -> None:
        drawing = {
            "items": [
                ("l", (0, 0), (10, 10)),
                ("l", (10, 10), (0, 10)),
                ("l", (0, 10), (10, 0)),
                ("l", (10, 0), (0, 0)),
            ]
        }
        geometry = exact_even_odd_geometry(drawing)
        self.assertTrue(geometry.is_valid)
        self.assertAlmostEqual(geometry.area, 50.0)

    def test_paint_order_is_derived_from_source_sequence(self) -> None:
        records = {
            "Tomé_2": evidence("Tomé_2", 20, "#222222"),
            "Tomé_1": evidence("Tomé_1", 10, "#111111"),
            "Tomé_3": evidence("Tomé_3", 30, "#333333"),
        }
        self.assertEqual(
            derive_paint_order(records),
            ("Tomé_1", "Tomé_2", "Tomé_3"),
        )

    def test_partition_resolves_overlap_and_conserves_union(self) -> None:
        left = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        right = Polygon([(5, 0), (15, 0), (15, 10), (5, 10)])
        original = {"A": left, "B": right}
        partition = build_painter_partition(original, ("A", "B"))

        self.assertAlmostEqual(partition["A"].area, 50.0)
        self.assertAlmostEqual(partition["B"].area, 100.0)
        self.assertEqual(find_overlaps(partition), {})
        validation = validate_partition(original, partition)
        self.assertAlmostEqual(validation["symmetric_difference_m2"], 0.0)
        self.assertEqual(validation["significant_overlap_count"], 0)
        self.assertAlmostEqual(validation["new_gap_area_m2"], 0.0)

    def test_partition_preserves_multipolygon_and_real_hole(self) -> None:
        shell_with_hole = Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            holes=[[(3, 3), (7, 3), (7, 7), (3, 7)]],
        )
        islands = MultiPolygon(
            [
                Polygon([(12, 0), (14, 0), (14, 2), (12, 2)]),
                Polygon([(12, 4), (14, 4), (14, 6), (12, 6)]),
            ]
        )
        original = {"A": shell_with_hole, "B": islands}
        partition = build_painter_partition(original, ("A", "B"))
        validation = validate_partition(original, partition)

        self.assertAlmostEqual(validation["symmetric_difference_m2"], 0.0)
        self.assertEqual(validation["original_hole_count"], 1)
        self.assertEqual(validation["canonical_hole_count"], 1)
        self.assertEqual(len(partition["B"].geoms), 2)

    def test_exclusivity_grid_never_matches_two_interiors(self) -> None:
        original = {
            "A": Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            "B": Polygon([(5, 0), (15, 0), (15, 10), (5, 10)]),
        }
        overlaps = find_overlaps(original)
        partition = build_painter_partition(original, ("A", "B"))
        result = exclusivity_sample(
            partition,
            overlaps,
            grid_axis=40,
            random_points=500,
            seed=7,
        )
        self.assertEqual(result["two_or_more_areas"], 0)
        self.assertGreater(result["one_area"], 0)

    def test_source_render_comparison_matches_painters_algorithm(self) -> None:
        document = pymupdf.open()
        page = document.new_page(width=100, height=60)
        page.draw_rect(
            pymupdf.Rect(10, 10, 60, 50),
            color=None,
            fill=(1.0, 1.0, 0.0),
            overlay=True,
        )
        page.draw_rect(
            pymupdf.Rect(40, 10, 90, 50),
            color=None,
            fill=(0.0, 0.0, 1.0),
            overlay=True,
        )
        original = {
            "A": Polygon([(10, 10), (60, 10), (60, 50), (10, 50)]),
            "B": Polygon([(40, 10), (90, 10), (90, 50), (40, 50)]),
        }
        partition = build_painter_partition(original, ("A", "B"))
        result = source_render_agreement(
            page,
            partition,
            {"A": "#ffff00", "B": "#0000ff"},
            grid_columns=100,
            render_scale=2.0,
        )
        self.assertGreater(result["valid_palette_points_compared"], 1_000)
        self.assertGreater(result["agreement_percent"], 99.0)
        document.close()


if __name__ == "__main__":
    unittest.main()
