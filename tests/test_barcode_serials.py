import unittest

from inventory_system.barcode_serials import (
    format_unit_numbers,
    is_unit_barcode,
    item_code_candidates,
    make_unit_barcode,
    normalize_scanned_barcode,
    resolve_scanned_item_code,
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

    def test_split_quantity_unit_qr(self):
        self.assertEqual(
            split_unit_barcode("1001-2606-0007|1/2\r\n"),
            ("1001-2606-0007", 1),
        )
        self.assertEqual(
            split_unit_barcode("1001-2606-0007|2/2"),
            ("1001-2606-0007", 2),
        )
        self.assertTrue(is_unit_barcode("1001-2606-0007|1/2"))

    def test_invalid_quantity_unit_qr_is_not_split(self):
        self.assertEqual(
            split_unit_barcode("1001-2606-0007|3/2"),
            ("1001-2606-0007|3/2", None),
        )

    def test_base_item_code_is_not_mistaken_for_unit_barcode(self):
        self.assertEqual(
            split_unit_barcode("1001-2606-0007"),
            ("1001-2606-0007", None),
        )
        self.assertFalse(is_unit_barcode("1001-2606-0007"))

    def test_legacy_project_prefix_is_used_as_fallback(self):
        self.assertEqual(
            normalize_scanned_barcode("PROJECT_1001-2606-0007-003\r\n"),
            "PROJECT_1001-2606-0007-003",
        )
        self.assertEqual(
            item_code_candidates("PROJECT_1001-2606-0007-003\r\n"),
            (["PROJECT_1001-2606-0007", "1001-2606-0007"], 3),
        )
        self.assertEqual(
            resolve_scanned_item_code(
                "PROJECT_1001-2606-0007-003\r\n",
                ["1001-2606-0007"],
            ),
            ("1001-2606-0007", 3),
        )

    def test_item_code_underscore_is_preserved_before_legacy_fallback(self):
        self.assertEqual(
            resolve_scanned_item_code("ABC_DEF|1/2", ["ABC_DEF"]),
            ("ABC_DEF", 1),
        )
        self.assertEqual(
            resolve_scanned_item_code(
                "PROJECT_ABC_DEF|2/2",
                ["ABC_DEF"],
            ),
            ("ABC_DEF", 2),
        )

    def test_item_code_lookup_ignores_outer_spaces_and_case(self):
        self.assertEqual(
            resolve_scanned_item_code("abc-001|1/1", [" ABC-001 "]),
            (" ABC-001 ", 1),
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
