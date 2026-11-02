import unittest
from datetime import date
from pathlib import Path
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

    def test_archived_november_2_header_suffix_ocr_as_two(self):
        image = Image.open(
            ROOT / "data" / "images" / "2026-11-02" / "daily_VR_566202.png"
        )
        value, _, init = collect.read_chart(image, date(2026, 11, 2))
        self.assertEqual(value, 250)
        self.assertEqual(init, "2026-11-01 00Z")

    def test_all_archived_november_2_station_images_are_readable(self):
        archive = ROOT / "data" / "images" / "2026-11-02"
        images = sorted(archive.glob("daily_VR_*.png"))
        self.assertEqual(len(images), 77)
        for path in images:
            with self.subTest(station=path.stem):
                image = Image.open(path)
                self.assertEqual(
                    collect.read_chart(image, date(2026, 11, 2))[2],
                    "2026-11-01 00Z",
                )

    def test_one_tick_ocr_error_is_rejected_by_bar_geometry(self):
        with self.assertRaisesRegex(ValueError, "number_ocr_uncertain"):
            collect.validated_number("(1500)", "(1500)", 1386)

    def test_opening_parenthesis_substitution_requires_geometry(self):
        self.assertEqual(
            collect.validated_number("14875)", "14875)", 4875), 4875
        )
        with self.assertRaisesRegex(ValueError, "number_ocr_uncertain"):
            collect.validated_number("14875)", "14875)", 1386)

    def test_ambiguous_3_or_8_uses_bar_height(self):
        self.assertEqual(
            collect.validated_number("(8125)", "(8125)", 3134), 3125
        )
        self.assertEqual(
            collect.validated_number("(8125)", "(8125)", 8125), 8125
        )


if __name__ == "__main__":
    unittest.main()
