import unittest

from inventory_system.barcode_serials import (
    format_unit_numbers,
    is_unit_barcode,
    make_unit_barcode,
    normalize_scanned_barcode,
    split_unit_barcode,
)


class BarcodeSerialsTest(unittest.TestCase):
    def test_make_unit_barcode(self):
        self.assertEqual(
            make_unit_barcode("1001-2606-0007", 1),
            "1001-2606-0007-001",
        )

    def test_split_unit_barcode(self):
        self.assertEqual(
            split_unit_barcode("1001-2606-0007-025"),
            ("1001-2606-0007", 25),
        )

    def test_base_item_code_is_not_mistaken_for_unit_barcode(self):
        self.assertEqual(
            split_unit_barcode("1001-2606-0007"),
            ("1001-2606-0007", None),
        )
        self.assertFalse(is_unit_barcode("1001-2606-0007"))

    def test_legacy_project_prefix_is_removed(self):
        self.assertEqual(
            normalize_scanned_barcode("PROJECT_1001-2606-0007-003\r\n"),
            "1001-2606-0007-003",
        )

    def test_unit_number_range(self):
        with self.assertRaises(ValueError):
            make_unit_barcode("1001-2606-0007", 0)

        with self.assertRaises(ValueError):
            make_unit_barcode("1001-2606-0007", 1000)

    def test_format_unit_numbers(self):
        self.assertEqual(
            format_unit_numbers({5, 1, 3}),
            "001, 003, 005",
        )


if __name__ == "__main__":
    unittest.main()
