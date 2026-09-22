"""语料库驱动回归（P2 前置 v0.1，2026-09-23）。

加载 tests/corpus/ 下"真实结构"的数电票 XML 票样（已脱敏，结构化元数据见 manifest.json），
验证 parser 第三方言（EInvoice 英文结构）解析正确性，并按 manifest 绑定 SHA256（防文件被静默改动）。

DeepSeek 里程碑评审要求：测试升级为语料库驱动（≥3 省份/3 开票系统 + XBRL + 红冲 +
差额征税 + 多税率）；当前 v0.1 覆盖广东（官方样例）+ 江苏（真实打车票含退款负行），
未达标项（省份/系统/XBRL/红冲/差额/多税率）登记在 manifest.governance.required_for_v1，待 v0.2+ 补齐。
"""
import hashlib
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser import parse_xml

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


def _load_manifest() -> dict:
    with (CORPUS_DIR / "manifest.json").open(encoding="utf-8") as f:
        return json.load(f)


# (文件名, 省份, 开票系统, 场景, 期望字段断言)
CASES = [
    (
        "official-gd-special.xml",
        "广东",
        "标准税局系统",
        "蓝字专票·人力资源服务·6%",
        {
            "invoice_no": "23440000000000100001",
            "issue_date": "2023-02-23",  # RequestTime 优先（财政部元素清单：开票日期=开票请求时间）
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
        "蓝字普票·交通·3%·含退款负行（Amount=-0.96 不含税 / TotaltaxIncludedAmount=-0.99 含税）",
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
    @classmethod
    def setUpClass(cls):
        cls.manifest = _load_manifest()

    def test_manifest_exists_and_valid(self):
        """manifest.json 必须存在且含版本/治理/样本清单（DeepSeek M1/M7）。"""
        self.assertIn("corpus_version", self.manifest)
        self.assertIn("samples", self.manifest)
        self.assertIn("required_for_v1", self.manifest.get("governance", {}))
        files_in_manifest = {s["file"] for s in self.manifest["samples"]}
        files_on_disk = {c[0] for c in CASES}
        self.assertEqual(files_in_manifest, files_on_disk,
                         "manifest 与测试用例文件集合不一致")

    def test_corpus_files_exist(self):
        """语料库文件必须存在（防目录被误删后测试静默跳过）。"""
        for fname, *_ in CASES:
            self.assertTrue((CORPUS_DIR / fname).exists(), f"语料缺失：{fname}")

    def test_manifest_sha256_filled(self):
        """语料库 v0.1 门槛：所有票样 SHA256 必须回填（不允许 AUTO 占位入库）。"""
        for s in self.manifest["samples"]:
            with self.subTest(file=s["file"]):
                self.assertNotEqual(s["sha256"], "AUTO", f"{s['file']} 哈希未回填")

    def test_corpus_sha256_bound(self):
        """manifest 记录的 SHA256 必须与磁盘文件一致（防静默篡改/替换）。"""
        for fname, *_ in CASES:
            with self.subTest(fname=fname):
                sample = next(s for s in self.manifest["samples"] if s["file"] == fname)
                data = (CORPUS_DIR / fname).read_bytes()
                actual = hashlib.sha256(data).hexdigest()
                self.assertEqual(actual, sample["sha256"],
                                 f"[{fname}] SHA256 与 manifest 不一致（文件被改动？）")

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
        """高德样例：明细行含负金额（退款），合计仍取 BasicInformation 路径值。"""
        data = (CORPUS_DIR / "gaode-js-taxi.xml").read_bytes()
        inv = parse_xml(data)
        self.assertEqual(str(inv.total), "39.54")
        self.assertEqual(str(inv.amount), "38.39")


if __name__ == "__main__":
    unittest.main()
