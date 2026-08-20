import unittest
from collections import Counter

from inventory_system.barcode_serials import resolve_scanned_item_code
from inventory_system.pages.label_a4_documents import (
    _shark_expand_a4_unit_labels,
)


class OutboundQrIntegrationTest(unittest.TestCase):
    def test_a4_unit_qr_round_trip_counts_each_printed_label(self):
        item_code = "ABC_DEF"
        item_map = {item_code: {"code": item_code}}
        labels = _shark_expand_a4_unit_labels(
            [
                {
                    "item_code": item_code,
                    "required_quantity": 2,
                }
            ]
        )

        qr_values = [label["qr_value"] for label in labels]
        self.assertEqual(
            qr_values,
            ["ABC_DEF|1/2", "ABC_DEF|2/2"],
        )

        scan_counter = Counter(
            resolve_scanned_item_code(qr_value, item_map)[0]
            for qr_value in qr_values
        )
        self.assertEqual(scan_counter, {item_code: 2})

        for separator in ("}", "｜", "\\"):
            with self.subTest(separator=separator):
                scanner_value = qr_values[0].replace("|", separator)
                self.assertEqual(
                    resolve_scanned_item_code(scanner_value, item_map),
                    (item_code, 1),
                )


if __name__ == "__main__":
    unittest.main()
