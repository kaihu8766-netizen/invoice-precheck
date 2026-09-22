"""parser 自测：构造数电票 XML（含命名空间、专票结构、'*' 税额、勾稽异常）验证解析。"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser import detect_type, parse_xml

NS = "urn:cn:gov:etax:2021:invoice"

SAMPLE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<发票 xmlns="{NS}">
  <发票号码>04300260031112345678</发票号码>
  <开票日期>20260815</开票日期>
  <发票类型>增值税专用发票</发票类型>
  <购买方>
    <购买方名称>示例科技有限公司</购买方名称>
    <购买方纳税人识别号>91310000MA1FL1XXXX</购买方纳税人识别号>
  </购买方>
  <销售方>
    <销售方名称>北京华信办公用品有限公司</销售方名称>
    <销售方纳税人识别号>91110108XXXXXXXXXX</销售方纳税人识别号>
  </销售方>
  <合计金额>1000.00</合计金额>
  <合计税额>130.00</合计税额>
  <价税合计>1130.00</价税合计>
</发票>
"""

STAR_TAX_XML = SAMPLE_XML.replace("<合计税额>130.00</合计税额>", "<合计税额>*</合计税额>")

BAD_TOTAL_XML = SAMPLE_XML.replace("<价税合计>1130.00</价税合计>", "<价税合计>1135.00</价税合计>")


class TestParser(unittest.TestCase):
    def test_detect_type(self):
        self.assertEqual(detect_type(b'<?xml version="1.0"?><a/>'), "xml")
        self.assertEqual(detect_type(b"PK\x03\x04rest"), "ofd")
        self.assertEqual(detect_type(b"%PDF-1.7"), "pdf")
        self.assertEqual(detect_type(b"\x89PNG"), "unknown")

    def test_parse_normal(self):
        inv = parse_xml(SAMPLE_XML.encode())
        self.assertEqual(inv.invoice_no, "04300260031112345678")
        self.assertEqual(inv.issue_date, "2026-08-15")
        self.assertEqual(inv.buyer_name, "示例科技有限公司")
        self.assertEqual(inv.amount, Decimal("1000.00"))
        self.assertEqual(inv.tax, Decimal("130.00"))
        self.assertEqual(inv.total, Decimal("1130.00"))
        self.assertNotIn("勾稽异常", " ".join(inv.parse_warnings))
        self.assertTrue(inv.source_hash)

    def test_star_tax(self):
        inv = parse_xml(STAR_TAX_XML.encode())
        self.assertEqual(inv.tax, Decimal("0"))
        self.assertTrue(any("'*'" in w for w in inv.parse_warnings))

    def test_bad_total_flagged(self):
        inv = parse_xml(BAD_TOTAL_XML.encode())
        self.assertTrue(any("勾稽异常" in w for w in inv.parse_warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
