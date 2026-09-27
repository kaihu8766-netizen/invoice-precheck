"""P2-1 合成样本生成器回归：可复现性（黄金文件防漂移）+ 参数化变体勾稽。"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.generate_synthetic import (FILENAMES, GENERATORS, verify_reproducible)

CORPUS_SYNTHETIC = Path(__file__).resolve().parents[1] / "tests" / "corpus" / "synthetic"


class TestSyntheticGenerator(unittest.TestCase):
    def test_default_reproducible(self):
        """默认参数必须逐字节复现磁盘样本（生成器是源头，防漂移）。"""
        diffs = verify_reproducible(CORPUS_SYNTHETIC)
        self.assertEqual(diffs, [], "生成器默认输出与磁盘不一致：\n" + "\n".join(diffs))

    def test_generated_files_are_valid_xml(self):
        """生成产物必须是合法 XML（含参数化变体）。"""
        import xml.etree.ElementTree as ET
        for kind, fn in FILENAMES.items():
            with self.subTest(kind=kind):
                text = GENERATORS[kind]()
                ET.fromstring(text.encode("utf-8"))  # 不抛 = well-formed

    def test_variant_reconciliation(self):
        """参数化变体：20 位票号 + 勾稽自洽（构造硬规则 1/2）。"""
        red = GENERATORS["red-letter"](invoice_no="26110000000000100014",
                                       amount="-100.00", tax="-6.00", total="-106.00")
        self.assertIn("26110000000000100014", red)
        self.assertIn("-100.00", red)

        diff = GENERATORS["differential"](invoice_no="26440000000000100015",
                                          amount="900.00", tax="54.00", total="954.00", kce="300.00")
        self.assertIn("954.00", diff)
        self.assertIn("300.00", diff)

        multi = GENERATORS["multirate"](
            invoice_no="26320000000000100016",
            rows=(("50.00", "3.00", "0.06"), ("70.00", "6.30", "0.09")))
        self.assertIn("120.00", multi)   # 50+70
        self.assertIn("9.30", multi)     # 3+6.30
        self.assertIn("129.30", multi)   # 合计

        pinyin = GENERATORS["pinyin"](invoice_no="26320000000000100017",
                                      amount="30.00", tax="0.90", total="30.90")
        self.assertIn("30.90", pinyin)

    def test_rejects_bad_invoice_no(self):
        """票号非 20 位必须被拒绝（构造硬规则 2）。"""
        with self.assertRaises(AssertionError):
            GENERATORS["red-letter"](invoice_no="123")

    def test_rejects_bad_reconciliation(self):
        """勾稽不符必须被拒绝（构造硬规则 1）。"""
        with self.assertRaises(AssertionError):
            GENERATORS["pinyin"](amount="30.00", tax="0.90", total="99.00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
