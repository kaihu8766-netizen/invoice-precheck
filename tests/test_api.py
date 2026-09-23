"""API 集成测试：上传 XML → /review → 一页风险报告（P1 完整闭环）。

场景：正常票 / 重复票（R1）/ 连号票（R3）/ 差旅超标（R4）/ PDF 拒绝（failed 列表）。
鉴权（D 阶段）：业务端点需 X-API-Key；公开路径（/、/healthz）免鉴权。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import DEV_API_KEY, MAX_FILE_BYTES, app
from app.report import DISCLAIMER

# 业务调用默认携带开发 key；匿名客户端用于鉴权用例
client = TestClient(app, headers={"X-API-Key": DEV_API_KEY})
anon = TestClient(app)

NS = "urn:cn:gov:etax:2021:invoice"


def xml(no: str, date: str, amount: str, tax: str, total: str,
        seller: str = "北京华信办公用品有限公司", buyer: str = "示例科技有限公司",
        category: str | None = None, reimburse: str | None = None) -> bytes:
    cat = f"<类别>{category}</类别>" if category else ""
    rmb = f"<报销日期>{reimburse}</报销日期>" if reimburse else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<发票 xmlns="{NS}">
  <发票号码>{no}</发票号码>
  <开票日期>{date}</开票日期>
  <发票类型>增值税专用发票</发票类型>
  <购买方><购买方名称>{buyer}</购买方名称><购买方纳税人识别号>91310000MA1FL1XXXX</购买方纳税人识别号></购买方>
  <销售方><销售方名称>{seller}</销售方名称><销售方纳税人识别号>91110108XXXXXXXXXX</销售方纳税人识别号></销售方>
  <合计金额>{amount}</合计金额><合计税额>{tax}</合计税额><价税合计>{total}</价税合计>
  {cat}{rmb}
</发票>
""".encode()


def files(*items):
    return [("files", (name, content, "application/xml")) for name, content in items]


class TestAPI(unittest.TestCase):
    def test_index_and_healthz_public(self):
        """公开路径免鉴权：首页 + 探活。"""
        r = anon.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("发票合规预审", r.text)
        self.assertIn(DISCLAIMER, r.text)
        r = anon.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_business_endpoints_require_key(self):
        """无 key / 错误 key → 401（业务端点全部上锁）。"""
        for path, kwargs in (
            ("/parse", dict(files=[("file", ("a.xml", xml("26003300000000000001", "20260801", "100.00", "13.00", "113.00"), "application/xml"))])),
            ("/review", dict(files=files(("a.xml", xml("26003300000000000001", "20260801", "100.00", "13.00", "113.00"))))),
        ):
            r = anon.post(path, **kwargs)
            self.assertEqual(r.status_code, 401, path)
            self.assertIn("API Key", r.json()["detail"])
            r = TestClient(app, headers={"X-API-Key": "wrong-key"}).post(path, **kwargs)
            self.assertEqual(r.status_code, 401, f"{path} with wrong key")
            self.assertIn("API Key", r.json()["detail"])

    def test_index_serves_frontend(self):
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("发票合规预审", r.text)
        # 免责文案单一来源：前端页面必须包含后端 DISCLAIMER 原文（M-4）
        self.assertIn(DISCLAIMER, r.text)

    def test_parse_single(self):
        r = client.post("/parse", files=[("file", ("a.xml", xml("26003300000000000001", "20260801", "100.00", "13.00", "113.00"), "application/xml"))])
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["invoice_no"], "26003300000000000001")
        self.assertEqual(d["total"], "113.00")

    def test_parse_rejects_pdf(self):
        r = client.post("/parse", files=[("file", ("bad.pdf", b"%PDF-1.7 junk", "application/pdf"))])
        self.assertEqual(r.status_code, 400)
        self.assertIn("不支持的输入类型", r.json()["detail"])

    def test_review_full_flow(self):
        resp = client.post("/review", files=files(
            ("normal.xml", xml("26003300000000008801", "20260801", "100.00", "13.00", "113.00", seller="普通供应商")),
            ("dup.xml", xml("26003300000000008801", "20260801", "100.00", "13.00", "113.00", seller="普通供应商")),   # R1 重复
            ("serial1.xml", xml("26004410000000000001", "20260802", "50.00", "6.50", "56.50", seller="连号供应商")),
            ("serial2.xml", xml("26004410000000000002", "20260802", "60.00", "7.80", "67.80", seller="连号供应商")),
            ("serial3.xml", xml("26004410000000000003", "20260802", "70.00", "9.10", "79.10", seller="连号供应商")),
            ("over.xml", xml("26003300000000009902", "20260803", "6000.00", "780.00", "6780.00", seller="普通供应商", category="差旅")),
            ("bad.pdf", b"%PDF-1.7 junk", ),
        ))
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["summary"]["total_count"], 6)
        self.assertEqual(d["summary"]["failed_count"], 1)
        self.assertEqual(d["summary"]["review_count"], 5)  # 重复×2 + 连号×3（票号 20 位，无格式告警）
        self.assertEqual(d["summary"]["no_finding_count"], 1)  # 差旅票（类别未提供，R4 不误报）→ 未见规则命中
        self.assertIn("batch_id", d)
        self.assertIn("generated_at", d)
        self.assertIn("rules", d)
        rids = {f["rule_id"] for f in d["findings"]}
        self.assertIn("R1", rids)
        self.assertIn("R3", rids)
        # 规则名契约（P1② 规则名业务化）：findings 的 rule_id 必须被响应 rules 覆盖（业务名从 rules 解析）
        rule_ids_in_rules = {r["rule_id"] for r in d["rules"]}
        self.assertTrue(rids.issubset(rule_ids_in_rules),
                        f"findings rule_id 未被 rules 覆盖: {rids - rule_ids_in_rules}")
        for r in d["rules"]:
            self.assertTrue(r.get("name"), f"rules[{r['rule_id']}] 缺业务名")
        # 单 XML 上传不携带报销类别 → R4 不判定（防误报；类别通道在 P3）
        self.assertNotIn("R4", rids)
        self.assertEqual(len(d["failed"]), 1)
        self.assertIn("不支持的输入类型", d["failed"][0]["error"])
        # 报告架构（DeepSeek 信息架构）：摘要/清单/明细/规则版本/免责
        for key in ("summary", "findings", "invoices", "failed", "ruleset_version", "scope_note", "disclaimer"):
            self.assertIn(key, d)
        self.assertTrue(d["disclaimer"])
        # 规则三态（里程碑评审）：未执行规则必须可见，禁止沉默暗示合规
        self.assertIn("rules_summary", d)
        rstates = {r["rule_id"]: r["state"] for r in d["rules"]}
        self.assertIn("未执行", rstates["R2"])          # 未配置企业主体 → 未执行
        self.assertIn("未执行", rstates["R4"])          # 单 XML 无报销类别 → 未执行
        self.assertEqual(rstates["R1"], "命中")
        self.assertGreaterEqual(d["rules_summary"]["enabled_count"], 6)

    def test_review_all_failed_short_circuit(self):
        # 0 票批次：total_count=0，报告不含"未发现风险点"式合规结论（H-5）
        resp = client.post("/review", files=files(("bad.pdf", b"%PDF-1.7 junk")))
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["summary"]["total_count"], 0)
        self.assertEqual(d["summary"]["failed_count"], 1)
        self.assertEqual(len(d["failed"]), 1)

    def test_review_oversize_file(self):
        # 单文件超限 → 记入 failed，不 OOM（H-3）
        big = b"<a>" + b"x" * (MAX_FILE_BYTES + 1)
        resp = client.post("/review", files=files(("big.xml", big)))
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["summary"]["failed_count"], 1)
        self.assertIn("上限", d["failed"][0]["error"])

    def test_review_bad_file_does_not_kill_batch(self):
        # 坏文件混入正常批次 → 200，正常票出报告（H-4 隔离）
        resp = client.post("/review", files=files(
            ("normal.xml", xml("26003300000000000001", "20260801", "100.00", "13.00", "113.00")),
            ("garbage.xml", b"\xff\xfe not xml at all \x00\x01"),
            ("pdf.pdf", b"%PDF-1.7 junk"),
        ))
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["summary"]["total_count"], 1)
        self.assertEqual(d["summary"]["failed_count"], 2)
        # 正常票仍出报告
        self.assertEqual(d["invoices"][0]["invoice_no"], "26003300000000000001")

    def test_parse_injection_string_survives(self):
        # 恶意/特殊字符票号（经 XML 实体转义进入，解析后还原为攻击串）：
        # 管线不崩溃、内容原样返回（XSS 防御在前端 esc，后端不做 HTML 解析）
        evil = '<img src=x onerror=alert(1)>'
        escaped = evil.replace("<", "&lt;").replace(">", "&gt;")
        r = client.post("/parse", files=[("file", ("evil.xml", xml(escaped, "20260801", "100.00", "13.00", "113.00"), "application/xml"))])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["invoice_no"], evil)


if __name__ == "__main__":
    unittest.main(verbosity=2)
