"""语料库驱动回归（P2 前置 v0.3，2026-09-23）。

加载 tests/corpus/ 下的数电票 XML 样本（真实票样已脱敏 + 官方 XBRL 实例 + 合成样本，
结构化元数据见 manifest.json），验证 parser 三方言（中文标签/国标拼音缩写/EInvoice 英文结构）
解析正确性，并按 manifest 绑定 SHA256（防文件被静默改动）。

DeepSeek 里程碑评审要求：测试升级为语料库驱动（≥3 省份/3 开票系统 + XBRL + 红冲 +
差额征税 + 多税率）。
- v0.2：3 省份（广东/江苏/北京）× 3 开票系统（标准税局系统/网约车平台/电子发票服务平台·网页开票）
  × 版本分叉（0.2/0.32/0.33）达成，44 测试全过。
- v0.3：官方 XBRL 实例×2（财政部推广应用版附件3）+ 合成样本×4（红冲/差额/多税率/拼音缩写方言）
  入库；覆盖项 XBRL/红冲/差额/多税率/中文方言/拼音方言达成（来源=official/synthetic）。
  真实票样每省≥2 份仍未达成（v1 硬门 open，登记 manifest.governance）。
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


# (文件名, 省份, 开票系统, 场景, 格式, 期望字段断言)
# format=invoice_xml：真实/合成票面 XML，断言完整字段；
# format=xbrl_instance：官方入账信息结构化数据，断言结构安全（parser 未支持 xbrli 解析，仅归档）。
CASES = [
    (
        "official-gd-special.xml",
        "广东",
        "标准税局系统",
        "蓝字专票·人力资源服务·6%（官方公开样例·真实票样）",
        "invoice_xml",
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
        "蓝字普票·交通·3%·含退款负行（Amount=-0.96 不含税 / TotaltaxIncludedAmount=-0.99 含税）（真实票样）",
        "invoice_xml",
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
    (
        "bj-platform-it.xml",
        "北京",
        "电子发票服务平台·网页开票",
        "蓝字专票·信息技术服务·6%·大额票（25万）·Version 0.33·含签名子树（真实票样）",
        "invoice_xml",
        {
            "invoice_no": "24110000000000100003",
            "issue_date": "2024-09-24",  # RequestTime(2024-09-24 14:16:05) 带时间戳格式 → 截断为日期
            "amount": "235891.26",
            "tax": "14153.48",
            "total": "250044.74",
            "seller_name": "示例科技有限公司",
            "buyer_name": "示例商贸有限公司",
            "invoice_type": "增值税专用发票",
        },
    ),
    (
        "official-einv-xbrl-special.xml",
        "广东",
        "标准税局系统",
        "官方 XBRL 实例·专票入账信息结构化数据（xbrli 根/einv 命名空间）",
        "xbrl_instance",
        {"invoice_no": "23440000000000100011"},  # 仅结构安全断言（元素名巧合可解析，不代表支持 XBRL）
    ),
    (
        "official-einv-xbrl-ordinary.xml",
        "广东",
        "标准税局系统",
        "官方 XBRL 实例·普票入账信息结构化数据（xbrli 根/einv 命名空间）",
        "xbrl_instance",
        {"invoice_no": "23440000000000100012"},
    ),
    (
        "synthetic/synthetic-red-letter-cn.xml",
        "北京",
        "合成（中文标签结构）",
        "红字发票·销货退回·负数金额（-2500.00/-150.00/-2650.00）·中文标签方言·含 EI390/EI391（合成）",
        "invoice_xml",
        {
            "invoice_no": "26110000000000100004",
            "issue_date": "2026-09-18",
            "amount": "-2500.00",
            "tax": "-150.00",
            "total": "-2650.00",
            "seller_name": "示例商贸有限公司",
            "buyer_name": "示例科技（北京）有限公司",
        },
    ),
    (
        "synthetic/synthetic-differential-einv.xml",
        "广东",
        "合成（EInvoice 结构，同构 gaode-js-taxi）",
        "差额征税·旅游服务·KCE=200.00·计税基础 800.00×6%=48.00·备注'差额征税：200.00。'（合成）",
        "invoice_xml",
        {
            "invoice_no": "26440000000000100005",
            "issue_date": "2026-09-10",
            "amount": "800.00",
            "tax": "48.00",
            "total": "848.00",
            "seller_name": "示例旅行社（广东）有限公司",
        },
    ),
    (
        "synthetic/synthetic-multirate-einv.xml",
        "江苏",
        "合成（EInvoice 结构，同构 gaode-js-taxi）",
        "多税率明细·一票三行 6%/9%/13%→600.00/63.00/663.00（合成）",
        "invoice_xml",
        {
            "invoice_no": "26320000000000100006",
            "issue_date": "2026-08-20",
            "amount": "600.00",
            "tax": "63.00",
            "total": "663.00",
            "seller_name": "示例软件（江苏）有限公司",
        },
    ),
    (
        "synthetic/synthetic-pinyin-abbrev.xml",
        "江苏",
        "合成（国标拼音缩写结构）",
        "拼音缩写方言·餐饮服务·3% 征收率（57.28/1.72/59.00）·FPHM/KPRQ/HJJE/HJSE/JSHJXX（合成）",
        "invoice_xml",
        {
            "invoice_no": "26320000000000100007",
            "issue_date": "2026-07-30",
            "amount": "57.28",
            "tax": "1.72",
            "total": "59.00",
            "seller_name": "示例生活服务（无锡）有限公司",
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
        """语料库 v0.1 门槛：所有样本 SHA256 必须回填（不允许 AUTO 占位入库）。"""
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
        """票面 XML（真实+合成）：字段级断言（语料库驱动回归核心）。
        XBRL 实例样本仅断言结构安全（见 test_xbrl_instances_archive）。"""
        for fname, province, system, scenario, fmt, expected in CASES:
            if fmt != "invoice_xml":
                continue
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
        """勾稽校验：金额+税额=价税合计（票面 XML 均应勾稽成立，无告警级异常）。"""
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
        """样本发票号码 20 位数字（数电票票号规则）。"""
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

    def test_synthetic_samples_marked(self):
        """合成样本必须显式标注（synthetic=true + source_tier=synthetic_schema_based + 构造依据），
        与真实票样可区分（DeepSeek 评审：不得静默冒充真实票）。"""
        for s in self.manifest["samples"]:
            if s.get("synthetic"):
                with self.subTest(file=s["file"]):
                    self.assertEqual(s["source_tier"], "synthetic_schema_based",
                                     f"{s['file']} 合成样本来源分级错误")
                    self.assertTrue(s.get("construction_basis"),
                                    f"{s['file']} 缺少构造依据 construction_basis")
                    self.assertNotIn("official_public_sample", s["source_tier"])

    def test_xbrl_instances_archive(self):
        """官方 XBRL 实例：结构安全归档断言。
        parser 尚未支持 xbrli 结构解析——本测试验证：
        ① parse_xml 不抛异常（元素名巧合可解析部分字段，不代表支持 XBRL）；
        ② manifest 明确标记 format=xbrl_instance + verification=official_sample；
        ③ 结构安全性：不因 XBRL 实例导致崩溃/误判为票面。"""
        for fname, *_ in [c for c in CASES if c[4] == "xbrl_instance"]:
            with self.subTest(fname=fname):
                sample = next(s for s in self.manifest["samples"] if s["file"] == fname)
                self.assertEqual(sample["format"], "xbrl_instance")
                self.assertEqual(sample["verification"], "official_sample")
                self.assertEqual(sample["source_tier"], "official_public_sample")
                data = (CORPUS_DIR / fname).read_bytes()
                inv = parse_xml(data)  # 不抛异常（结构安全）
                self.assertTrue(inv.invoice_no, f"[{fname}] 应至少解析出发票号码")

    def test_red_letter_negative_reconciliation(self):
        """红字合成样本：金额为负且勾稽成立（-2500 + -150 = -2650），
        明细负行/负数票面不破坏合计路径（DeepSeek M2 口径统一）。"""
        data = (CORPUS_DIR / "synthetic/synthetic-red-letter-cn.xml").read_bytes()
        inv = parse_xml(data)
        self.assertLess(inv.amount, 0)
        self.assertLess(inv.tax, 0)
        self.assertLess(inv.total, 0)
        self.assertFalse(any("勾稽异常" in w for w in inv.parse_warnings))


if __name__ == "__main__":
    unittest.main()
