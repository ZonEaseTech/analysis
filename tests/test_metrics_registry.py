"""Tests for the metric registry (semantic/metrics)."""
import unittest

from semantic.metrics import (
    DOMAINS,
    load_registry,
    metric_from_dict,
    registry_by_domain,
    validate_registry,
)
from semantic.metrics.render_catalog import render_catalog


class RegistryLoadTests(unittest.TestCase):
    def setUp(self):
        self.metrics = load_registry()

    def test_loads_at_least_catalog_count(self):
        # docs/metrics-catalog.md documented ~21 metrics; we model >= that.
        self.assertGreaterEqual(len(self.metrics), 21)

    def test_ids_unique_and_snake_case(self):
        ids = [m.id for m in self.metrics]
        self.assertEqual(len(ids), len(set(ids)), "duplicate metric ids")
        for mid in ids:
            self.assertRegex(mid, r"^[a-z][a-z0-9_]*$", f"bad id: {mid}")

    def test_every_domain_present(self):
        seen = {m.domain for m in self.metrics}
        for d in ("sales", "settlement", "finance", "kpi", "metadata"):
            self.assertIn(d, seen, f"domain {d} has no metrics")
        self.assertTrue(seen.issubset(set(DOMAINS)))

    def test_upstream_references_resolve(self):
        # load_registry already validates; assert explicitly it does not raise.
        validate_registry(self.metrics)
        known = {m.id for m in self.metrics}
        for m in self.metrics:
            for up in m.lineage.upstream_metrics:
                self.assertIn(up, known, f"{m.id} -> unknown upstream {up}")

    def test_every_metric_has_a_business_formula(self):
        for m in self.metrics:
            self.assertTrue(m.formula.business.strip(), f"{m.id} missing formula")

    def test_group_by_domain_orders_by_declaration(self):
        grouped = registry_by_domain(self.metrics)
        self.assertEqual(list(grouped.keys())[: len(DOMAINS)], list(DOMAINS.keys()))


class SchemaValidationTests(unittest.TestCase):
    def _base(self):
        return {
            "id": "x",
            "name": "X",
            "domain": "sales",
            "status": "live",
            "confidence": "ACTUAL",
            "definition": "d",
            "formula": {"business": "a + b"},
        }

    def test_minimal_metric_builds(self):
        m = metric_from_dict(self._base())
        self.assertEqual(m.id, "x")
        self.assertEqual(m.anchor, "x")

    def test_unknown_domain_rejected(self):
        d = self._base()
        d["domain"] = "nope"
        with self.assertRaises(ValueError):
            metric_from_dict(d)

    def test_unknown_confidence_rejected(self):
        d = self._base()
        d["confidence"] = "MAYBE"
        with self.assertRaises(ValueError):
            metric_from_dict(d)

    def test_missing_formula_rejected(self):
        d = self._base()
        del d["formula"]
        with self.assertRaises(ValueError):
            metric_from_dict(d)

    def test_duplicate_ids_rejected(self):
        a = metric_from_dict(self._base())
        b = metric_from_dict(self._base())
        with self.assertRaises(ValueError):
            validate_registry([a, b])

    def test_dangling_upstream_rejected(self):
        d = self._base()
        d["lineage"] = {"upstream_metrics": ["ghost"]}
        m = metric_from_dict(d)
        with self.assertRaises(ValueError):
            validate_registry([m])


class RenderCatalogTests(unittest.TestCase):
    def setUp(self):
        self.metrics = load_registry()
        self.md = render_catalog(self.metrics)

    def test_renders_every_metric_anchor(self):
        for m in self.metrics:
            self.assertIn(f'<a id="{m.anchor}"></a>', self.md)

    def test_deterministic(self):
        self.assertEqual(self.md, render_catalog(self.metrics))

    def test_has_header_and_footer(self):
        self.assertIn("# 口径地图", self.md)
        self.assertIn("## 排障速查", self.md)
        self.assertIn("自动生成", self.md)


if __name__ == "__main__":
    unittest.main()
