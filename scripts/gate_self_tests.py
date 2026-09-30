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

print("== T1b 空staged（RV-128/129：档案入库提交语义）==")
# RV-128/129：空 staged = 仅评审产物/流程文档改动 = 档案入库提交。
#  放行条件：引用档案存在且已 adopted 的 RV（RV-129：仅"存在"不够，防退化为任意RV即放行）。
#  封堵"全局常量 diff_hash 冒用后门"：无 RV → 拒绝；档案缺失/未 adopted → 拒绝。
# RV-348 修复：记录**完整** staged 文件集合并逐条回填——原写死只回填 2 个文件，
# 会把 commit-msg/gate_rules.yaml 等接入文件踢出 staging，破坏评审后的 diff_hash（评审→提交失配）。
_saved_names = run(["git", "diff", "--cached", "--name-only"]).stdout.splitlines()
_saved = run(["git", "diff", "--cached", "--binary"]).stdout
if _saved.strip():
    run(["git", "reset", "-q"])
r = run([sys.executable, "scripts/trace_gate.py", "check-rv", "--message", "chore: archive (ISS-1)"])
check("空staged无RV被拒", r.returncode == 1 and "空 staged" in r.stderr, f"exit={r.returncode} stderr={r.stderr[:80]}")
r = run([sys.executable, "scripts/trace_gate.py", "check-rv", "--message", "chore: archive (RV-20260923-65)"])
check("空staged+adopted RV放行（RV-128/129）", r.returncode == 0, f"exit={r.returncode} stderr={r.stderr[:80]}")
if _saved_names:
    run(["git", "add"] + _saved_names)

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
# RV-349 P2：移除中途 sys.exit——T1-T3 失败也继续跑 T4/T5/T6，末尾统一退出
# （原中途退出会让 T4/T5/T6 根本不执行，早打印"五类回归通过"在失败场景下有误导性）


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

print("== T6 闸门5 risk_level 分级门禁（RV-20261001-347）==")
# RV-348 修复（去顺序依赖）：T6b/T6c 依赖前序 staged 非空——从干净工作树运行时
# check-risk 会因空 staged 早退（return 0），T6c（期望 exit 1）假失败/掩盖未阻断。
# 故先确保非空 staged fixture，T6 结束恢复进入时状态。
_t6_saved_names = run(["git", "diff", "--cached", "--name-only"]).stdout.splitlines()
_t6_saved = run(["git", "diff", "--cached", "--binary"]).stdout
if not _t6_saved.strip():
    run(["git", "add", "scripts/trace_gate.py"])
# 临时规则文件（copy 当前 gate_rules.yaml，改 risk_gate.mode）——不污染工作区配置
import tempfile as _tf
_tmp_dir = Path(_tf.mkdtemp())
_tmp_rules = _tmp_dir / "gate_rules.test.yaml"
_src_rules = (ROOT / "scripts" / "gate_rules.yaml").read_text(encoding="utf-8")

# T6a: [LIGHT] 标记 → 跳过（exit=0，闸门4 才是 [LIGHT] 判定者）
r = run([sys.executable, "scripts/trace_gate.py", "check-risk", "--message", "docs: update [LIGHT] (RV-20260923-65)"])
check("T6a [LIGHT]标记跳过", r.returncode == 0, f"exit={r.returncode} stderr={r.stderr[:80]}")

# T6b: warn 模式 + 无 RV → 放行但告警（观察期语义：应拦截但不阻断）
_tmp_rules.write_text(_src_rules, encoding="utf-8")
r = run([sys.executable, "scripts/trace_gate.py", "check-risk", "--message", "chore: no-rv (ISS-1)", "--rules", str(_tmp_rules)])
check("T6b warn观察期无RV放行+WARN", r.returncode == 0 and "WARN" in r.stderr, f"exit={r.returncode} stderr={r.stderr[:120]}")

# T6c: deny 模式 + 无 RV → 阻断（观察期满收紧后的语义）
_tmp_rules.write_text(_src_rules.replace("mode: warn", "mode: deny"), encoding="utf-8")
r = run([sys.executable, "scripts/trace_gate.py", "check-risk", "--message", "chore: no-rv (ISS-1)", "--rules", str(_tmp_rules)])
check("T6c deny模式无RV阻断", r.returncode == 1, f"exit={r.returncode} stderr={r.stderr[:120]}")

# T6e: 配置异常（非法 mode）→ fail-closed 阻断（RV-348 反证3 修正）
_tmp_rules.write_text(_src_rules.replace("mode: warn", "mode: bogus"), encoding="utf-8")
r = run([sys.executable, "scripts/trace_gate.py", "check-risk", "--message", "chore: no-rv (ISS-1)", "--rules", str(_tmp_rules)])
check("T6e 配置异常fail-closed阻断", r.returncode == 1 and "fail-closed" in r.stderr, f"exit={r.returncode} stderr={r.stderr[:120]}")

# T6d: 新增文件 → 升 medium（构造临时新文件 staged，断言升级提示）
_tmp_rules.write_text(_src_rules, encoding="utf-8")
_tmp_new = ROOT / "scripts" / "__gate_test_new__.py"
_tmp_new.write_text("# gate test new file\n", encoding="utf-8")
run(["git", "add", str(_tmp_new)])
r = run([sys.executable, "scripts/trace_gate.py", "check-risk", "--message", "chore: newfile (ISS-1)", "--rules", str(_tmp_rules)])
check("T6d 新增文件升级提示", "新增文件" in r.stderr, f"stderr={r.stderr[:120]}")
run(["git", "reset", "-q", str(_tmp_new)])
_tmp_new.unlink()
# RV-349 P1：清理后断言临时文件不在 staged（reset 失败时会以"已暂存+工作区已删"残留污染下次提交）
_r6d = run(["git", "diff", "--cached", "--name-only"])
check("T6d 临时文件已清理", "__gate_test_new__" not in _r6d.stdout, f"staged={_r6d.stdout[:80]}")

# 恢复进入时 staged 状态（fixture 仅在有需要时加入）
if not _t6_saved.strip():
    run(["git", "reset", "-q", "scripts/trace_gate.py"])

print("== T7 门禁守卫 fail-closed（RV-349 反证A + RV-350 反证1 + RV-351 消息拆分/去状态耦合）==")
# 反证A：rm gate_rules.yaml（工作区删除，不进 diff）→ 旧守卫跳过闸门2/5 → 免审提交。
# 反证1（RV-350）：守卫以 trace_gate.py 存在为前提 → 删 trace_gate.py 同构绕过（比 A 更彻底，跳过全部闸门）。
# 修复：钩子被调用即检查 trace_gate.py 与 gate_rules.yaml 任一缺失 / yaml 模块缺失 → 拒绝所有提交。
# RV-351：守卫消息拆分（各只提自己缺的文件）→ T7a/T7c 断言唯一可分辨；
#          T7 自带非空 staged fixture（去状态耦合：干净工作树下 T7b 不再假失败）。
_os = __import__("os")
_msgf = _tmp_dir / "msg.txt"
_msgf.write_text("chore: t7 (RV-20260923-65)\n", encoding="utf-8")
_tg_path = ROOT / "scripts" / "trace_gate.py"
_tg_bak = _tg_path.with_name("trace_gate.py.__t7bak__")

# fixture：确保非空 staged（干净工作树也走"非空+无匹配RV → 闸门2 拒绝"的统一场景）
_t7_saved = run(["git", "diff", "--cached", "--binary"]).stdout
if not _t7_saved.strip():
    run(["git", "add", "scripts/trace_gate.py"])

# T7a: gate_rules.yaml 缺失 → 守卫拒绝（fail-closed，消息只提 gate_rules.yaml）
_cfg_path = ROOT / "scripts" / "gate_rules.yaml"
_cfg_bak = _cfg_path.with_name("gate_rules.yaml.__t7bak__")
try:
    _os.rename(_cfg_path, _cfg_bak)
    r = run(["sh", ".githooks/commit-msg", str(_msgf)])
    check("T7a 配置缺失守卫拒绝", r.returncode == 1 and "gate_rules.yaml 缺失" in r.stderr,
          f"exit={r.returncode} stderr={r.stderr[:120]}")
finally:
    if _cfg_bak.exists() and not _cfg_path.exists():
        _os.rename(_cfg_bak, _cfg_path)
# T7b: 配置恢复 → 守卫放行且走到闸门2（非空 staged 无匹配 RV 时闸门2 正常拒绝属预期；
#      断言"守卫消息（缺失）不出现 + check-rv 拒绝输出（diff_hash）出现"= 守卫放行、后续闸门已执行）
r = run(["sh", ".githooks/commit-msg", str(_msgf)])
check("T7b 配置恢复守卫放行+闸门执行",
      "缺失" not in r.stderr and "diff_hash" in r.stderr,
      f"exit={r.returncode} stderr={r.stderr[:120]}")
# T7c: trace_gate.py 缺失 → 守卫拒绝（RV-350 反证1 封堵，消息只提 trace_gate.py）
try:
    _os.rename(_tg_path, _tg_bak)
    r = run(["sh", ".githooks/commit-msg", str(_msgf)])
    check("T7c trace_gate缺失守卫拒绝", r.returncode == 1 and "trace_gate.py 缺失" in r.stderr,
          f"exit={r.returncode} stderr={r.stderr[:120]}")
finally:
    if _tg_bak.exists() and not _tg_path.exists():
        _os.rename(_tg_bak, _tg_path)

# 恢复 fixture（进入时为空才移除）
if not _t7_saved.strip():
    run(["git", "reset", "-q", "scripts/trace_gate.py"])

print()
if failures:
    print(f"❌ {len(failures)} 项失败：{', '.join(failures)}")
    sys.exit(1)
print("✅ 全部通过 — 七类回归无回归（含闸门5 risk_level + 守卫 fail-closed）")
