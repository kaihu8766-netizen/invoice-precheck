"""rules 自测：构造整批 NormalizedInvoice，验证 R1-R11 命中与不误报。"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import ItemDetail, NormalizedInvoice
from app.rules import RulesConfig, run_rules


def inv(no, date, amount, total, seller="北京华信办公用品有限公司",
        buyer="示例科技有限公司", taxid="91310000MA1FL1XXXX",
        category="办公", reimburse=None, tax=Decimal("0"),
        invoice_type="专票", is_red_letter=False, is_differential=False,
        items=None, differential_deduction=None, red_letter_blue_no="",
        raw_fields=None):
    return NormalizedInvoice(
        invoice_no=no, invoice_type=invoice_type, issue_date=date,
        amount=amount, tax=tax, total=total,
        buyer_name=buyer, buyer_taxid=taxid, seller_name=seller,
        category=category, reimburse_date=reimburse,
        is_red_letter=is_red_letter, is_differential=is_differential,
        items=items or [], differential_deduction=differential_deduction,
        red_letter_blue_no=red_letter_blue_no, raw_fields=raw_fields or {},
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
        # 空值不产出"确定"级结论（M15）：税号缺失+名称缺失 → 中·可疑（RV-43 防 fail-open，仍非确定级）
        batch2 = [NormalizedInvoice(invoice_no="x1", invoice_type="普票", issue_date="2026-08-01",
                                    amount=Decimal("1"), tax=Decimal("0"), total=Decimal("1"),
                                    buyer_name="", buyer_taxid="", seller_name="s")]
        r2b = [x for x in run_rules(batch2, CFG) if x.rule_id == "R2"]
        self.assertTrue(all(x.severity in ("低", "中") for x in r2b))

    def test_r2_no_config_skips(self):
        # 红线保护：未配置企业主体 → R2 不误报（H7）
        batch = [inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="任意公司", taxid="999999999999999999", tax=Decimal("13"))]
        f = run_rules(batch)  # 默认 config：company 为 None（必须，测未配置场景）
        r2 = [x for x in f if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertIn("未配置企业主体", r2[0].message)
        self.assertEqual(r2[0].severity, "低")

    # ---------- RV-42 重写：税号优先分级（F-20260923-04） ----------

    def test_r2_taxid_match_name_match_no_finding(self):
        # 税号一致 + 名称一致 → 通过（无 R2 finding）
        batch = [inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技有限公司", taxid="91310000MA1FL1XXXX", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(r2, [])

    def test_r2_taxid_match_name_mismatch_low(self):
        # RV-42：税号一致但名称不一致 → 低风险"名称差异"，不报高
        batch = [inv("1002", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技公司（简称）", taxid="91310000MA1FL1XXXX", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "低")
        self.assertIn("名称", r2[0].message)

    def test_r2_taxid_mismatch_high_even_name_match(self):
        # RV-42：税号不一致（即使名称一致）→ 高风险"抬头/税号不符"
        batch = [inv("1003", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技有限公司", taxid="999999999999999999", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "高")

    def test_r2_taxid_missing_low(self):
        # RV-42：票面税号缺失 → 低风险"无法校验"
        batch = [inv("1004", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技有限公司", taxid="", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "低")
        self.assertIn("缺失", r2[0].message)

    def test_r2_fullwidth_norm_pass(self):
        # RV-42：全半角/大小写归一化 → 全角税号与配置一致 → 通过
        from app.config_store import normalize_tax_id
        fullwidth = normalize_tax_id("９１３１００００MA1FL1XXXX")
        self.assertEqual(fullwidth, "91310000MA1FL1XXXX")
        batch = [inv("1005", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技有限公司", taxid="９１３１００００MA1FL1XXXX", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(r2, [])

    def test_r2_name_missing_taxid_match_low(self):
        # RV-42：税号一致但名称缺失 → 低风险提示（不报高）
        batch = [inv("1006", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="", taxid="91310000MA1FL1XXXX", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "低")

    # ---------- RV-43 补档：占位符税号 / fail-open 修复 / env 成套 ----------

    def test_r2_placeholder_taxid_name_ok_low(self):
        # RV-43：税号缺失/占位符 + 名称一致 → 低·无法校验（不误报高）
        for ph in ["0" * 18, "00000000000000000000", "N/A", "-", "无"]:
            batch = [inv("p1", "2026-08-01", Decimal("100"), Decimal("113"),
                         buyer="示例科技有限公司", taxid=ph, tax=Decimal("13"))]
            r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
            self.assertEqual(len(r2), 1, f"占位符 {ph}")
            self.assertEqual(r2[0].severity, "低", f"占位符 {ph}")

    def test_r2_placeholder_taxid_name_mismatch_medium(self):
        # RV-43 补档：税号缺失/占位符 + 名称不一致 → 中·可疑（不得 fail-open 掉到低）
        for ph in ["00000000000000000000", "-"]:
            batch = [inv("p2", "2026-08-01", Decimal("100"), Decimal("113"),
                         buyer="完全无关公司", taxid=ph, tax=Decimal("13"))]
            r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
            self.assertEqual(len(r2), 1, f"占位符 {ph}")
            self.assertEqual(r2[0].severity, "中", f"占位符 {ph} 名称不符应中风险")

    def test_r2_short_taxid_invalid(self):
        # RV-43：短税号（<8 位）视为无效 → 不参与高风险比对
        batch = [inv("p3", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技有限公司", taxid="123", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "低")

    def test_config_env_pairing(self):
        # RV-43：env 必须成套，只设一个时忽略两个（防混合主体）
        import importlib, os
        from unittest import mock
        os.environ.pop("INVOICE_COMPANY_NAME", None)
        os.environ.pop("INVOICE_COMPANY_TAXID", None)
        with mock.patch.dict(os.environ, {"INVOICE_COMPANY_NAME": "仅名字公司"}, clear=False):
            from app.config_store import load_config as lc
            # 直接测 load_config 内部逻辑（env 单设 → 不覆盖）
            # 用子进程隔离太重，改为验证：单设 env 时 load_config 仍返回文件/默认（无 env 值）
            cfg = lc()
            ent = cfg["company_entities"][0]
            self.assertEqual(ent["name"], "")  # 单设 env 被忽略
        with mock.patch.dict(os.environ, {"INVOICE_COMPANY_NAME": "成对公司", "INVOICE_COMPANY_TAXID": "91310000MA1FL1XXXX"}, clear=False):
            from app.config_store import load_config as lc2
            cfg = lc2()
            ent = cfg["company_entities"][0]
            self.assertEqual(ent["name"], "成对公司")
            self.assertEqual(ent["tax_id"], "91310000MA1FL1XXXX")

    def test_config_atomic_save(self):
        # RV-43：原子写（tmp+replace）——保存后文件可读且内容完整
        import json, tempfile
        from app import config_store
        tmpdir = tempfile.mkdtemp()
        old_dir, old_path = config_store.DATA_DIR, config_store.CONFIG_PATH
        config_store.DATA_DIR = Path(tmpdir)
        config_store.CONFIG_PATH = Path(tmpdir) / "config.json"
        try:
            cfg = {"company_entities": [{"id": "default", "name": "原子公司", "tax_id": "91310000MA1FL1XXXX", "enabled": True}], "r2": {}}
            config_store.save_config(cfg)
            loaded = json.loads(Path(tmpdir, "config.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded["company_entities"][0]["name"], "原子公司")
            self.assertFalse((Path(tmpdir) / "config.json.tmp").exists(), "临时文件应已清理")
        finally:
            config_store.DATA_DIR, config_store.CONFIG_PATH = old_dir, old_path

    # ---------- RV-44：税号白名单/长度区间/占位词边界 ----------

    def test_is_plausible_tax_id_matrix(self):
        from app.config_store import is_plausible_tax_id
        cases = [
            ("", False),                      # 空
            ("91310000MA1FL1XXXX", True),     # 18 位统一码（标准）
            ("91310000123456789", True),      # 15 位老税号
            ("１２３４５６７８９０１", False),  # 全角数字（归一后长度不足 15）
            ("91310000ma1fl1xxxx", True),     # 小写字母 → 归一后大写 → 有效
            (" 91310000MA1FL1XXXX ", True),   # 前后空白 → 归一后有效
            ("9131-0000-MA1F-L1XX", False),   # 连字符 → 归一后非纯字母数字 → 无效
            ("1234567", False),               # 短号
            ("0" * 18, False),                # 全 0 占位
            ("X" * 18, False),                # 全 X 占位
            ("N/A", False),                   # 占位词
            ("暂无", False),
            ("91310000MA1FL1XX0X", True),     # 含 X 的混合合法（不误杀）
        ]
        for raw, want in cases:
            got = is_plausible_tax_id(raw)
            self.assertEqual(got, want, f"is_plausible_tax_id({raw!r}) 应为 {want}")

    def test_r2_taxid_valid_mismatch_name_missing_high(self):
        # RV-44 固化：税号有效且不一致 + 名称缺失 → 高（不得因名称缺失降级）
        batch = [inv("p4", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="", taxid="91310000MA1FL1XXXX", tax=Decimal("13"))]
        # CFG 税号即 91310000MA1FL1XXXX → 配置另一有效税号触发不一致
        from app.rules import RulesConfig
        cfg2 = RulesConfig(company_name="示例科技有限公司", company_taxid="91440300MA5XXXXX2A")
        r2 = [x for x in run_rules(batch, cfg2) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].severity, "高")

    def test_r2_company_suffix_variant_documented(self):
        # RV-44 固化：不做"有限公司/有限责任公司"同义合并（文档声明）→ 名称差异 → 低（税号一致）
        batch = [inv("p5", "2026-08-01", Decimal("100"), Decimal("113"),
                     buyer="示例科技有限责任公司", taxid="91310000MA1FL1XXXX", tax=Decimal("13"))]
        r2 = [x for x in run_rules(batch, CFG) if x.rule_id == "R2"]
        self.assertEqual(len(r2), 1)
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

    # ---------- P0-8 金额异常类型化（R8） ----------

    def test_r8_negative_non_red(self):
        """负数金额且非红冲 → 高危（疑似异常）。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("-100"), Decimal("-113"),
                           tax=Decimal("-13"), is_red_letter=False)], CFG)
        r8 = [x for x in f if x.rule_id == "R8"]
        self.assertEqual(len(r8), 1)
        self.assertEqual(r8[0].severity, "高")
        self.assertIn("负数非红冲", r8[0].message)

    def test_r8_red_letter_negative_passes(self):
        """红字发票负数 → 不报（红冲负数合法，避免误报）。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("-2500"), Decimal("-2650"),
                           tax=Decimal("-150"), is_red_letter=True)], CFG)
        r8 = [x for x in f if x.rule_id == "R8"]
        self.assertEqual(len(r8), 0)

    def test_r8_differential_pending(self):
        """差额征税票：R8 不再输出占位声明（C1 移交 R9 专项）；
        差额票勾稽成立 + KCE 存在 → R8/R9 均不报。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("800"), Decimal("848"),
                           tax=Decimal("48"), is_differential=True,
                           differential_deduction=Decimal("200.00"),
                           raw_fields={"Remark": "差额征税：200.00。"})], CFG)
        self.assertFalse([x for x in f if x.rule_id == "R8"])
        self.assertFalse([x for x in f if x.rule_id == "R9"],
                         "正常差额票（KCE 存在且备注一致）不应命中 R9")

    def test_r8_reconcile_defense(self):
        """勾稽不符 → 高危（确定性）（parser 已拦截，规则层独立防御复核）。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("100"), Decimal("120"),
                           tax=Decimal("13"))], CFG)
        r8 = [x for x in f if x.rule_id == "R8"]
        self.assertEqual(len(r8), 1)
        self.assertEqual(r8[0].severity, "高")
        self.assertEqual(r8[0].confidence, "确定")
        self.assertIn("勾稽不符", r8[0].message)

    def test_r8_absurd_total(self):
        """单票超合理阈值 → 低危疑似（阈值已校准 09-23：100万→3000万，31张真实票分布）。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("34999999"), Decimal("35000000"),
                           tax=Decimal("1"))], CFG)
        r8 = [x for x in f if x.rule_id == "R8"]
        self.assertEqual(len(r8), 1)
        self.assertEqual(r8[0].severity, "低")
        self.assertIn("合理阈值", r8[0].message)

    def test_r8_normal_passes(self):
        """正常票：勾稽成立 + 正数 + 非差额 + 未超阈值 → 无 R8 finding。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                           tax=Decimal("13"))], CFG)
        self.assertFalse([x for x in f if x.rule_id == "R8"])

    # ---------- F-05 规则校准（RV-48） ----------

    def test_rules_payload_valid_partial(self):
        from app.config_store import validate_rules_payload
        ok, err = validate_rules_payload({"serial_tail_len": 6, "category_limits": {"travel": 8000}})
        self.assertIsNone(err)
        self.assertEqual(ok["serial_tail_len"], 6)
        self.assertEqual(ok["category_limits"]["travel"], 8000)
        self.assertEqual(ok["category_limits"]["other"], 5000, "未提交字段回退默认")

    def test_rules_payload_invalid_rejected(self):
        from app.config_store import validate_rules_payload
        for bad in ({"serial_tail_len": 99}, {"default_limit": -5}, {"max_plausible_total": 3000.5}):
            ok, err = validate_rules_payload(bad)
            self.assertIsNone(ok, f"应拒绝 {bad}")
            self.assertTrue(err)
        # 原子性：部分合法+部分非法 → 整体拒绝
        ok, err = validate_rules_payload({"serial_tail_len": 6, "serial_max_gap": 99})
        self.assertIsNone(ok)
        # 未知字段拒绝
        ok, err = validate_rules_payload({"bad_key": 1})
        self.assertIsNone(ok)
        self.assertIn("未知", err)

    def test_rules_config_override_effective(self):
        from app.config_store import to_rules_config
        cfg = {"company_entities": [{"name": "示例", "tax_id": "91310000MA1FL1XXXX", "enabled": True}],
               "rules": {"serial_tail_len": 7, "category_limits": {"travel": 8000}, "max_plausible_total": 50000000}}
        rc = to_rules_config(cfg)
        self.assertEqual(rc.serial_tail_len, 7)
        self.assertEqual(rc.category_limits["差旅"], 8000)  # 英文存储 → 中文规则 key
        self.assertEqual(rc.category_limits["办公"], 2000)
        self.assertEqual(rc.max_plausible_total, Decimal("50000000"))

    def test_rules_threshold_changes_rule_hit(self):
        # 自定义连号阈值生效：tail_len=2 时 1001/1002 触发 R3
        from app.config_store import to_rules_config
        cfg = {"company_entities": [], "rules": {"serial_tail_len": 2, "serial_max_gap": 2, "serial_min_count": 2}}
        cfg2 = to_rules_config(cfg)
        batch = [inv(f"20260100{t:04d}", "2026-08-01", Decimal("100"), Decimal("113"),
                     seller="连号供应商", tax=Decimal("13")) for t in (1001, 1002)]
        r3 = [x for x in run_rules(batch, cfg2) if x.rule_id == "R3"]
        self.assertEqual(len(r3), 2, "自定义 tail_len=2 应命中连号")

    # ---------- RV-50 复核修复（阈值区间放宽/custom 精确判定/gap 语义） ----------

    def test_rules_payload_lower_bounds_relaxed(self):
        """RV-50：类别限额下限放宽到 1（出租车/定额票可配置），serial_min_count 上限 500。"""
        from app.config_store import validate_rules_payload, RULES_WHITELIST
        ok, err = validate_rules_payload({"category_limits": {"transport": 1}})
        self.assertIsNone(err)
        ok, err = validate_rules_payload({"serial_min_count": 500})
        self.assertIsNone(err)
        ok, err = validate_rules_payload({"category_limits": {"transport": 0}})
        self.assertIsNotNone(err, "0 限额应被拒绝（下限 1）")

    def test_is_default_rules_partial_default_not_custom(self):
        """RV-50 意见7：提交的值全部等于默认（含空对象/部分字段）→ 不算自定义。"""
        from app.config_store import is_default_rules, DEFAULT_RULES
        self.assertTrue(is_default_rules({}), "空 rules 应视为默认")
        self.assertTrue(is_default_rules({"rules": {}}), "空对象不误标 custom")
        self.assertTrue(is_default_rules({"rules": {"serial_tail_len": DEFAULT_RULES["serial_tail_len"]}}))
        self.assertFalse(is_default_rules({"rules": {"serial_tail_len": 6}}))

    def test_serial_gap_zero_semantics(self):
        """RV-50 意见3：gap=0 聚合完全同号票（差=0），不误伤差 1 的连续票。"""
        f = run_rules([inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                           tax=Decimal("13"), seller="北京华信办公用品有限公司"),
                       inv("1001", "2026-08-01", Decimal("80"), Decimal("90.4"),
                           tax=Decimal("10.4"), seller="北京华信办公用品有限公司"),
                       inv("1001", "2026-08-01", Decimal("60"), Decimal("67.8"),
                           tax=Decimal("7.8"), seller="北京华信办公用品有限公司")],
                      RulesConfig(serial_tail_len=4, serial_max_gap=0, serial_min_count=3))
        r3 = [x for x in f if x.rule_id == "R3"]
        self.assertEqual(len(r3), 3, "同号 3 张窗口内全标（同号聚合）")
        # 差 1 的连续票在 gap=0 下不应聚合（不是同号）
        f2 = run_rules([inv("1001", "2026-08-01", Decimal("100"), Decimal("113"),
                            tax=Decimal("13"), seller="北京华信办公用品有限公司"),
                        inv("1002", "2026-08-01", Decimal("80"), Decimal("90.4"),
                            tax=Decimal("10.4"), seller="北京华信办公用品有限公司"),
                        inv("1003", "2026-08-01", Decimal("60"), Decimal("67.8"),
                            tax=Decimal("7.8"), seller="北京华信办公用品有限公司")],
                       RulesConfig(serial_tail_len=4, serial_max_gap=0, serial_min_count=3))
        r3b = [x for x in f2 if x.rule_id == "R3"]
        self.assertEqual(len(r3b), 0, "gap=0 下连续票（差1）不应命中连号")


if __name__ == "__main__":
    unittest.main(verbosity=2)
