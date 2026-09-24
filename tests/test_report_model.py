"""RV-91 预审报告数据模型测试：buildReportModel 纯函数口径一致性。

在 chromium 渲染 demo 后注入 node 脚本，调用 buildReportModel(demoReport())
校验：批次头 / 统计摘要（价税净额=蓝+红冲）/ 四类结论计数 / 逐票结论 / 风险清单 / 免责声明。
"""
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "index.html"
CHROME = "/usr/local/bin/chromium"
HAVE_CHROME = os.path.exists(CHROME)


def render_check(script: str) -> str:
    """chromium headless 渲染后执行注入 JS，返回 console 输出。CI 无 chromium 时跳过。"""
    if not HAVE_CHROME:
        raise unittest.SkipTest("chromium 不可用（CI 环境），跳过渲染类测试")
    code = f"""
    <script>
    try {{
      const m = buildReportModel(demoReport());
      {script}
      document.title = "REPORT_TEST_RESULT=" + JSON.stringify(window.__report_test_output);
    }} catch (e) {{
      document.title = "REPORT_TEST_ERROR=" + (e && e.message || String(e));
    }}
    </script>
    """
    html = HTML.read_text(encoding="utf-8")
    injected = html.replace("</body>", code + "</body>")
    tmp = ROOT / "tests" / "_tmp_report_test.html"
    tmp.write_text(injected, encoding="utf-8")
    try:
        r = subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=5000", "--dump-dom", f"file://{tmp}"],
            capture_output=True, text=True, timeout=60)
        out = r.stdout
    finally:
        tmp.unlink(missing_ok=True)
    m = re.search(r"<title>(REPORT_TEST_(?:RESULT|ERROR)=.*?)</title>", out, re.S)
    if not m:
        raise AssertionError("no report test title (chrome failed or script error)")
    payload = m.group(1)
    if payload.startswith("REPORT_TEST_RESULT="):
        return payload[len("REPORT_TEST_RESULT="):]
    raise AssertionError(payload[len("REPORT_TEST_ERROR="):])


class TestReportModel(unittest.TestCase):
    def test_header_fields(self):
        out = render_check("""
        window.__report_test_output = {
          batchId: m.header.batchId, ruleset: m.header.ruleset,
          source: m.header.source, fileCount: m.header.fileCount,
          invoiceCount: m.header.invoiceCount, failedCount: m.header.failedCount
        };
        """)
        d = __import__("json").loads(out)
        self.assertEqual(d["batchId"], "demo-20260923-0001")
        self.assertEqual(d["fileCount"], 6)
        self.assertEqual(d["invoiceCount"], 5)
        self.assertEqual(d["failedCount"], 1)
        self.assertIn("0.3.0", d["ruleset"])

    def test_summary_consistency(self):
        """价税净额 = 蓝票合计 + 红冲（负数）；四类结论计数=发票数。"""
        out = render_check("""
        const s = m.summary;
        const pcSum = Object.values(s.precheck).reduce((a, b) => a + b, 0);
        window.__report_test_output = {
          net: s.netAmt, blue: s.blueAmt, red: s.redAmt,
          blueCnt: s.blueCnt, redCnt: s.redCnt,
          pcSum: pcSum, invCnt: s.invoiceCount,
          amtSum: s.amountSum, taxSum: s.taxSum
        };
        """)
        d = __import__("json").loads(out)
        self.assertAlmostEqual(d["net"], round(d["blue"] + d["red"], 2), places=2)
        self.assertEqual(d["blueCnt"], 4)
        self.assertEqual(d["redCnt"], 1)
        self.assertEqual(d["pcSum"], d["invCnt"])
        self.assertEqual(d["invCnt"], 5)
        # 红冲为负数
        self.assertAlmostEqual(d["red"], -530.00, places=2)
        self.assertAlmostEqual(d["blue"], 2656.57, places=2)
        self.assertAlmostEqual(d["net"], 2126.57, places=2)
        # 金额合计（不含税）与税额（不含红冲）——与工作台 KPI 口径一致
        self.assertAlmostEqual(d["amtSum"], 2006.20, places=2)
        self.assertAlmostEqual(d["taxSum"], 150.37, places=2)

    def test_tickets_and_risks(self):
        out = render_check("""
        window.__report_test_output = {
          tickets: m.tickets.length, risks: m.risks.length,
          firstPc: m.tickets[0].precheck, redKind: m.tickets[3].kind,
          redTotal: m.tickets[3].total, hasProblem: m.tickets[0].problem_count,
          disclaimLen: m.disclaimer.length,
          blockAmt: m.summary.precheckAmt["阻断入账"], reviewAmt: m.summary.precheckAmt["建议核对"]
        };
        """)
        d = __import__("json").loads(out)
        self.assertEqual(d["tickets"], 5)
        self.assertEqual(d["risks"], 4)
        self.assertEqual(d["firstPc"], "阻断入账")
        self.assertEqual(d["redKind"], "红冲")
        self.assertEqual(d["redTotal"], "-530.00")
        # 00001 命中 R1 重复报销 1 条问题
        self.assertEqual(d["hasProblem"], 1)
        self.assertGreater(d["disclaimLen"], 50)
        # 结论涉及金额（修复括号 bug 后的口径）：848+1590=2438；91.37-530=-438.63
        self.assertAlmostEqual(d["blockAmt"], 2438.00, places=2)
        self.assertAlmostEqual(d["reviewAmt"], -438.63, places=2)

    def test_report_dom_render(self):
        """报告 DOM 生成：含批次头/统计/逐票表/风险表/免责声明。"""
        out = render_check("""
        const html = reportDOMHtml(m);
        window.__report_test_output = {
          hasTitle: html.includes("发票预审报告"),
          hasNet: html.includes("价税合计净额"),
          hasTicket: html.includes("逐票结论"),
          hasRisk: html.includes("风险清单"),
          hasDisc: html.includes("免责声明"),
          hasBatch: html.includes("demo-20260923-0001"),
          hasSymbol: html.includes("⛔")
        };
        """)
        d = __import__("json").loads(out)
        for k in ("hasTitle", "hasNet", "hasTicket", "hasRisk", "hasDisc", "hasBatch", "hasSymbol"):
            self.assertTrue(d[k], f"{k} should be true")

    def test_file_name(self):
        out = render_check("""
        window.__report_test_output = {
          name: m.fileName,
          ok: m.fileName.indexOf("预审报告") === 0 &&
              m.fileName.indexOf("demo-20260923-0001") > 0 &&
              m.fileName.endsWith(".xlsx")
        };
        """)
        d = __import__("json").loads(out)
        self.assertTrue(d["ok"], f"fileName bad: {d['name']}")

    def test_disp_snapshot_injection(self):
        """RV-92 B1：处置状态快照注入——模型不读 UI 全局状态（纯函数契约）。"""
        out = render_check("""
        const m0 = buildReportModel(demoReport(), { dispMap: {}, liveMode: false });
        // 找到第一个风险对应的 riskId，构造快照
        const d0 = demoReport();
        const f0 = d0.findings[0];
        const rid = riskId(f0);
        const m1 = buildReportModel(d0, { dispMap: { [rid]: { status: "已处理" } }, liveMode: false });
        window.__report_test_output = {
          m0status: m0.risks[0].status, m1status: m1.risks[0].status,
          rid: rid
        };
        """)
        d = __import__("json").loads(out)
        self.assertEqual(d["m0status"], "待处理")
        self.assertEqual(d["m1status"], "已处理")

    def test_model_memo_reuse(self):
        """RV-92 B2：数据/处置未变时复用同一模型实例，Excel/PDF 时间戳状态一致。"""
        out = render_check("""
        __lastReport = demoReport();
        const a = getReportModel();
        const b = getReportModel();
        // 构造一次无变化调用后对比实例与时间戳
        window.__report_test_output = {
          same: a === b,
          expA: a.header.exportedAt, expB: b.header.exportedAt,
          nameA: a.fileName, nameB: b.fileName
        };
        """)
        d = __import__("json").loads(out)
        self.assertTrue(d["same"], "模型应复用同一实例")
        self.assertEqual(d["expA"], d["expB"], "导出时间戳应一致（快照语义）")
        self.assertEqual(d["nameA"], d["nameB"], "文件名应一致（同一快照）")


if __name__ == "__main__":
    unittest.main()
