#!/usr/bin/env python3
"""confidence.py 字段级置信度测试（T-054 · RV-20261001-362 修订）。

覆盖：路径先验映射 / 字段证据惩罚 / OCR 行 P25 乘子 / 缺失字段=0 / 三档边界 /
parser 契约（field_evidence+ocr_confs 填充）/ eval 冒烟。
"""
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.confidence import bucket, field_confidence, wilson_interval  # noqa: E402
from app.models import NormalizedInvoice  # noqa: E402


def _mk_inv(**kw):
    defaults = dict(invoice_no="25300000000090000000", invoice_type="电子发票", issue_date="2026-08-01",
                    amount=Decimal("100.00"), tax=Decimal("13.00"), total=Decimal("113.00"),
                    buyer_name="示例采购有限公司", buyer_taxid="91310000MA1FKEXAMPLE1",
                    seller_name="示例服务有限公司", seller_taxid="91320000MA1FKEXAMPLE2",
                    raw_fields={"parse_path": "qr+text"})
    defaults.update(kw)
    return NormalizedInvoice(**defaults)


class TestPathPrior(unittest.TestCase):
    def test_xml_prior_099(self):
        inv = _mk_inv(raw_fields={"parse_path": "xml"})
        conf = field_confidence(inv)
        self.assertGreaterEqual(conf["invoice_no"], 0.99)
        self.assertEqual(bucket(conf["invoice_no"]), "high")

    def test_ocr_prior_lower(self):
        inv = _mk_inv(raw_fields={"parse_path": "image+ocr"}, ocr_confs=[0.9] * 8)
        conf = field_confidence(inv)
        self.assertLess(conf["invoice_no"], 0.90)
        self.assertEqual(bucket(conf["invoice_no"]), "low")  # 0.85×1.0×P25(0.9)=0.765<0.80


class TestEvidencePenalty(unittest.TestCase):
    def test_reconcile_penalty(self):
        inv = _mk_inv(field_evidence={"amount": 0.5, "tax": 0.5, "total": 0.5})
        conf = field_confidence(inv)
        self.assertAlmostEqual(conf["amount"], round(0.95 * 0.5, 3))
        self.assertEqual(bucket(conf["amount"]), "low")
        # 不受影响的字段保持先验
        self.assertAlmostEqual(conf["invoice_no"], 0.95)

    def test_party_swapped_penalty(self):
        inv = _mk_inv(field_evidence={"buyer_name": 0.7, "seller_name": 0.7})
        conf = field_confidence(inv)
        self.assertEqual(bucket(conf["buyer_name"]), "low")  # 0.95×0.7=0.665<0.80

    def test_missing_field_zero(self):
        inv = _mk_inv(issue_date="", raw_fields={"parse_path": "xml"})
        conf = field_confidence(inv)
        self.assertEqual(conf["issue_date"], 0.0)
        self.assertEqual(bucket(conf["issue_date"]), "low")


class TestOcrFactor(unittest.TestCase):
    def test_p25_factor(self):
        # OCR 路径：先验 0.85 × P25(confs)；噪声行（0.1）不影响 P25
        inv = _mk_inv(raw_fields={"parse_path": "image+ocr"},
                      ocr_confs=[0.1, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9])
        conf = field_confidence(inv)
        # P25(8 个值)=sorted[1]=0.9 → 0.85×0.9=0.765
        self.assertAlmostEqual(conf["invoice_no"], round(0.85 * 0.9, 3))

    def test_p25_not_min(self):
        # 单条极低噪声行（0.05）不应把整票拉爆：P25(11×0.9+1×0.05)=0.9 > P25(12×0.05)=0.05
        inv_ok = _mk_inv(raw_fields={"parse_path": "image+ocr"},
                         ocr_confs=[0.9] * 11 + [0.05])
        inv_bad = _mk_inv(raw_fields={"parse_path": "image+ocr"},
                          ocr_confs=[0.05] * 12)
        self.assertGreater(field_confidence(inv_ok)["invoice_no"],
                           field_confidence(inv_bad)["invoice_no"])


class TestBucketBoundary(unittest.TestCase):
    def test_high_mid_low(self):
        self.assertEqual(bucket(0.95), "high")
        self.assertEqual(bucket(0.949), "mid")
        self.assertEqual(bucket(0.80), "mid")
        self.assertEqual(bucket(0.799), "low")
        self.assertEqual(bucket(0.0), "low")


class TestWilson(unittest.TestCase):
    def test_wilson_known(self):
        wi = wilson_interval(6, 77)
        self.assertIsNotNone(wi)
        self.assertAlmostEqual(wi.p, 6 / 77, places=3)

    def test_wilson_small_n_none(self):
        self.assertIsNone(wilson_interval(1, 4))  # n<5 → None

    def test_wilson_zero_n(self):
        self.assertIsNone(wilson_interval(0, 0))


class TestParserContract(unittest.TestCase):
    def test_parse_pdf_fills_evidence(self):
        from app.parser import parse_document
        inv = parse_document((ROOT / "benchmark/synthetic/synthetic_8_bad_expected.pdf").read_bytes())
        # 勾稽不符 → amount/tax/total 证据惩罚 0.5
        self.assertEqual(inv.field_evidence.get("amount"), 0.5)
        self.assertEqual(inv.field_evidence.get("total"), 0.5)

    def test_parse_xml_path_marked(self):
        from app.parser import parse_document
        inv = parse_document((ROOT / "tests/corpus/official-einv-xbrl-ordinary.xml").read_bytes())
        self.assertEqual(inv.raw_fields.get("parse_path"), "xml")
        conf = field_confidence(inv)
        self.assertGreaterEqual(conf["invoice_no"], 0.99)


class TestEvalSmoke(unittest.TestCase):
    def test_eval_exit0_and_report(self):
        out = subprocess.run(
            [sys.executable, "scripts/eval_goldset_confidence.py", "--json"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        rep = json.loads((ROOT / "benchmark/results/confidence_report.json").read_text(encoding="utf-8"))
        for bk in ("high", "mid", "low"):
            self.assertIn(bk, rep["buckets"])
        self.assertIn("auc", rep)


if __name__ == "__main__":
    unittest.main()
