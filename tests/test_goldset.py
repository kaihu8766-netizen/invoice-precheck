#!/usr/bin/env python3
"""goldset 基准集测试（T-053，RV-20261001-360 修订）。

覆盖：
- gold_labeler 派生标签边界（勾稽容差/日期/票号/负数）
- run_goldset L2 冒烟（不依赖 L3 本地副本）
"""
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.gold_labeler import derive_issues  # noqa: E402


class TestGoldLabeler(unittest.TestCase):
    def test_reconcile_ok_within_tolerance(self):
        issues = derive_issues({"amount": "100.00", "tax": "13.00", "total": "113.00"})
        self.assertNotIn("RECONCILE", [i["code"] for i in issues])

    def test_reconcile_beyond_tolerance_reports(self):
        issues = derive_issues({"amount": "100.00", "tax": "13.00", "total": "114.00"})
        self.assertIn("RECONCILE", [i["code"] for i in issues])

    def test_reconcile_0_5_cent_boundary(self):
        # 容差 0.005：差 0.004 不报，差 0.006 报
        self.assertNotIn("RECONCILE", [i["code"] for i in derive_issues(
            {"amount": "1.00", "tax": "0.00", "total": "1.004"})])
        self.assertIn("RECONCILE", [i["code"] for i in derive_issues(
            {"amount": "1.00", "tax": "0.00", "total": "1.006"})])

    def test_date_invalid(self):
        issues = derive_issues({"issue_date": "2026-13-40", "amount": "1", "tax": "0", "total": "1"})
        self.assertIn("DATE_INVALID", [i["code"] for i in issues])

    def test_date_valid(self):
        issues = derive_issues({"issue_date": "2026-08-01", "amount": "1", "tax": "0", "total": "1"})
        self.assertNotIn("DATE_INVALID", [i["code"] for i in issues])

    def test_invno_not_20_digits(self):
        issues = derive_issues({"invoice_no": "1234567890", "amount": "1", "tax": "0", "total": "1"})
        self.assertIn("INVNO_INVALID", [i["code"] for i in issues])

    def test_negative_amount_reports(self):
        issues = derive_issues({"amount": "-100", "tax": "0", "total": "-100"})
        self.assertIn("AMOUNT_NEG", [i["code"] for i in issues])

    def test_missing_fields_no_crash(self):
        issues = derive_issues({"invoice_no": ""})
        self.assertEqual(issues, [])


class TestRunGoldsetSmoke(unittest.TestCase):
    def test_l2_run_exit0_and_report(self):
        out = subprocess.run(
            [sys.executable, "scripts/run_goldset.py", "--tier", "L2", "--json"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads((ROOT / "benchmark/results/goldset_report.json").read_text(encoding="utf-8"))
        self.assertIn("L2", report["tiers"])
        self.assertIn("parse_rate", report["tiers"]["L2"])
        self.assertIn("field_accuracy", report["tiers"]["L2"])
        self.assertIn("miss_rate", report["tiers"]["L2"])
        self.assertIn("false_positive_rate", report["tiers"]["L2"])

    def test_goldset_json_wellformed(self):
        gs = json.loads((ROOT / "benchmark/goldset/goldset.json").read_text(encoding="utf-8"))
        self.assertIn("goldset_version", gs)
        self.assertIn("sha256_private_index", gs)
        tiers = {s["source_tier"] for s in gs["samples"]}
        self.assertEqual(tiers, {"L1", "L2", "L3"})
        # L3 条目必须只有指针+hash，不得含 truth 金额（数据红线）
        for s in gs["samples"]:
            if s["source_tier"] == "L3":
                self.assertIsNone(s.get("truth"), f"L3 {s['sample_id']} 不得含 truth（金额真实值）")
                self.assertIn("private_ref", s)


if __name__ == "__main__":
    unittest.main()
