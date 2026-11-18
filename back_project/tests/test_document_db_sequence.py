from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.features import document_db


class ItemTypeCodeSequenceTests(unittest.TestCase):
    @patch.object(document_db, "fetch_one", return_value={"n": 80})
    def test_next_item_type_code_uses_public_postgresql_sequence(self, fetch_one):
        result = document_db.next_item_type_code()

        self.assertEqual(result, 80)
        fetch_one.assert_called_once_with(
            "SELECT nextval('public.item_type_code_seq') AS n"
        )

    @patch.object(document_db, "fetch_one", return_value=None)
    def test_missing_sequence_result_does_not_fall_back_to_reserved_code(self, _fetch_one):
        with self.assertRaises(RuntimeError):
            document_db.next_item_type_code()


if __name__ == "__main__":
    unittest.main()