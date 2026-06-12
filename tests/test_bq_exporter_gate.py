"""bq_exporter 的闸门钩子: set_gate 后写盘前必过闸。"""
import unittest

import tests._setup  # noqa: F401

from bq_reports.utils.bq_exporter import BaseExporter
from semantic.validators.gate import GateSpec
from semantic.validators.identities import DEFAULT_IDENTITIES


class TestExporterGate(unittest.TestCase):
    def test_gate_spec_holds_config(self):
        spec = GateSpec(identities=DEFAULT_IDENTITIES, force=False,
                        report_name="daily_sales",
                        build_check_rows=lambda rows: rows)
        self.assertEqual(spec.report_name, "daily_sales")

    def test_exporter_accepts_gate(self):
        exporter = BaseExporter.__new__(BaseExporter)  # 不触发 BQ 连接
        exporter.gate_spec = None
        spec = GateSpec(identities=DEFAULT_IDENTITIES, force=False,
                        report_name="t", build_check_rows=lambda rows: rows)
        exporter.set_gate(spec)
        self.assertIs(exporter.gate_spec, spec)

    def test_gate_spec_run_blocks_on_empty(self):
        spec = GateSpec(identities=DEFAULT_IDENTITIES, force=False,
                        report_name="t", build_check_rows=lambda rows: rows)
        with self.assertRaises(SystemExit) as cm:
            spec.run([])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
