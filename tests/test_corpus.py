"""语料库驱动回归（P2 前置 v0.1，2026-09-23）。

加载 tests/corpus/ 下"真实结构"的数电票 XML 票样（已脱敏，元数据见 corpus/README.md），
验证 parser 第三方言（EInvoice 英文结构）解析正确性。

DeepSeek 里程碑评审要求：测试升级为语料库驱动（≥3 省份/3 开票系统 + XBRL + 红冲 +
差额征税 + 多税率）；当前 v0.1 覆盖广东（官方样例）+ 江苏（真实打车票含退款负行）。
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser import parse_xml

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"

# (文件名, 省份, 开票系统, 场景, 期望字段断言)
CASES = [
    (
        "official-gd-special.xml",
        "广东",
        "标准税局系统",
        "蓝字专票·人力资源服务·6%",
        {
            "invoice_no": "23440000000000100001",
            "issue_date": "2023-02-23",  # IssueTime 带时间 → 截断为日期
            "amount": "2.00",
            "tax": "0.12",
            "total": "2.12",
            "seller_name": "示例制造集团有限公司",
            "buyer_name": "示例商贸有限公司",
            "invoice_type": "增值税专用发票",  # GeneralOrSpecialVAT.LabelName
        },
    ),
    (
        "gaode-js-taxi.xml",
        "江苏",
        "网约车平台",
        "蓝字普票·交通·3%·含退款负行",
        {
            "invoice_no": "26320000000000100001",
            "issue_date": "2026-07-18",
            "amount": "38.39",
            "tax": "1.15",
            "total": "39.54",
            "seller_name": "示例出行服务有限公司示例分公司",
            "buyer_name": "xxxxxx有限责任公司",
        },
    ),
]


class TestCorpusRealSamples(unittest.TestCase):
    def test_corpus_files_exist(self):
        """语料库文件必须存在（防目录被误删后测试静默跳过）。"""
        for fname, *_ in CASES:
            self.assertTrue((CORPUS_DIR / fname).exists(), f"语料缺失：{fname}")

    def test_corpus_parse_and_fields(self):
        """真实结构票样：字段级断言（语料库驱动回归核心）。"""
        for fname, province, system, scenario, expected in CASES:
            with self.subTest(fname=fname):
                data = (CORPUS_DIR / fname).read_bytes()
                inv = parse_xml(data)
                for key, want in expected.items():
                    got = getattr(inv, key)
                    if key in ("amount", "tax", "total"):
                        self.assertEqual(str(got), want,
                                         f"[{fname}] {key} 应为 {want}，实为 {got}")
                    else:
                        self.assertEqual(got, want,
                                         f"[{fname}] {key} 应为 {want}，实为 {got}")

    def test_corpus_reconciliation(self):
        """勾稽校验：金额+税额=价税合计（两票样均应勾稽成立，无告警级异常）。"""
        for fname, *_ in CASES:
            with self.subTest(fname=fname):
                data = (CORPUS_DIR / fname).read_bytes()
                inv = parse_xml(data)
                self.assertLessEqual(abs(inv.amount + inv.tax - inv.total),
                                     Decimal("0.01"),
                                     f"[{fname}] 勾稽异常：{inv.amount}+{inv.tax}≠{inv.total}")
                self.assertFalse(
                    any("勾稽异常" in w for w in inv.parse_warnings),
                    f"[{fname}] 不应有勾稽异常告警：{inv.parse_warnings}",
                )

    def test_corpus_invoice_no_20digits(self):
        """真实票样发票号码 20 位数字（数电票票号规则）。"""
        for fname, *_ in CASES:
            with self.subTest(fname=fname):
                data = (CORPUS_DIR / fname).read_bytes()
                inv = parse_xml(data)
                self.assertTrue(inv.invoice_no.isdigit() and len(inv.invoice_no) == 20,
                                f"[{fname}] 票号应 20 位数字：{inv.invoice_no}")
                self.assertFalse(any("发票号码位数" in w for w in inv.parse_warnings))

    def test_corpus_raw_fields_masked(self):
        """raw_fields 税号必须脱敏（防 PII 外泄：中英文键都覆盖）。"""
        for fname, *_ in CASES:
            with self.subTest(fname=fname):
                data = (CORPUS_DIR / fname).read_bytes()
                inv = parse_xml(data)
                for k, v in inv.raw_fields.items():
                    if "IdNum" in k or "识别号" in k or "税号" in k:
                        self.assertIn("****", v, f"[{fname}] {k} 未脱敏：{v}")

    def test_gaode_negative_line_does_not_break_total(self):
        """高德样例：明细行含负金额（退款 -0.96），合计仍取 BasicInformation 值。"""
        data = (CORPUS_DIR / "gaode-js-taxi.xml").read_bytes()
        inv = parse_xml(data)
        self.assertEqual(str(inv.total), "39.54")
        self.assertEqual(str(inv.amount), "38.39")


if __name__ == "__main__":
    unittest.main()
