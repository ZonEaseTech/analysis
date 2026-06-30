"""make_required_fields_identity / make_unique_key_identity — 非销售导出的基线恒等式。"""
import unittest

import tests._setup  # noqa: F401

from semantic.validators import check
from semantic.validators.core import Severity
from semantic.validators.identities import (
    make_required_fields_identity,
    make_unique_key_identity,
)


class TestRequiredFields(unittest.TestCase):
    def setUp(self):
        self.ident = make_required_fields_identity(
            ("item_name", "material_name"), name="BOM导出必填")

    def test_complete_row_passes(self):
        result = check([{"item_name": "汉堡", "material_name": "面包"}], [self.ident])
        self.assertEqual(result.violations, [])

    def test_empty_field_must_fix(self):
        result = check([{"item_name": "汉堡", "material_name": ""}], [self.ident])
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            for v in result.violations))

    def test_missing_key_must_fix(self):
        result = check([{"item_name": "汉堡"}], [self.ident])
        self.assertTrue(result.violations)

    def test_whitespace_only_field_must_fix(self):
        result = check([{"item_name": "汉堡", "material_name": "   "}], [self.ident])
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            for v in result.violations))


class TestUniqueKey(unittest.TestCase):
    def test_duplicate_key_fires(self):
        ident, prepare = make_unique_key_identity(("store", "item"), name="主键唯一")
        rows = prepare([{"store": "1", "item": "A"}, {"store": "1", "item": "A"}])
        result = check(rows, [ident])
        self.assertTrue(any(v.severity == Severity.MUST_FIX
                            for v in result.violations))

    def test_unique_rows_pass(self):
        ident, prepare = make_unique_key_identity(("store", "item"), name="主键唯一")
        rows = prepare([{"store": "1", "item": "A"}, {"store": "1", "item": "B"}])
        result = check(rows, [ident])
        self.assertEqual(result.violations, [])


if __name__ == "__main__":
    unittest.main()
