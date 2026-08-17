from __future__ import annotations

import unittest

from geojardins.gcp_picker import PdfViewport, display_to_pdf, screen_to_pdf


class GcpPickerViewportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.viewport = PdfViewport.from_extent((0.0, 2400.0, 3400.0, 0.0))

    def test_zoom_is_anchored_at_cursor_position(self) -> None:
        xlim, ylim = self.viewport.fit_limits()
        anchor_x, anchor_y = 1800.0, 2500.0

        zoomed_x, zoomed_y = self.viewport.zoom_limits(xlim, ylim, anchor_x, anchor_y, 1.25)

        original_x_fraction = (anchor_x - xlim[0]) / (xlim[1] - xlim[0])
        original_y_fraction = (anchor_y - ylim[0]) / (ylim[1] - ylim[0])
        zoomed_x_fraction = (anchor_x - zoomed_x[0]) / (zoomed_x[1] - zoomed_x[0])
        zoomed_y_fraction = (anchor_y - zoomed_y[0]) / (zoomed_y[1] - zoomed_y[0])
        self.assertAlmostEqual(original_x_fraction, zoomed_x_fraction)
        self.assertAlmostEqual(original_y_fraction, zoomed_y_fraction)
        self.assertEqual(self.viewport.zoom_percentage(zoomed_x), 125)

    def test_zoom_limits_and_pan_stay_inside_pdf_extent(self) -> None:
        xlim, ylim = self.viewport.fit_limits()
        maximum_x, maximum_y = self.viewport.zoom_limits(xlim, ylim, 1200.0, 1700.0, 100.0)
        self.assertEqual(self.viewport.zoom_percentage(maximum_x), 800)

        panned_x, panned_y = self.viewport.pan_limits(maximum_x, maximum_y, -10000.0, 10000.0)
        self.assertGreaterEqual(min(panned_x), 0.0)
        self.assertLessEqual(max(panned_x), 2400.0)
        self.assertGreaterEqual(min(panned_y), 0.0)
        self.assertLessEqual(max(panned_y), 3400.0)

        fitted_x, fitted_y = self.viewport.zoom_limits(panned_x, panned_y, 1200.0, 1700.0, 0.001)
        self.assertEqual(fitted_x, self.viewport.base_xlim)
        self.assertEqual(fitted_y, self.viewport.base_ylim)

    def test_display_and_tk_screen_convert_to_original_pdf_after_zoom_pan(self) -> None:
        xlim, ylim = self.viewport.fit_limits()
        xlim, ylim = self.viewport.zoom_limits(xlim, ylim, 1700.0, 2300.0, 1.25)
        xlim, ylim = self.viewport.pan_limits(xlim, ylim, -120.0, 85.0)
        xlim, ylim = self.viewport.zoom_limits(xlim, ylim, 1500.0, 2200.0, 1.25)
        xlim, ylim = self.viewport.pan_limits(xlim, ylim, 60.0, -40.0)

        axes_bounds = (100.0, 80.0, 800.0, 600.0)
        expected_pdf = (1450.0, 2050.0)
        display_x = axes_bounds[0] + (expected_pdf[0] - xlim[0]) / (xlim[1] - xlim[0]) * axes_bounds[2]
        display_y = axes_bounds[1] + (expected_pdf[1] - ylim[0]) / (ylim[1] - ylim[0]) * axes_bounds[3]

        from_display = display_to_pdf(display_x, display_y, axes_bounds, xlim, ylim)
        from_tk_screen = screen_to_pdf(
            display_x,
            900.0 - display_y,
            900.0,
            axes_bounds,
            xlim,
            ylim,
        )

        self.assertIsNotNone(from_display)
        self.assertIsNotNone(from_tk_screen)
        self.assertAlmostEqual(from_display[0], expected_pdf[0])  # type: ignore[index]
        self.assertAlmostEqual(from_display[1], expected_pdf[1])  # type: ignore[index]
        self.assertAlmostEqual(from_tk_screen[0], expected_pdf[0])  # type: ignore[index]
        self.assertAlmostEqual(from_tk_screen[1], expected_pdf[1])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
