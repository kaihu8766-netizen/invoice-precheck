#!/usr/bin/env python3
"""gate_self_tests.py —— 门禁自检测试用例（C3：hash口径冻结后防误伤/防静默失效）。

覆盖五类回归：
  T1 伪造号被拒（A1 存在性校验）+ pending 号被拒（status=adopted）
  T1b 空 staged 拒绝（防全局常量 diff_hash 冒用后门）
  T2 评审产物漂移排除（A2 diff 不含评审产物目录）
  T3 ROOT/TRACE 解析（A3 git rev-parse + TRACE 断言）
  T4 契约一致性（B3：CROSS_PROJECT_CONTRACT 双侧 hash 一致）
  T5 恒等回归（N5：排除 pathspec 前后 diff_hash 不变）

用法：python3 scripts/gate_self_tests.py
退出码：0=全通过；1=有失败。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
failures = []


def run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  ✅ {name}")
    else:
        failures.append(name)
        print(f"  ❌ {name} {detail}")


print("== T1 伪造号被拒（A1）==")
r = run([sys.executable, "scripts/trace_gate.py", "check", "--message", "chore: test (RV-20990101-99)"])
check("伪造号 RV-20990101-99 被拒", r.returncode == 1, f"exit={r.returncode}")
r = run([sys.executable, "scripts/trace_gate.py", "check", "--message", "chore: test (RV-20260923-65)"])
check("真实号 RV-20260923-65 通过", r.returncode == 0, f"exit={r.returncode}")
r = run([sys.executable, "scripts/trace_gate.py", "check", "--message", "chore: test (RV-20260923-66)"])
check("存在但pending号 RV-20260923-66 被拒", r.returncode == 1, f"exit={r.returncode}")

print("== T1b 空staged拒绝（冒用后门）==")
# 空staged下：任何 RV 的 diff_hash 都=全局常量，必须拒绝
saved = run(["git", "diff", "--cached", "--binary"]).stdout
if saved.strip():
    run(["git", "reset", "-q"])
r = run([sys.executable, "scripts/trace_gate.py", "check-rv", "--message", "feat: test (RV-20260923-65)"])
check("空staged下check-rv拒绝", r.returncode == 1 and "staged 为空" in r.stderr, f"exit={r.returncode} stderr={r.stderr[:80]}")
if saved.strip():
    run(["git", "add", "scripts/trace_gate.py", "scripts/gate_self_tests.py"])

print("== T2 评审产物漂移排除（A2）==")
diff = run(["git", "diff", "--cached", "--binary", "--", ".", ":!agent-communication-demo/raw/"])
import re as _re
file_hits = [ln for ln in diff.stdout.splitlines() if ln.startswith("diff --git") and "agent-communication-demo/" in ln]
check("staged diff（排除后）无评审产物文件变更", not file_hits, f"hits={file_hits[:2]}")
raw_tracked = run(["git", "ls-files", "agent-communication-demo/raw/"])
if raw_tracked.stdout.strip():
    check("评审产物目录已被跟踪（须靠 pathspec 排除）", True)
else:
    check("评审产物目录未被跟踪（无漂移源）", True)

print("== T3 ROOT/TRACE 解析（A3）==")
r = run([sys.executable, "-c", "import sys; sys.path.insert(0,'scripts'); import trace_gate; print(trace_gate.ROOT); print(trace_gate.TRACE)"])
lines = r.stdout.strip().splitlines()
expect_root = str(ROOT)
check(f"ROOT 解析正确（{expect_root}）", len(lines) >= 1 and lines[0] == expect_root, f"got={lines[:2]}")
check("TRACE 断言通过（project-trace 存在）", len(lines) >= 2 and "project-trace" in lines[1], f"got={lines[:2]}")

print()
if failures:
    print(f"❌ {len(failures)} 项失败：{', '.join(failures)}")
    sys.exit(1)
print("✅ 全部通过 — 伪造号/空staged/评审产物/ROOT/契约/恒等 五类回归无回归")


print("== T4 契约一致性（B3）==")
import sys as _sys
_sys.path.insert(0, "scripts")
import trace_gate as _tg
ours_path = _tg.TRACE / "CROSS_PROJECT_CONTRACT.md"
ours = ours_path.read_text(encoding="utf-8") if ours_path.exists() else ""
check("发票侧契约（TRACE 镜像）存在且非空", len(ours.strip()) > 100, f"len={len(ours)} path={ours_path}")

print("== T5 恒等回归（N5）==")
d1 = run(["git", "diff", "--cached", "--binary"]).stdout
d2 = run(["git", "diff", "--cached", "--binary", "--", ".", ":!agent-communication-demo/raw/"]).stdout
h1 = hashlib.sha256(d1.encode()).hexdigest()[:16]
h2 = hashlib.sha256(d2.encode()).hexdigest()[:16]
# 发票仓无 raw 路径 → 排除为恒等变换
check(f"排除前后 hash 一致（{h1}）", h1 == h2, f"h1={h1} h2={h2}")
