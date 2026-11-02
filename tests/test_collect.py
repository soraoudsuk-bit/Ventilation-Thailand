import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import collect


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "images" / "2026-10-31"


class HistoricalChartTests(unittest.TestCase):
    def test_archived_october_31_labels_match_chart_geometry(self):
        expected = {
            "310201": 1375,  # Phayao
            "352201": 1375,  # Nong Khai
            "354201": 1375,  # Udon Thani
            "303201": 1250,  # Chiang Rai
        }
        for station, value in expected.items():
            with self.subTest(station=station):
                image = Image.open(ARCHIVE / f"daily_VR_{station}.png")
                self.assertEqual(
                    collect.read_chart(image, date(2026, 10, 31))[0], value
                )

    def test_archived_november_1_rows_recover_after_wider_date_crop(self):
        expected = {
            "383201": 750,   # Mukdahan
            "406201": 750,   # Yasothon
            "474000": 1250,  # Samut Sakhon
            "391201": 750,   # Amnat Charoen
        }
        archive = ROOT / "data" / "images" / "2026-11-01"
        for station, value in expected.items():
            with self.subTest(station=station):
                image = Image.open(archive / f"daily_VR_{station}.png")
                self.assertEqual(
                    collect.read_chart(image, date(2026, 11, 1))[0], value
                )

    def test_one_tick_ocr_error_is_rejected_by_bar_geometry(self):
        image = Image.open(ARCHIVE / "daily_VR_310201.png")
        # Header, date label, two matching but wrong number OCR passes, level.
        outputs = iter(
            ["2026-10-30 00Z", "31-Oct", "(1500)", "(1500)", "Poor"]
        )
        with patch("collect.ocr", side_effect=lambda *args, **kwargs: next(outputs)):
            with self.assertRaisesRegex(ValueError, "number_ocr_uncertain"):
                collect.read_chart(image, date(2026, 10, 31))

    def test_opening_parenthesis_substitution_requires_geometry(self):
        self.assertEqual(
            collect.validated_number("14875)", "14875)", 4875), 4875
        )
        with self.assertRaisesRegex(ValueError, "number_ocr_uncertain"):
            collect.validated_number("14875)", "14875)", 1386)


if __name__ == "__main__":
    unittest.main()
