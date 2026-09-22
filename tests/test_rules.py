"""rules 自测：构造整批 NormalizedInvoice，验证 R1/R2/R3/R4/R6/R7 命中与不误报。"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import NormalizedInvoice
from app.rules import RulesConfig, run_rules


def inv(no, date, amount, total, seller="北京华信办公用品有限公司",
        buyer="示例科技有限公司", taxid="91310000MA1FL1XXXX",
        category="办公", reimburse=None, tax=Decimal("0"),
        invoice_type="专票"):
    return NormalizedInvoice(
        invoice_no=no, invoice_type=invoice_type, issue_date=date,
        amount=amount, tax=tax, total=total,
        buyer_name=buyer, buyer_taxid=taxid, seller_name=seller,
        category=category, reimburse_date=reimburse,
    )


CFG = RulesConfig(company_name="示例科技有限公司", company_taxid="91310000MA1FL1XXXX")


class TestRules(unittest.TestCase):
    def test_r1_duplicate(self):
        batch = [
            inv("1001", "2026-08-01", Decimal("100"), Decimal("113"), tax=Decimal("13")),
            inv("1001", "2026-08-01", Decimal("100"), Decimal("113"), tax=Decimal("13")),
            inv("1002", "2026-08-02", Decimal("200"), Decimal("226"), tax=Decimal("26")),
        ]
        f = run_rules(batch, CFG)
        r1 = [x for x in f if x.rule_id == "R1"]
        self.assertEqual(len(r1), 2)  # 两张都标
        self.assertEqual(r1[0].severity, "高")
        self.assertEqual(r1[0].confidence, "确定")

    def test_r1_incomplete_not_duplicate(self):
        # 红线保护：票号缺失的票不参与判重，只出低危提示（H2）
        batch = [
            inv("", "2026-08-01", Decimal("100"), Decimal("113"), tax=Decimal("13")),
            inv("", "2026-08-01", Decimal("100"), Decimal("113"), tax=Decimal("13")),
        ]
        f = run_rules(batch, CFG)
        r1 = [x for x in f if x.rule_id == "R1"]
        self.assertTrue(all(x.severity == "低" for x in r1))
        self.assertTrue(all(x.confidence == "疑似" for x in r1))

    def test_r2_header(self):
        batch = [inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="错误公司", taxid="999999999999999999", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "高")
        # 空值不产出"确定"级结论（M15）
        batch2 = [NormalizedInvoice(invoice_no="x1", invoice_type="普票", issue_date="2026-08-01",
                                    amount=Decimal("1"), tax=Decimal("0"), total=Decimal("1"),
                                    buyer_name="", buyer_taxid="", seller_name="s")]
        r2b = [x for x in run_rules(batch2, CFG) if x.rule_id == "R2"]
        self.assertTrue(all(x.severity == "低" for x in r2b))

    def test_r2_no_config_skips(self):
        # 红线保护：未配置企业主体 → R2 不误报（H7）
        batch = [inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="任意公司", taxid="999999999999999999", tax=Decimal("13"))]
        f = run_rules(batch)  # 默认 config：company 为 None（必须，测未配置场景）
        r2 = [x for x in f if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertIn("未配置企业主体", r2[0].message)
        self.assertEqual(r2[0].severity, "低")

    def test_r3_serial(self):
        batch = [inv(f"20260100{t:04d}", "2026-08-01", Decimal("100"), Decimal("113"),
                     seller="连号供应商", tax=Decimal("13")) for t in (1001, 1002, 1003)]
        r3 = [x for x in run_rules(batch, CFG) if x.rule_id == "R3"]
        self.assertEqual(len(r3), 3)  # 窗口内 3 张全标
        self.assertEqual(r3[0].confidence, "疑似")

    def test_r1_missing_date_not_duplicate(self):
        # 里程碑评审 None 短路：票号相同但开票日期缺失的票，不得撞 key 误判"重复"
        batch = [
            inv("1001", "", Decimal("100"), Decimal("113"), tax=Decimal("13")),
            inv("1001", "", Decimal("100"), Decimal("113"), tax=Decimal("13")),
        ]
        r1 = [x for x in run_rules(batch, CFG) if x.rule_id == "R1"]
        self.assertTrue(all(x.severity == "低" for x in r1))
        self.assertTrue(all(x.confidence == "疑似" for x in r1))
        self.assertFalse(any(x.severity == "高" for x in r1))

    def test_r3_red_invoice_excluded(self):
        # 里程碑评审：红冲票不参与连号判定
        batch = [inv(f"20260100{t:04d}", "2026-08-01", Decimal("100"), Decimal("113"),
                     seller="连号供应商", tax=Decimal("13"),
                     invoice_type="红字专票") for t in (1001, 1002, 1003)]
        r3 = [x for x in run_rules(batch, CFG) if x.rule_id == "R3"]
        self.assertFalse(any(x.severity == "低" for x in r3))

    def test_r3_cross_day_not_flagged(self):
        # 里程碑评审口径收敛：同供应商跨开票日的连号不提示（同日批量开票才构成拆分嫌疑）
        batch = [
            inv("202601000001", "2026-08-01", Decimal("100"), Decimal("113"), seller="连号供应商", tax=Decimal("13")),
            inv("202601000002", "2026-08-05", Decimal("100"), Decimal("113"), seller="连号供应商", tax=Decimal("13")),
            inv("202601000003", "2026-08-09", Decimal("100"), Decimal("113"), seller="连号供应商", tax=Decimal("13")),
        ]
        r3 = [x for x in run_rules(batch, CFG) if x.rule_id == "R3"]
        self.assertFalse(any(x.severity == "低" for x in r3))

    def test_rule_states(self):
        # 里程碑评审三态化：命中/未命中/未执行 必须显式返回
        from app.rules import run_rules_with_states
        batch = [
            NormalizedInvoice(invoice_no="1001", invoice_type="专票", issue_date="2026-08-01",
                              amount=Decimal("100"), tax=Decimal("13"), total=Decimal("113"),
                              buyer_name="示例科技有限公司", buyer_taxid="91310000MA1FL1XXXX",
                              seller_name="s", category=None),  # 类别缺失 → R4 未执行
            NormalizedInvoice(invoice_no="1001", invoice_type="专票", issue_date="2026-08-01",
                              amount=Decimal("100"), tax=Decimal("13"), total=Decimal("113"),
                              buyer_name="示例科技有限公司", buyer_taxid="91310000MA1FL1XXXX",
                              seller_name="s", category=None),
        ]
        _, states = run_rules_with_states(batch, CFG)
        self.assertEqual(states["R1"], "命中")
        self.assertIn(states["R2"], ("命中", "未命中"))
        self.assertEqual(states["R4"], "未执行（缺少报销类别）")  # 类别未提供
        self.assertEqual(states["R3"], "未命中")

    def test_r3_dirty_tail_no_crash(self):
        # 票号不可解析 → 不崩溃、只出低危提示（H1）
        batch = [
            inv("abc", "2026-08-01", Decimal("100"), Decimal("113"), seller="脏票号", tax=Decimal("13")),
            inv("", "2026-08-01", Decimal("100"), Decimal("113"), seller="脏票号", tax=Decimal("13")),
        ]
        f = run_rules(batch, CFG)
        r3 = [x for x in f if x.rule_id == "R3"]
        self.assertTrue(all(x.severity == "低" for x in r3))
        # 其他规则仍正常产出（隔离生效，H8）
        self.assertTrue(any(x.rule_id == "R1" for x in f))

    def test_r4_overlimit(self):
        batch = [inv("1001", "2026-08-01", Decimal("6000"), Decimal("6780"),
                     category="差旅", tax=Decimal("780"))]
        r4 = [x for x in run_rules(batch, CFG) if x.rule_id == "R4"]
        self.assertEqual(len(r4), 1)
        # 未超限额不误报
        batch2 = [inv("1002", "2026-08-01", Decimal("100"), Decimal("113"),
                      category="差旅", tax=Decimal("13"))]
        self.assertFalse([x for x in run_rules(batch2) if x.rule_id == "R4"])

    def test_r6_concentrated(self):
        batch = [inv(f"c{t:04d}", "2026-08-01", Decimal("100"), Decimal("113"),
                     seller="集中供应商", tax=Decimal("13")) for t in range(1, 7)]
        r6 = [x for x in run_rules(batch, CFG) if x.rule_id == "R6"]
        self.assertEqual(len(r6), 6)

    def test_r7_date_anomaly(self):
        batch = [
            inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                reimburse="2026-07-01", tax=Decimal("13")),   # 开票晚于报销
            inv("1002", "2025-01-01", Decimal("100"), Decimal("113"),
                reimburse="2026-08-01", tax=Decimal("13")),   # 跨期 >365 天
            inv("1003", "2026-08-01", Decimal("100"), Decimal("113"),
                reimburse="2026-08-10", tax=Decimal("13")),   # 正常
        ]
        r7 = [x for x in run_rules(batch, CFG) if x.rule_id == "R7"]
        self.assertEqual(len(r7), 2)
        self.assertEqual(r7[0].confidence, "确定")

    def test_batch_is_required(self):
        # 跨票规则：单张调用不会命中重复/连号（架构验证：/review 必须整批）
        one = [inv("1001", "2026-08-01", Decimal("100"), Decimal("113"), tax=Decimal("13"))]
        self.assertFalse([x for x in run_rules(one) if x.rule_id == "R1"])
        self.assertFalse([x for x in run_rules(one) if x.rule_id == "R3"])

    def test_sorting(self):
        batch = [
            inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                reimburse="2026-07-01", tax=Decimal("13")),   # R7 中/确定
            inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                reimburse="2026-07-01", tax=Decimal("13")),   # R1 高/确定
            inv("9000", "2026-08-01", Decimal("6000"), Decimal("6780"),
                category="差旅", tax=Decimal("780")),          # R4 中/疑似
        ]
        f = run_rules(batch, CFG)
        self.assertEqual(f[0].rule_id, "R1")  # 高严重度排最前


if __name__ == "__main__":
    unittest.main(verbosity=2)
