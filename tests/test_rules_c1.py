"""C1 规范驱动规则测试（2026-09-23）：R9 差额专项 / R10 行级勾稽 / R11 红冲关联占位。

口径登记（诚实声明）：
- 差额票计税基础 = 销售额 - 扣除额（合成依据 EI386 + 金蝶差额开票口径）；
  真实差额票字段形态/备注格式待真实票核验。
- 行级勾稽：Σ行金额/税额 = 票面（容差 ±0.01）；行级税率自洽 amount×rate≈tax（可解析时）。
- 红冲关联：单票上传仅票面检查（被冲蓝票号存在性）；完整关联需历史发票库。
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import ItemDetail, NormalizedInvoice
from app.rules import RulesConfig, run_rules, run_rules_with_states

CFG = RulesConfig(company_name="示例科技有限公司", company_taxid="91310000MA1FL1XXXX")


def inv(no, date, amount, total, tax, invoice_type="增值税电子普通发票",
        is_differential=False, is_red_letter=False, items=None,
        differential_deduction=None, red_letter_blue_no="", raw_fields=None,
        seller="示例供应商", taxid="91330000MA1FL2XXXX") -> NormalizedInvoice:
    return NormalizedInvoice(
        invoice_no=no, invoice_type=invoice_type, issue_date=date,
        amount=amount, tax=tax, total=total,
        buyer_name="示例企业", buyer_taxid="91330000MA1FL0XXXX",
        seller_name=seller, seller_taxid=taxid,
        is_differential=is_differential, is_red_letter=is_red_letter,
        items=items or [], differential_deduction=differential_deduction,
        red_letter_blue_no=red_letter_blue_no, raw_fields=raw_fields or {},
    )


class TestR9Differential(unittest.TestCase):
    def test_differential_missing_kce(self):
        """差额票声明（备注含差额征税）但无 KCE → 中/疑似。"""
        f = run_rules([inv("1001", "2026-01-01", Decimal("800"), Decimal("848"),
                           Decimal("48"), is_differential=True,
                           raw_fields={"Remark": "差额征税：200.00。"})], CFG)
        r9 = [x for x in f if x.rule_id == "R9"]
        self.assertEqual(len(r9), 1)
        self.assertEqual(r9[0].severity, "中")
        self.assertIn("缺少扣除额", r9[0].message)
        self.assertIn("differential_deduction", r9[0].field)

    def test_differential_kce_exceeds_total(self):
        """扣除额 > 价税合计 → 高/疑似（数据错误）。"""
        f = run_rules([inv("1002", "2026-01-01", Decimal("100"), Decimal("106"),
                           Decimal("6"), is_differential=True,
                           differential_deduction=Decimal("500.00"))], CFG)
        r9 = [x for x in f if x.rule_id == "R9"]
        self.assertEqual(len(r9), 1)
        self.assertEqual(r9[0].severity, "高")
        self.assertIn("大于价税合计", r9[0].message)

    def test_differential_remark_mismatch(self):
        """备注"差额征税：200.00。"与 KCE=150.00 不一致 → 中/疑似。"""
        f = run_rules([inv("1003", "2026-01-01", Decimal("800"), Decimal("848"),
                           Decimal("48"), is_differential=True,
                           differential_deduction=Decimal("150.00"),
                           raw_fields={"Remark": "差额征税：200.00。"})], CFG)
        r9 = [x for x in f if x.rule_id == "R9"]
        self.assertEqual(len(r9), 1)
        self.assertIn("不一致", r9[0].message)

    def test_differential_normal_passes(self):
        """KCE 存在 + 备注一致 + KCE≤total → R9 不报。"""
        f = run_rules([inv("1004", "2026-01-01", Decimal("800"), Decimal("848"),
                           Decimal("48"), is_differential=True,
                           differential_deduction=Decimal("200.00"),
                           raw_fields={"Remark": "差额征税：200.00。"})], CFG)
        self.assertFalse([x for x in f if x.rule_id == "R9"])

    def test_state_none_differential(self):
        """批次无差额票 → R9 三态=未执行。"""
        _, states = run_rules_with_states([inv("1005", "2026-01-01", Decimal("100"),
                                               Decimal("113"), Decimal("13"))], CFG)
        self.assertIn("未执行（本批次无差额征税票）", states["R9"])


class TestR10ItemReconciliation(unittest.TestCase):
    def _multirate(self):
        # 6%:100→6 ｜ 9%:200→18 ｜ 13%:300→39 → Σ金额 600、Σ税额 63（与票面一致）
        return inv("2001", "2026-01-01", Decimal("600"), Decimal("663"), Decimal("63"),
                   items=[
                       ItemDetail(name="服务A", amount=Decimal("100.00"),
                                  tax_rate="0.06", tax_amount=Decimal("6.00")),
                       ItemDetail(name="服务B", amount=Decimal("200.00"),
                                  tax_rate="0.09", tax_amount=Decimal("18.00")),
                       ItemDetail(name="服务C", amount=Decimal("300.00"),
                                  tax_rate="0.13", tax_amount=Decimal("39.00")),
                   ])

    def test_item_reconciliation_passes(self):
        """多税率行级：Σ行金额/税额=票面 + 行级税率自洽 → R10 不报。"""
        f = run_rules([self._multirate()], CFG)
        self.assertFalse([x for x in f if x.rule_id == "R10"])

    def test_item_sum_amount_mismatch(self):
        """Σ行金额 ≠ 票面金额 → 高/疑似（税额口径一致，只测金额分支）。"""
        f = run_rules([inv("2002", "2026-01-01", Decimal("100"), Decimal("111.70"),
                           Decimal("11.70"), items=[
                               ItemDetail(name="A", amount=Decimal("90.00"),
                                          tax_rate="0.13", tax_amount=Decimal("11.70")),
                           ])], CFG)
        r10 = [x for x in f if x.rule_id == "R10"]
        self.assertEqual(len(r10), 1)
        self.assertEqual(r10[0].severity, "高")
        self.assertIn("金额合计与票面金额不符", r10[0].message)

    def test_item_sum_tax_mismatch(self):
        """Σ行税额 ≠ 票面税额 → 高/疑似。"""
        f = run_rules([inv("2003", "2026-01-01", Decimal("100"), Decimal("115"),
                           Decimal("15"), items=[
                               ItemDetail(name="A", amount=Decimal("100.00"),
                                          tax_rate="0.13", tax_amount=Decimal("13.00")),
                           ])], CFG)
        r10 = [x for x in f if x.rule_id == "R10"]
        self.assertEqual(len(r10), 1)
        self.assertIn("税额合计与票面税额不符", r10[0].message)

    def test_item_rate_self_consistent(self):
        """行级税额与税率不符（100×0.13=13 ≠ 20）→ 中/疑似，含行号。"""
        f = run_rules([inv("2004", "2026-01-01", Decimal("100"), Decimal("120"),
                           Decimal("20"), items=[
                               ItemDetail(name="A", amount=Decimal("100.00"),
                                          tax_rate="0.13", tax_amount=Decimal("20.00")),
                           ])], CFG)
        r10 = [x for x in f if x.rule_id == "R10"]
        self.assertEqual(len(r10), 1)
        self.assertEqual(r10[0].severity, "中")
        self.assertIn("第 1 行", r10[0].message)
        # 证据链含行号
        rows = [e.row for e in r10[0].evidence_chain]
        self.assertTrue(any(r == 1 for r in rows), "证据链应定位到行号")

    def test_star_rate_skipped(self):
        """税率原文不可解析（"*"）→ 跳过行级税率自洽（不误报）；Σ 勾稽口径一致。"""
        f = run_rules([inv("2005", "2026-01-01", Decimal("100"), Decimal("100"),
                           Decimal("0"), items=[
                               ItemDetail(name="A", amount=Decimal("100.00"),
                                          tax_rate="*", tax_amount=Decimal("0.00")),
                           ])], CFG)
        r10 = [x for x in f if x.rule_id == "R10"]
        self.assertEqual(len(r10), 0, "税率不可解析行应跳过自洽检查")

    def test_state_no_items(self):
        """批次无行级数据（中文/拼音方言单行票）→ R10 三态=未执行。"""
        _, states = run_rules_with_states([inv("2006", "2026-01-01", Decimal("57.28"),
                                               Decimal("59.00"), Decimal("1.72"))], CFG)
        self.assertIn("未执行（无明细行数据）", states["R10"])


class TestR11RedLetterLink(unittest.TestCase):
    def test_red_letter_with_blue_no_passes(self):
        """红冲票携带被冲蓝票号 → R11 不报。"""
        f = run_rules([inv("3001", "2026-01-01", Decimal("-2500"), Decimal("-2650"),
                           Decimal("-150"), invoice_type="红字发票",
                           is_red_letter=True,
                           red_letter_blue_no="24110000000000100003")], CFG)
        self.assertFalse([x for x in f if x.rule_id == "R11"])

    def test_red_letter_missing_blue_no(self):
        """红冲票未携带被冲蓝票号 → 低/疑似（无法关联校验，占位声明）。"""
        f = run_rules([inv("3002", "2026-01-01", Decimal("-2500"), Decimal("-2650"),
                           Decimal("-150"), invoice_type="红字发票",
                           is_red_letter=True)], CFG)
        r11 = [x for x in f if x.rule_id == "R11"]
        self.assertEqual(len(r11), 1)
        self.assertEqual(r11[0].severity, "低")
        self.assertIn("无法做红冲关联校验", r11[0].message)
        # 证据链：红冲标志=True + 蓝票号缺失
        fields = [e.field for e in r11[0].evidence_chain]
        self.assertEqual(fields, ["is_red_letter", "red_letter_blue_no"])

    def test_state_no_red_letter(self):
        """批次无红字票 → R11 三态=未执行。"""
        _, states = run_rules_with_states([inv("3003", "2026-01-01", Decimal("100"),
                                               Decimal("113"), Decimal("13"))], CFG)
        self.assertIn("未执行（本批次无红字票）", states["R11"])


if __name__ == "__main__":
    unittest.main()
