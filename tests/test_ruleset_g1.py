"""G1 规则版本化 + 证据链（2026-09-23）。

覆盖：
- 规则包元数据完整性（版本/生效日期/8 规则含 R8/每规则有政策依据）
- 单一权威来源（report 不再各自维护规则清单——修复 R8 曾缺失于报告清单的漂移）
- Finding.evidence_chain 结构化证据链（可审计定位：字段/原文/规范化值/行号/计算）
- report 输出：evidence_chain + ruleset 元数据 + R8 在规则清单 + invoice.item_count
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import NormalizedInvoice
from app.report import build_report
from app.rules import RULESET_META, RULESET_VERSION, RulesConfig, run_rules_with_states


def _inv(invoice_no="23440000000000100001", issue_date="2026-01-01",
         amount="100.00", tax="6.00", total="106.00",
         seller_name="供应商A", seller_taxid="91330000XXXX00001A",
         buyer_name="示例企业", buyer_taxid="91330000XXXX0000AA") -> NormalizedInvoice:
    return NormalizedInvoice(
        invoice_no=invoice_no, invoice_type="增值税电子普通发票",
        issue_date=issue_date,
        amount=Decimal(amount), tax=Decimal(tax), total=Decimal(total),
        buyer_name=buyer_name, buyer_taxid=buyer_taxid,
        seller_name=seller_name, seller_taxid=seller_taxid,
    )


class TestRulesetMeta(unittest.TestCase):
    def test_ruleset_version_bumped(self):
        """G1 发布应升级版本（0.1.1 → 0.2.0）并带生效日期。"""
        self.assertEqual(RULESET_VERSION, "0.2.0")
        self.assertEqual(RULESET_META["version"], RULESET_VERSION)
        self.assertEqual(RULESET_META["effective_date"], "2026-09-23")

    def test_ruleset_meta_contains_all_rules_with_basis(self):
        """规则包必须含 R1-R8（含此前漂移缺失的 R8）且每规则有名称/严重度/政策依据。"""
        ids = [r["rule_id"] for r in RULESET_META["rules"]]
        self.assertEqual(ids, ["R1", "R2", "R3", "R4", "R6", "R7", "R8"],
                         "规则包应完整列出全部已实现规则（含 R8）")
        for r in RULESET_META["rules"]:
            with self.subTest(rule=r["rule_id"]):
                self.assertTrue(r["name"], f"{r['rule_id']} 缺名称")
                self.assertTrue(r["severity"], f"{r['rule_id']} 缺严重度")
                self.assertTrue(r["basis"], f"{r['rule_id']} 缺政策依据")

    def test_ruleset_meta_scope_note_honest(self):
        """规则包诚实声明：政策依据按主题引用 + 阈值占位待校准（不编造条款号）。"""
        note = RULESET_META["scope_note"]
        self.assertIn("待法务/税务核验", note)
        self.assertIn("占位口径，待真实数据校准", note)


class TestEvidenceChain(unittest.TestCase):
    def test_r1_duplicate_chain(self):
        """R1 重复命中：证据链含判重三键 + 来源哈希（可审计定位）。"""
        a = _inv("23440000000000100001", "2026-01-01", total="106.00")
        b = _inv("23440000000000100001", "2026-01-01", total="106.00")
        findings, _ = run_rules_with_states([a, b])
        dup = [f for f in findings if f.rule_id == "R1" and "存在票号+开票日期+价税合计相同的发票" in f.message]
        self.assertEqual(len(dup), 2, "两张重复票都应命中")
        chain = dup[0].evidence_chain
        fields = [e.field for e in chain]
        self.assertIn("invoice_no", fields)
        self.assertIn("issue_date", fields)
        self.assertIn("total", fields)
        self.assertIn("source_hash", fields)
        total_link = next(e for e in chain if e.field == "total")
        self.assertEqual(total_link.value, "106.00", "证据链应含规范化金额")
        self.assertIn("判重键", total_link.note)

    def test_r8_reconcile_chain_has_calc(self):
        """R8 勾稽不符：证据链含金额/税额/合计 + 计算过程（差 -0.01 级别可复现）。"""
        inv = _inv(total="106.50")  # amount 100.00 + tax 6.00 = 106.00 ≠ 106.50
        findings, _ = run_rules_with_states([inv])
        r8 = next(f for f in findings if f.rule_id == "R8" and "勾稽不符" in f.message)
        chain = r8.evidence_chain
        self.assertEqual([e.field for e in chain], ["amount", "tax", "total", "calc"])
        calc = chain[-1]
        self.assertEqual(calc.field, "calc")
        self.assertEqual(calc.value, "106.00", "calc 规范化值=金额+税额结果")
        self.assertIn("勾稽差", calc.note)
        self.assertTrue(r8.evidence_chain, "R8 应有证据链")

    def test_incomplete_finding_has_empty_chain(self):
        """数据不完整类提示：证据链为空列表（不造假证据）。"""
        inv = _inv(invoice_no="", total="0.00")
        findings, _ = run_rules_with_states([inv])
        r1 = [f for f in findings if f.rule_id == "R1"][0]
        self.assertEqual(r1.evidence_chain, [])


class TestReportG1(unittest.TestCase):
    def setUp(self):
        a = _inv("23440000000000100001", "2026-01-01", total="106.00")
        b = _inv("23440000000000100001", "2026-01-01", total="106.00")
        findings, states = run_rules_with_states([a, b], RulesConfig(
            company_name="示例企业", company_taxid="91330000XXXX0000AA"))
        self.report = build_report([a, b], findings, [], RULESET_VERSION,
                                   rule_states=states)

    def test_report_ruleset_meta(self):
        """报告输出规则包元数据（版本/名称/生效日期/诚实声明）。"""
        rs = self.report["ruleset"]
        self.assertEqual(rs["version"], "0.2.0")
        self.assertEqual(rs["effective_date"], "2026-09-23")
        self.assertTrue(rs["name"])
        self.assertTrue(rs["scope_note"])

    def test_report_rules_include_r8(self):
        """报告规则清单必须含 R8（修复 RULES_META 漂移：R8 曾缺失）。"""
        ids = [r["rule_id"] for r in self.report["rules"]]
        self.assertIn("R8", ids)
        self.assertEqual(len(self.report["rules"]), 7)

    def test_report_evidence_chain_serialized(self):
        """报告 risk_list 输出结构化证据链（JSON 安全 dict）。"""
        f0 = self.report["findings"][0]
        self.assertIn("evidence_chain", f0)
        for e in f0["evidence_chain"]:
            self.assertIn("field", e)
            self.assertIn("raw", e)
            self.assertIn("value", e)
            self.assertIn("row", e)
            self.assertIn("note", e)

    def test_report_invoice_item_count(self):
        """发票明细输出行级条数（G0 行级表示贯通报告）。"""
        self.assertIn("item_count", self.report["invoices"][0])

    def test_report_single_source_ruleset(self):
        """报告规则清单必须来自 rules.RULESET_META（单一权威来源，无本地漂移清单）。"""
        import app.report as report_mod
        self.assertTrue(hasattr(report_mod, "RULESET_META"),
                        "report 应引用 rules 的 RULESET_META")
        self.assertFalse(hasattr(report_mod, "RULES_META"),
                         "report 不应再维护独立 RULES_META（漂移修复）")


if __name__ == "__main__":
    unittest.main()
