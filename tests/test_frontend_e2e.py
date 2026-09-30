"""T-006 前端自动化测试（RV-20260930-342 方案 adopted）。

Playwright 真实浏览器 e2e：页面加载/演示渲染/视图切换/规则抽屉/处置流转/
风险ID稳定性/导出CSV/XLSX/存储预警/移动端375px/规则跳转。12 用例。
说明：click 一律用 DOM click 触发（headless 下原生 click 的可见性等待不稳定）。
UI 事实（RV-75）：≥1280px 双栏同屏；<1280px 用 .mob-view-bar；rule 走抽屉。
运行：python3 -m unittest tests.test_frontend_e2e
"""
import os
import sys
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

INDEX_URL = Path(__file__).resolve().parents[1] / "docs" / "index.html"
FILE_URL = f"file://{INDEX_URL}"
# 浏览器路径自适应：本机用预装 chromium-1169（playwright 1.62 默认要 1234 未装）；
# CI 上 playwright install 后走默认路径（不传 executable_path）。
CHROME_BIN = os.environ.get(
    "TEST_CHROME_BIN",
    "/opt/vm/preinstall/ms-playwright/chromium_headless_shell-1169/chrome-linux/headless_shell",
)


def _launch_kwargs():
    import os as _os
    if _os.path.exists(CHROME_BIN):
        return {"executable_path": CHROME_BIN}
    return {}


class FrontendE2EBase(unittest.TestCase):
    """模块级浏览器单例 + 每用例独立 page（sync，无 asyncio 冲突）。"""

    _pw = None
    _browser = None

    @classmethod
    def setUpClass(cls):
        if cls._browser is None:
            cls._pw = sync_playwright().start()
            cls._browser = cls._pw.chromium.launch(headless=True, **_launch_kwargs())

    @classmethod
    def tearDownClass(cls):
        if cls._browser:
            cls._browser.close()
            cls._pw.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={"width": 1024, "height": 900})
        self.js_errors = []
        self.page.on("pageerror", lambda e: self.js_errors.append(str(e)))
        self.page.on("console",
                     lambda m: self.js_errors.append(m.text) if m.type == "error" else None)
        self.page.goto(FILE_URL)
        self.page.wait_for_selector("#result .finding", timeout=10000)

    def tearDown(self):
        self.page.close()

    def _click(self, selector, index=0):
        """DOM click（绕过 headless 可见性等待）。"""
        self.page.evaluate(
            "([sel, i]) => { const el = document.querySelectorAll(sel)[i]; if (el) el.click(); }",
            [selector, index])

    def _active_mob_view(self):
        return self.page.locator(".mob-view-bar .mv.active").get_attribute("data-view")


class TestLoadAndRender(FrontendE2EBase):
    def test_01_page_loads_no_js_error(self):
        """页面加载：无 JS 报错 + 风险发现出现。"""
        self.assertEqual(self.js_errors, [], f"存在 JS 错误: {self.js_errors}")
        n = self.page.locator("#result .finding").count()
        self.assertGreaterEqual(n, 1, "应有风险发现项")

    def test_02_demo_data_rendered(self):
        """演示数据渲染：统计口径提示存在（渲染成功标志）。"""
        text = self.page.locator("#result").inner_text()
        self.assertIn("统计口径", text)

    def test_03_risk_id_stable_across_rerender(self):
        """风险ID稳定性：同一发票同一规则 → data-rid 重渲染不变。"""
        first = self.page.locator("#result .finding").evaluate_all(
            "els => els.map(e => e.getAttribute('data-rid'))")
        self.page.evaluate("render(demoReport())")
        self.page.wait_for_timeout(300)
        second = self.page.locator("#result .finding").evaluate_all(
            "els => els.map(e => e.getAttribute('data-rid'))")
        self.assertEqual(first, second, "重渲染后风险ID应稳定不变")


class TestViewSwitch(FrontendE2EBase):
    def test_04_risk_invoice_switch_and_rule_drawer(self):
        """窄屏：risk/invoice 切换 + rule 走抽屉打开。"""
        self._click(".mob-view-bar .mv[data-view='invoice']")
        self.page.wait_for_timeout(250)
        self.assertEqual(self._active_mob_view(), "invoice", "逐票清单应激活")
        self._click("#btnOpenRules")
        self.page.wait_for_selector(".rule-drawer.open", timeout=5000)
        self.assertGreater(self.page.locator(".rule-drawer.open").count(), 0,
                           "规则抽屉应打开")
        self._click(".mob-view-bar .mv[data-view='risk']")
        self.page.wait_for_timeout(250)
        self.assertEqual(self._active_mob_view(), "risk", "风险待办应激活")

    def test_05_view_content_distinct(self):
        """视图内容不同：risk 视图 vs invoice 视图。"""
        self._click(".mob-view-bar .mv[data-view='risk']")
        self.page.wait_for_timeout(200)
        risk_text = self.page.locator("#result .view-risk-col").inner_text()
        self._click(".mob-view-bar .mv[data-view='invoice']")
        self.page.wait_for_timeout(200)
        inv_text = self.page.locator("#result .view-invoice-col").inner_text()
        self.assertNotEqual(risk_text, inv_text, "两视图内容应不同")
        self.assertIn("逐票清单", inv_text, "invoice 视图应有逐票清单")

    def test_06_mobile_375px_view_switch(self):
        """移动端 375px：视图切换可用且无 JS 错误。"""
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.page.wait_for_timeout(250)
        self._click(".mob-view-bar .mv[data-view='invoice']")
        self.page.wait_for_timeout(250)
        self.assertEqual(self._active_mob_view(), "invoice", "移动端切换应可用")
        text = self.page.locator("#result").inner_text()
        self.assertGreater(len(text), 0)
        self.assertEqual(self.js_errors, [], f"移动端 JS 错误: {self.js_errors}")


class TestDisposition(FrontendE2EBase):
    def test_07_confirm_marks_processed(self):
        """处置流转：标记已处理 → badge → 撤销回待处理。"""
        rid = self.page.locator("#result .finding").first.get_attribute("data-rid")
        self.assertIsNotNone(rid)
        self._click("#result .finding button.op.resolved")
        self.page.wait_for_timeout(250)
        text = self.page.locator("#result .finding").first.inner_text()
        self.assertIn("已处理", text, "badge 应显示已处理")
        self._click("#result .finding button.op.undo")
        self.page.wait_for_timeout(250)
        text = self.page.locator("#result .finding").first.inner_text()
        self.assertNotIn("已处理", text, "撤销后应回待处理")

    def test_08_disposition_persists_in_storage(self):
        """处置持久化：setDisp 写入后 getDisp 可读回。"""
        rid = self.page.locator("#result .finding").first.get_attribute("data-rid")
        self.page.evaluate(
            "(rid) => { window.setDisp(rid, 'resolved'); }", rid)
        got = self.page.evaluate(
            "(rid) => { const d = window.getDisp(rid); return d && d.status; }", rid)
        self.assertEqual(got, "resolved", "setDisp 后 getDisp 应读回 resolved")


class TestExport(FrontendE2EBase):
    def test_09_export_csv_downloads(self):
        """导出 CSV：展开菜单 → 触发下载且内容含发票。"""
        self._click("#btnExport")
        self.page.wait_for_selector("#dlList.open", timeout=3000)
        with self.page.expect_download(timeout=8000) as dl_info:
            self.page.evaluate(
                "document.querySelector(\"button[onclick*='exportInvoicesCSV']\").click()")
        dl = dl_info.value
        path = dl.path()
        self.assertTrue(os.path.exists(path), "CSV 应生成文件")
        with open(path, "rb") as f:
            head = f.read(300).decode("utf-8", errors="ignore")
        self.assertIn("发票", head, "CSV 应含发票相关内容")

    def test_10_export_xlsx_downloads(self):
        """导出 XLSX：触发下载且为 zip 魔数。"""
        self._click("#btnExport")
        self.page.wait_for_selector("#dlList.open", timeout=3000)
        with self.page.expect_download(timeout=8000) as dl_info:
            self.page.evaluate(
                "document.querySelector(\"button[onclick*='exportInvoicesXLSX']\").click()")
        dl = dl_info.value
        path = dl.path()
        self.assertTrue(os.path.exists(path), "XLSX 应生成文件")
        with open(path, "rb") as f:
            magic = f.read(4)
        self.assertEqual(magic[:2], b"PK", "XLSX 应含 zip 魔数 PK")


class TestStorageAndJump(FrontendE2EBase):
    def test_11_storage_usage_no_error(self):
        """存储预警：storageUsageBytes 返回数字不抛错。"""
        val = self.page.evaluate(
            "() => { const v = window.storageUsageBytes(); return typeof v; }")
        self.assertEqual(val, "number", "storageUsageBytes 应返回数字")

    def test_12_rule_link_opens_drawer(self):
        """规则跳转：点击规则链接 → 打开规则抽屉并高亮。"""
        self._click("#result .rule-link")
        self.page.wait_for_selector(".rule-drawer.open", timeout=5000)
        self.assertGreater(self.page.locator(".rule-drawer.open .rule-card").count(), 0,
                           "抽屉内应有规则卡")


if __name__ == "__main__":
    unittest.main()
