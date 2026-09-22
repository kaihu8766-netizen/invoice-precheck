"""P0-3 脱敏流水线回归：打码规则 + 签名清理 + 幂等 + manifest 绑定（防脱敏被绕过）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.redact import redact

RAW = """<?xml version="1.0"?>
<EInvoice><Header><EIid>22442000000921291350</EIid></Header>
<SellerInformation><SellerName>中山市奕博照明有限公司</SellerName>
<SellerIdNum>91442000MA52PFXD1J</SellerIdNum><SellerTelNum>13812345678</SellerTelNum></SellerInformation>
<TaxSupervisionInfo><InvoiceNumber>22442000000921291350</InvoiceNumber>
<Signature><SignedInfo>...</SignedInfo><SignatureValue>abc123</SignatureValue>
<X509Certificate>MIICert...</X509Certificate></Signature></TaxSupervisionInfo></EInvoice>"""


class TestRedact(unittest.TestCase):
    def test_taxid_masked(self):
        out = redact(RAW)
        self.assertNotIn("91442000MA52PFXD1J", out)
        self.assertIn("9144****1J", out)  # 保留前 4 后 2

    def test_mobile_masked(self):
        out = redact(RAW)
        self.assertNotIn("13812345678", out)
        self.assertIn("138****5678", out)

    def test_signature_value_cleared_and_x509_removed(self):
        out = redact(RAW)
        self.assertNotIn("abc123", out)
        self.assertNotIn("MIICert", out)

    def test_strip_signature_whole(self):
        out = redact(RAW, strip_signature=True)
        self.assertNotIn("<Signature>", out)
        self.assertNotIn("SignedInfo", out)

    def test_invoice_no_map(self):
        out = redact(RAW, invoice_no_map={"22442000000921291350": "23440000000000100011"})
        self.assertIn("23440000000000100011", out)
        self.assertNotIn("22442000000921291350", out)

    def test_name_replacement(self):
        out = redact(RAW, replacements={"中山市奕博照明有限公司": "示例照明（广东）有限公司"})
        self.assertIn("示例照明（广东）有限公司", out)
        self.assertNotIn("中山市奕博照明有限公司", out)

    def test_idempotent(self):
        once = redact(RAW, invoice_no_map={"22442000000921291350": "23440000000000100011"})
        twice = redact(once, invoice_no_map={"22442000000921291350": "23440000000000100011"})
        self.assertEqual(once, twice, "脱敏应幂等（二次执行不改变结果）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
