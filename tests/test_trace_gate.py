"""trace_gate 事前对齐门禁单测（RV-24 放行条件 5）。

覆盖：FEAT_RE 四形态触发 / fix 跳过 / 无 F 引用拒绝 / 未批准 F 拒绝 /
F-ID 去重全校验 / scheme-RV 判定（phase+feature+status）。
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.trace_gate as tg


def make_arch(dirpath: Path, rv_id: str, phase: str, feature: str, status: str) -> Path:
    f = dirpath / f"{rv_id}.md"
    f.write_text(
        "---\n"
        f"id: RV-{rv_id}\n"
        "date: 2026-09-23T12:00:00\n"
        f"phase: {phase}\n"
        f"feature: {feature}\n"
        f"status: {status}\n"
        "---\n# 测试档案\n",
        encoding="utf-8",
    )
    return f


class TestFeatRegex(unittest.TestCase):
    """RV-23 口径 2：feat 触发正则四形态定死。"""

    def test_four_forms(self):
        for m in ("feat: a (F-20260923-01)", "feat(rev): a (F-20260923-01)",
                  "feat!: a (F-20260923-01)", "feat(rev)!: a (F-20260923-01)"):
            self.assertTrue(tg.FEAT_RE.match(m), m)

    def test_non_feat_skipped(self):
        for m in ("fix(csv): a", "docs: a", "refactor: a", "featx: a"):
            self.assertFalse(tg.FEAT_RE.match(m), m)


class TestCheckScheme(unittest.TestCase):
    """check-scheme：全部 F-xxx 必须有 adopted scheme-RV。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rv_dir = Path(self.tmp.name) / "DeepSeek评审"
        self.rv_dir.mkdir(parents=True)
        self.feat_dir = Path(self.tmp.name) / "功能登记"
        self.feat_dir.mkdir(parents=True)
        patcher1 = mock.patch.object(tg, "RV_DIR", self.rv_dir)
        patcher2 = mock.patch.object(tg, "FEATURE_DIR", self.feat_dir)
        patcher1.start()
        patcher2.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)

    def test_no_fid_rejected(self):
        rc = tg.cmd_check_scheme("feat(rev): 复核工作台 (RV-20260923-17)")
        self.assertEqual(rc, 1)

    def test_unapproved_fid_rejected(self):
        rc = tg.cmd_check_scheme("feat(rev): 测试 (F-20260923-99)")
        self.assertEqual(rc, 1)

    def test_approved_fid_passes(self):
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "adopted")
        rc = tg.cmd_check_scheme("feat(gate): 事前对齐门禁 (F-20260923-01, RV-20260923-23)")
        self.assertEqual(rc, 0)

    def test_pending_fid_rejected(self):
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "pending")
        rc = tg.cmd_check_scheme("feat(gate): 测试 (F-20260923-01)")
        self.assertEqual(rc, 1)

    def test_wrong_phase_rejected(self):
        make_arch(self.rv_dir, "20260923-23", "review", "F-20260923-01", "adopted")
        rc = tg.cmd_check_scheme("feat(gate): 测试 (F-20260923-01)")
        self.assertEqual(rc, 1)

    def test_all_fids_required(self):
        # 一个已批准 + 一个未批准 → 拒绝（防挂靠包装）
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "adopted")
        rc = tg.cmd_check_scheme("feat(gate): 测试 (F-20260923-01, F-20260923-02)")
        self.assertEqual(rc, 1)

    def test_fid_regex_fixed_length(self):
        # F-ID 定长：8 位日期 + 2 位序号
        for m in ("F-20260923-01", "F-20260923-99"):
            self.assertTrue(tg.FID_RE.search(m), m)
        for m in ("F-20260923-1", "F-2026092-01", "F-abc"):
            self.assertFalse(tg.FID_RE.search(m), m)

    def test_any_approved_among_multi(self):
        # 口径 4：存在任一 adopted 即可（保留评审历史）
        make_arch(self.rv_dir, "20260923-20", "scheme", "F-20260923-01", "pending")
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "adopted")
        rc = tg.cmd_check_scheme("feat(gate): 测试 (F-20260923-01)")
        self.assertEqual(rc, 0)

    def test_fid_ascii_digits_only(self):
        # RV-25：\d→[0-9] 防 Unicode 数字（全角）误判
        self.assertFalse(tg.FID_RE.search("F-２０２６０９２３-０１"), "全角数字不应匹配")
        self.assertTrue(tg.FID_RE.search("F-20260923-01"))

    def test_fid_word_boundaries(self):
        # RV-26/27：\b 词边界（非锚定）——F 前与序号后不得紧邻 word 字符；前缀/后缀/超长序号不得通过
        for m in ("xF-20260923-01", "F-20260923-011", "F-20260923-01x"):
            self.assertFalse(tg.FID_RE.search(m), m)
        self.assertTrue(tg.FID_RE.search("(F-20260923-01,"))
        self.assertTrue(tg.FID_RE.search("F-20260923-01 "))


class TestAuditScheme(unittest.TestCase):
    """RV-25 口径 6 相等分支：同次提交（档案时间=代码时间）判"方案先行"。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feat_dir = Path(self.tmp.name) / "功能登记"
        self.feat_dir.mkdir(parents=True)
        self.rv_dir = Path(self.tmp.name) / "DeepSeek评审"
        self.rv_dir.mkdir(parents=True)
        patcher1 = mock.patch.object(tg, "FEATURE_DIR", self.feat_dir)
        patcher2 = mock.patch.object(tg, "RV_DIR", self.rv_dir)
        patcher1.start()
        patcher2.start()
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)

    def _run_with_git_times(self, rv_times, code_times):
        """mock subprocess.run：--diff-filter=A 调用=档案入库时间，其余=代码首次提交时间。"""
        import subprocess

        def fake_run(cmd, *a, **kw):
            which = 0 if "--diff-filter=A" in cmd else 1
            src = rv_times if which == 0 else code_times
            return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(src), stderr="")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            return tg.cmd_audit_scheme()

    def test_equal_time_is_scheme_first(self):
        # 同次提交：档案入库时间 == 代码首次提交时间 → 方案先行（<= 含相等，RV-25 口径6）
        (self.feat_dir / "F-20260923-01.md").write_text("# F-20260923-01\n", encoding="utf-8")
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "adopted")
        rc = self._run_with_git_times(
            ["2026-09-23 12:00:00 +0800"], ["2026-09-23 12:00:00 +0800"])
        self.assertEqual(rc, 0)

    def test_code_before_review_is_late(self):
        # 代码先于方案评审入库 → 方案后补（exit 1）
        (self.feat_dir / "F-20260923-01.md").write_text("# F-20260923-01\n", encoding="utf-8")
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "adopted")
        rc = self._run_with_git_times(
            ["2026-09-23 14:00:00 +0800"], ["2026-09-23 12:00:00 +0800"])
        self.assertEqual(rc, 1)

    def test_no_archive_is_pending(self):
        # 档案未入库（rv 时间空）→ 待审计（非后补违规）
        (self.feat_dir / "F-20260923-01.md").write_text("# F-20260923-01\n", encoding="utf-8")
        rc = self._run_with_git_times([], ["2026-09-23 12:00:00 +0800"])
        self.assertEqual(rc, 0)

    def test_git_log_cmd_uses_archive_dir(self):
        # RV-31：git log 档案路径须含 ARCHIVE_DIR 前缀 + basename（防相对路径查不到）
        import subprocess
        (self.feat_dir / "F-20260923-01.md").write_text("# F-20260923-01\n", encoding="utf-8")
        make_arch(self.rv_dir, "20260923-23", "scheme", "F-20260923-01", "adopted")
        captured = []

        def fake_run(cmd, *a, **kw):
            captured.append(cmd)
            which = 0 if "--diff-filter=A" in cmd else 1
            src = ["2026-09-23 12:00:00 +0800"] if which == 0 else ["2026-09-23 12:00:00 +0800"]
            return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(src), stderr="")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            tg.cmd_audit_scheme()
        log_cmd = captured[0]
        self.assertTrue(any(tg.ARCHIVE_DIR in str(a) for a in log_cmd), "git log 必须带 ARCHIVE_DIR 前缀")
        self.assertTrue(any(a.endswith(".md") for a in log_cmd), "git log 须含档案 basename")


class TestRequiresTraceMatrix(unittest.TestCase):
    """RV-82（声明式 requires_trace）命令矩阵测试：无 TRACE 仓时 classify 成功、
    依赖 TRACE 命令 SystemExit。防"本地绿 CI 红"回归 + 防误拦/漏拦。"""

    def _run_main(self, argv):
        import contextlib, io
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["trace_gate"] + argv):
            with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
                code = tg.main()
        return code, buf.getvalue()

    def _set_trace_missing(self):
        self._orig_trace = tg.TRACE
        tg.TRACE = Path("/nonexistent/project-trace")

    def tearDown(self):
        if hasattr(self, "_orig_trace"):
            tg.TRACE = self._orig_trace

    def test_classify_without_trace_succeeds(self):
        """classify 声明 requires_trace=False：无 TRACE 也放行（只读 gate_rules.yaml）。"""
        self._set_trace_missing()
        code, out = self._run_main(["classify", "--staged"])
        self.assertNotIn("FATAL: TRACE", out, "classify 不应要求 TRACE")

    def test_dependent_cmd_without_trace_exits(self):
        """依赖 TRACE 的命令（gate/check/ids/check-rv/preflight/check-scheme/audit-scheme）无 TRACE 必拦。"""
        self._set_trace_missing()
        cases = [
            ["gate", "--task", "架构"],
            ["check", "--message", "feat(x): RV-20260923-99 (F-20260923-99)"],
            ["ids", "--grep", "RV-"],
            ["check-rv", "--message", "feat(x): (F-20260923-99, RV-20260923-99)"],
            ["preflight", "--desc", "x"],
            ["check-scheme", "--message", "feat: x (F-20260923-99)"],
            ["audit-scheme"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "argv", ["trace_gate"] + argv):
                    with self.assertRaises(SystemExit):
                        tg.main()

    def test_classify_does_not_fallback_to_trace_check(self):
        """classify 即使显式给 --staged 也不触发 _require_trace（声明式豁免生效）。
        无 --staged 返回 2（缺参提示），但不抛 SystemExit——证明没走 TRACE 检查。"""
        self._set_trace_missing()
        code, out = self._run_main(["classify"])
        self.assertEqual(code, 2, "无 --staged 应返回缺参码 2（而非被 TRACE 拦）")
        self.assertIn("--staged", out, "应提示缺 --staged 而非 TRACE 错误")


class TestCheckRVAnchoredRegex(unittest.TestCase):
    """RV-112/114：hash 字段提取必须 ^ 行首锚定——防 diff_hash 为空时向后滑动匹配
    project_trace_diff_hash: 行的子串 diff_hash:（双向误配缺陷回归）。

    RV-114 修正：测试**直接调用生产函数 tg._extract_hash_field**（真耦合）——
    回退生产锚定修复后本测试必须失败，杜绝"假回归保护"。
    """

    ARCH_WITH_EMPTY_DIFF = (
        "---\nid: RV-20260925-99\ndiff_hash: \nproject_trace_diff_hash: a2eaaf9f12a764e1\n"
        "status: adopted\n---\n# body\n"
    )
    ARCH_NORMAL = (
        "---\nid: RV-20260925-99\ndiff_hash: 9d54e362fc4adf81\nproject_trace_diff_hash: a2eaaf9f12a764e1\n"
        "status: adopted\n---\n# body\n"
    )

    def test_anchored_does_not_match_substring(self):
        """diff_hash 为空 + pt hash 非空：生产函数必须返回 None（不滑动匹配子串）。"""
        self.assertIsNone(tg._extract_hash_field(self.ARCH_WITH_EMPTY_DIFF, "diff_hash"))

    def test_anchored_still_matches_normal(self):
        """正常形态：两字段各自正确提取，互不干扰。"""
        self.assertEqual(tg._extract_hash_field(self.ARCH_NORMAL, "diff_hash"), "9d54e362fc4adf81")
        self.assertEqual(
            tg._extract_hash_field(self.ARCH_NORMAL, "project_trace_diff_hash"), "a2eaaf9f12a764e1")

    def test_anchored_rejects_missing_field(self):
        """字段缺失返回 None（fail-closed 拒绝路径）。"""
        self.assertIsNone(tg._extract_hash_field(self.ARCH_NORMAL, "nonexistent_hash"))


if __name__ == "__main__":
    unittest.main()
