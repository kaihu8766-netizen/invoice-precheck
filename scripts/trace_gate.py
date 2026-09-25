#!/usr/bin/env python3
"""trace_gate.py —— 项目硬门禁脚本（DeepSeek 评审 #9 采纳）。

用途：
- gate    : 大动作前门禁：读评审索引/DECISIONS/OPEN_ISSUES → 输出相关 RV/DEC/ISS、
            判定是否需新评审 → 生成 GATE-YYYYMMDD-NN 记录（写 03-会议与日志/门禁记录/）
- check   : 校验 commit message 含合法 ID 引用（GATE-|RV-|DEC-|ISS-），供 commit-msg 钩子调用
- ids     : 按关键词列出相关 RV/DEC/ISS（讨论前查重，避免重复评审）
- preflight: 功能开发前立项（RV-22 采纳：定位=发号器，非检查点）——生成 F-YYYYMMDD-NN
            功能登记 + 检查该功能是否已有已批准(adopted)的方案评审 RV（phase=scheme）
- check-scheme: 功能提交第三闸门（commit-msg 调用）——message 以 feat( 开头 →
            必须引用 F-xxx 且存在已批准且 feature 匹配的 scheme-RV，否则拒绝提交
- audit-scheme: 事后审计（RV-22 修正：时间审计不在 commit-msg 做墙钟比对——恒为假；
            由审计脚本对比 scheme-RV 评审时间 vs 首次引用 F-xxx 的提交时间，产出"方案后补"清单）

用法：
    python3 scripts/trace_gate.py gate --task "TASK-xxx 描述"
    python3 scripts/trace_gate.py check --message "feat: ... (RV-20260923-05)"
    python3 scripts/trace_gate.py ids --grep "OCR"
    python3 scripts/trace_gate.py preflight --desc "复核工作台弹窗"
    python3 scripts/trace_gate.py check-scheme --message "feat(rev): ... (F-20260923-01, RV-...)"
    python3 scripts/trace_gate.py audit-scheme

退出码：check 校验失败 = 1；其余正常 = 0。
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

# 项目根（A3修复：git rev-parse 更稳健，兼容 worktree/symlink；失败时明确报错）
import subprocess as _sp
try:
    ROOT = Path(_sp.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
except Exception as _e:
    raise SystemExit(f"FATAL: 无法定位仓库根（git rev-parse 失败）：{_e}")

# 溯源仓库路径（与 invoice-precheck 平级）
TRACE = ROOT.parent / "project-trace"
# RV-79（CI修复）：TRACE 存在性检查从模块级下沉到命令执行时——import 永不因外部环境
# 崩溃（CI 只有本仓、无旁侧 trace 仓；模块级 SystemExit 曾导致 tests/test_trace_gate.py
# import 失败 → CI unittest 步骤全红）。正确语义：库模块 import 不应退出进程，
# 外部依赖在使用时检查。各 cmd_* 入口调用 _require_trace()。
# RV-30：评审档案相对 TRACE 仓库根的目录（git log -- <path> 用，path 相对仓库根；抽常量防目录改名漏改）
ARCHIVE_DIR = "03-会议与日志/DeepSeek评审"
RV_DIR = TRACE / ARCHIVE_DIR
GATE_DIR = TRACE / "03-会议与日志" / "门禁记录"
FEATURE_DIR = TRACE / "03-会议与日志" / "功能登记"
DECISIONS = TRACE / "DECISIONS.md"
OPEN_ISSUES = TRACE / "OPEN_ISSUES.md"
RV_INDEX = RV_DIR / "索引.md"


def _require_trace() -> None:
    """命令执行时检查 TRACE 仓存在（RV-79：从模块级 SystemExit 下沉至此；RV-82 错误信息完善）。

    A3 断言保留：TRACE 必须存在且是真 git 仓，防布局假设失效时静默读错文件。
    调用方：main() 分发处（requires_trace=True 的命令）；库复用场景请先确认 TRACE 有效。
    """
    if not (TRACE / ".git").exists():
        raise SystemExit(
            f"FATAL: TRACE 仓库不存在或不是 git 仓：{TRACE}\n"
            f"  期望布局：TRACE 与项目根（{ROOT}）平级，即 {TRACE} 应为 git 仓（含 .git/）。\n"
            f"  修复：clone/pull project-trace 到正确位置，或设置环境变量 TRACE_GATE_ROOT 指向含 project-trace 的父目录。\n"
            f"  A3 假设：评审档案/功能登记/门禁记录/索引均存于 TRACE 仓，缺失会导致静默读错文件。"
        )

# 大动作清单（与 AGENTS.md §2 一致）
BIG_ACTIONS = [
    "架构", "接口", "数据模型", "依赖", "技术栈", "外部服务", "部署",
    "发布", "对外", "成本", "权限", "数据迁移", "删除", "覆盖",
    "DECISIONS", "真实用户数据", "真实数据", "批量",
]

# commit message 合法 ID 引用
ID_RE = re.compile(r"\b(?:GATE|RV|DEC|ISS)[-:]\d{6,8}(?:-\d+)?\b|\bISS-\d+\b")


def _today() -> str:
    return datetime.date.today().strftime("%Y%m%d")


def _next_gate_no() -> int:
    if not GATE_DIR.exists():
        return 1
    used = []
    for f in GATE_DIR.glob("GATE-*.md"):
        m = re.search(r"GATE-(\d{8})-(\d+)", f.name)
        if m and m.group(1) == _today():
            used.append(int(m.group(2)))
    return (max(used) + 1) if used else 1


def _big_action(task: str) -> list[str]:
    return [kw for kw in BIG_ACTIONS if kw.lower() in task.lower()]


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _find_ids(text: str, prefix: str) -> list[str]:
    pat = rf"\b{prefix}-\d{{6,8}}(-\d+)?\b|\b{prefix}-\d+\b"
    ids = re.findall(pat, text)
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def cmd_gate(task: str) -> int:
    hits = _big_action(task)
    if not hits:
        print(f"[gate] 未命中大动作清单，绿灯可直接做（命中词：{hits}）")
        print("[gate] 建议：非大动作提交仍带关联 ID 引用")
        return 0

    # 读相关上下文
    dec = _read_text(DECISIONS)
    iss = _read_text(OPEN_ISSUES)
    rv_idx = _read_text(RV_INDEX)
    rel_rv = [i for i in re.findall(r"\| \d+ \|", rv_idx) if any(k in rv_idx for k in (task,))]
    # 简化：列出索引全部行号段内的主题关键词命中
    rv_lines = [ln for ln in rv_idx.splitlines() if ln.startswith("| ")]
    rel = [ln for ln in rv_lines if any(k.lower() in ln.lower() for k in (task, *hits))]

    no = _next_gate_no()
    gid = f"GATE-{_today()}-{no:02d}"
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    f = GATE_DIR / f"{gid}.md"
    f.write_text(
        f"# {gid}\n\n"
        f"- 时间：{datetime.datetime.now().isoformat(timespec='seconds')}\n"
        f"- 任务：{task}\n"
        f"- 命中大动作：{', '.join(hits)}\n"
        f"- 判定：**需新评审**（大动作，先过 DeepSeek 双闸门，用户批准后执行）\n\n"
        f"## 关联上下文\n\n"
        f"- 评审索引命中：{len(rel)} 条（见下）\n"
        f"- 相关 DEC：{len(_find_ids(dec, 'DEC'))} 条候选\n"
        f"- 相关 ISS：{len(_find_ids(iss, 'ISS'))} 条候选\n\n"
        f"## 索引命中（避免重复评审）\n\n"
        + ("\n".join(rel) if rel else "（无，需新建评审）")
        + "\n",
        encoding="utf-8",
    )
    print(f"[gate] 大动作命中：{hits}")
    print(f"[gate] 判定：需新评审（先过 DeepSeek 双闸门，用户批准后执行）")
    print(f"[gate] 相关评审索引命中 {len(rel)} 条：")
    for ln in rel:
        print(f"       {ln.strip()[:120]}")
    print(f"[gate] 门禁 ID：{gid}（已记录 {f.name}）")
    print("[gate] 下一步：用 deepseek_gate.py 发起评审 → 用户批准 → 带 GATE-ID 执行")
    return 0


def cmd_check(message: str) -> int:
    ids = ID_RE.findall(message)
    if not ids:
        print("[check] FAIL：commit message 必须含 ID 引用（GATE-* / RV-* / DEC-* / ISS-*）")
        print("[check] 示例：feat(scope): 描述 (RV-20260923-05, GATE-20260923-01)")
        return 1
    # A1修复：RV/GATE 号必须在 trace/ 索引中真实存在（防伪造号自证）+ status=adopted（防冒用pending号）
    idx = _read_text(RV_INDEX)
    for rid in ids:
        if rid.startswith(("RV-", "GATE-")):
            m = re.search(r"(\d{8})-(\d+)$", rid)
            if m:
                ymd, no = m.group(1), m.group(2)
                prefix = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}-{int(no):02d}-"
                rv_line = next((ln for ln in idx.splitlines() if prefix in ln), None)
                # B1：档案=唯一权威源；索引是派生视图。索引缺→查档案目录，档案在则 fail loud 提示重建索引，不在才判伪造号
                if not rv_line:
                    arch_md = RV_DIR / f"{prefix}*.md"
                    if list(RV_DIR.glob(f"{prefix}*.md")):
                        print(f"[check] WARN：{rid} 档案存在但索引未收录（索引视图过期），请重建索引", file=sys.stderr)
                        print(f"[check] FAIL：{rid} 索引不一致，拒绝提交（需先重建索引）", file=sys.stderr)
                        return 1
                    print(f"[check] FAIL：{rid} 未在评审索引中找到（伪造号或档案未入库）", file=sys.stderr)
                    return 1
                if "| adopted |" not in rv_line:
                    print(f"[check] FAIL：{rid} 在索引中存在但未置 adopted（pending 号不可作为批准依据）", file=sys.stderr)
                    return 1
    print(f"[check] OK：引用 {ids}（存在性校验通过）")
    return 0


def cmd_ids(grep: str) -> int:
    print(f"[ids] 关键词：{grep}")
    for name, path in (("RV 索引", RV_INDEX), ("DECISIONS", DECISIONS), ("OPEN_ISSUES", OPEN_ISSUES)):
        t = _read_text(path)
        lines = [ln for ln in t.splitlines() if grep.lower() in ln.lower()]
        print(f"--- {name}（{len(lines)} 行命中）---")
        for ln in lines[:15]:
            print(f"  {ln.strip()[:130]}")
        if not lines:
            print("  （无）")
    return 0


def _data_segments(path: Path, markers: list[str]) -> list[tuple[int, int]]:
    """定位数据段行区间（RV-78：显式哨兵锚点，弃用花括号配平——HTML/JS 混合文件
    中模板串/插值/注释会让朴素配平失准，哨兵零歧义）。

    哨兵格式（gate_rules.yaml data_segments 定义 begin/end 标记对）：
        // @demo-data:begin
        function demoReport() { ... }
        // @demo-data:end
    区间 = [begin行, end行]（闭区间，删改哨兵行本身=命中）。

    fail-closed（RV-78）：文件不存在/读取失败/找不到成对哨兵 → 返回 [(0,0)] 哨兵
    对用 (0,0) 表示"无法定位=全文件视为数据段"（由调用方判断：空区间与任何变更行
    不相交则不会误报；但调用方对找不到哨兵单独处理 fail-closed）。
    返回：[(start,end), ...]；找不到任何哨兵对时返回 []（调用方据此 fail-closed）。
    """
    if not path.exists():
        return [(0, 0)]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return [(0, 0)]
    # markers: ["begin", "end"] 或含 ":begin"/":end" 字样
    begin_m = next((m for m in markers if "begin" in m), None)
    end_m = next((m for m in markers if "end" in m), None)
    if not begin_m or not end_m:
        return [(0, 0)]
    segs = []
    begin = 0
    for i, ln in enumerate(lines, start=1):
        if begin == 0 and begin_m in ln:
            begin = i
        elif begin > 0 and end_m in ln:
            segs.append((begin, i))
            begin = 0
    return segs if segs else [(0, 0)]


def _diff_changed_lines(diff: str) -> set[int]:
    """从 unified diff（-U0）解析变更行号集合（RV-77/78：变更行 ∩ 数据段 = 命中 demo_data）。

    解析 @@ -a,b +c,d @@ 头：跟踪旧侧游标（删除行 → 旧侧行号）与新侧游标（新增行 → 新侧行号）。
    过滤 0（diff 头 +0 特例：新增文件首行实际从行 1 起；行号 0 不与任何数据段相交）。
    """
    import re as _re
    out = set()
    old = 0; new = 0
    for ln in diff.splitlines():
        m = _re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            old = int(m.group(1)); new = int(m.group(3))
            continue
        if not ln.startswith(("+", "-", " ")):
            continue
        if ln.startswith("-"):
            if old > 0:
                out.add(old)
            old += 1
        elif ln.startswith("+"):
            if new > 0:
                out.add(new)
            new += 1
        else:
            old += 1; new += 1
    return out


def _classify(staged_diff: str, rules_path: Path | None = None) -> tuple[list[str], str]:
    """按 gate_rules.yaml 分类 staged diff：返回 (命中类别列表, diff_hash)。

    RV-77：统一判定引擎——check-rv 消费红线类（parser_core/pdf_pipeline/ocr_engine/
    rules_engine/data_redline/gate_self），check-scheme 消费需事前对齐类（demo_data）。
    判定分层：结构区间（主判据）> 变更行内容 > 路径+关键词（兜底）。
    RV-110（#62 门禁）：rules_path 可指定自定义规则文件——project-trace 仓复用本引擎
    时传其仓内规则（gate_rules.trace.yaml），否则默认本仓（invoice-precheck）规则。
    """
    import hashlib, re, yaml
    rules_path = rules_path or (Path(__file__).parent / "gate_rules.yaml")
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))["rules"]
    diff_hash = hashlib.sha256(staged_diff.encode("utf-8", "replace")).hexdigest()[:16]
    files = set()
    for ln in staged_diff.splitlines():
        if ln.startswith("diff --git"):
            m = re.search(r"b/(\S+)", ln)
            if m:
                files.add(m.group(1))
    hits = []
    # RV-15 自指修复：核心保护集硬编码（不随 gate_rules.yaml 被删而失效）
    # RV-110：移除跨仓死路径 agent-communication-demo/deepseek_gate.py（RV-107 同口径——
    # 该目录属 project-trace 仓，本仓规则管不到；project-trace 侧由 #62 门禁 +
    # gate_rules.trace.yaml gate_self 承接）
    HARD_GATE_SELF = ("scripts/trace_gate.py", ".githooks/commit-msg", "scripts/gate_rules.yaml",
                      "AGENTS.md")
    if any(f.startswith(h) for f in files for h in HARD_GATE_SELF):
        hits.append("gate_self")
    changed_lines = _diff_changed_lines(staged_diff) if staged_diff.strip() else set()
    for name, rule in rules.items():
        paths = rule.get("paths", []); kws = rule.get("keywords", []); segs = rule.get("data_segments", [])
        file_hit = any(f.startswith(p) for f in files for p in paths)
        # RV-77/78 结构区间主判据（demo_data 专属）：paths 仅限定目标文件，命中与否由
        # 哨兵数据段行区间 ∩ diff 变更行决定——改 CSS/文案/模板不命中，改数据语义/删哨兵必命中
        if segs:
            seg_hit = False
            for f in files:
                if not any(f.startswith(p) for p in paths):
                    continue
                segments = _data_segments(ROOT / f, segs)
                # RV-78 fail-closed：文件不存在/读取失败/找不到成对哨兵 → 判命中（防删哨兵绕过）
                if not segments or segments == [(0, 0)]:
                    seg_hit = True
                    break
                for s, e in segments:
                    if changed_lines & set(range(s, e + 1)):
                        seg_hit = True
                        break
                if seg_hit:
                    break
            if seg_hit:
                hits.append(name)
            continue
        # 非 demo_data 类：paths 前缀命中即算（红线类语义：改了该文件就要审）
        if file_hit:
            hits.append(name); continue
        if any(k in f for f in files for k in kws):
            hits.append(name)
    return sorted(set(hits)), diff_hash


def cmd_classify(staged: bool, rules: str = "") -> int:
    """classify --staged：分类当前 staged diff，输出命中红线类别 + diff_hash。"""
    import subprocess
    diff = subprocess.run(["git", "diff", "--cached", "--binary", "--", ".", ":!agent-communication-demo/raw/"],
                          capture_output=True, text=True, cwd=ROOT).stdout if staged else ""
    if not staged:
        print("缺少 --staged；仅支持对 staged 改动分类（commit 前使用）")
        return 2
    rp = Path(rules).resolve() if rules else None
    if rp and not rp.is_file():
        print(f"[classify] FAIL：--rules 文件不存在：{rp}", file=sys.stderr)
        return 2
    hits, diff_hash = _classify(diff, rp)
    if hits:
        print(f"[classify] 命中评审红线：{', '.join(hits)}")
    else:
        print("[classify] 未命中评审红线（可仅带常规 ID 提交）")
    print(f"[classify] diff_hash={diff_hash}")
    return 0 if not hits else 1


def cmd_check_rv(message: str, rules: str = "", hash_field: str = "diff_hash") -> int:
    """check-rv --message <msg>：命中红线时校验提交带已批准且 diff_hash 匹配的 RV-ID。

    commit-msg 钩子调用：1) 分类 staged diff；2) 命中红线→必须有 RV-ID 且
    其档案记录的 diff_hash == 当前 staged diff hash；3) 无 RV 或哈希不符→拒绝。
    RV-110：--rules 自定义规则文件（project-trace 钩子用其仓内规则）；
    --hash-field 指定档案中对比的哈希字段（project-trace 钩子传 project_trace_diff_hash）。
    """
    import subprocess, re, yaml
    diff = subprocess.run(["git", "diff", "--cached", "--binary", "--", ".", ":!agent-communication-demo/raw/"],
                          capture_output=True, text=True, cwd=ROOT).stdout
    if not diff.strip():
        # 空 staged：diff_hash 是全局常量（e3b0c442...），任何 RV 都能"匹配"——冒用后门，直接拒绝
        print("[check-rv] FAIL：staged 为空，拒绝以空 diff 校验 RV（防全局常量冒用后门）", file=sys.stderr)
        return 1
    rp = Path(rules).resolve() if rules else None
    if rp and not rp.is_file():
        print(f"[check-rv] FAIL：--rules 文件不存在：{rp}", file=sys.stderr)
        return 1
    hits, cur_hash = _classify(diff, rp)
    if not hits:
        print("[check-rv] 未命中评审红线（常规 ID 校验由钩子继续）")
        return 0
    m = re.search(r"\bRV-([0-9]{6,8}(?:-[0-9]+)?)\b", message)  # RV-112：\d→[0-9]（RV-25 同口径，防 Unicode 数字）
    if not m:
        print(f"✗ 命中评审红线（{', '.join(hits)}），提交被拒：必须带已批准 RV-ID", file=sys.stderr)
        print("  流程：先跑 deepseek_gate.py 发起评审（自动记录 staged diff_hash），批准后重提交", file=sys.stderr)
        print("  提示：git add 后评审，评审完成前不要改动工作区", file=sys.stderr)
        return 1
    rv = m.group(1)  # 形如 20260923-99（message 提取时无 RV- 前缀）
    # 索引行不含完整 RV-ID（只有序号），按档案文件名格式匹配：YYYY-MM-DD-NN
    ymd, no = rv.split("-")[0], "-".join(rv.split("-")[1:])
    arch_glob = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}-{no}"
    idx = _read_text(RV_INDEX)
    rv_line = next((ln for ln in idx.splitlines() if arch_glob in ln), None)
    arch = None
    if rv_line:
        cm = re.search(r"\|\s*([^|]+\.md)\s*\|", rv_line)
        if cm:
            arch = cm.group(1).strip()
    if not arch:
        print(f"✗ RV-{rv} 未在索引中找到档案，提交被拒", file=sys.stderr)
        return 1
    arch_path = Path(RV_INDEX).parent / arch
    if not arch_path.exists():
        print(f"✗ RV-{rv} 档案不存在（{arch}），提交被拒", file=sys.stderr)
        return 1
    atext = arch_path.read_text(encoding="utf-8")
    # RV-112：正则加 ^ 行首锚定（re.M）——防 diff_hash 为空时 re.search 向后滑动匹配
    # project_trace_diff_hash: 行的子串 diff_hash:（RV-111/112 双向误配缺陷修复）
    dm = re.search(rf"^{re.escape(hash_field)}:\s*([0-9a-f]{{16}})", atext, re.M)
    if not dm:
        print(f"✗ RV-{rv} 档案未记录 {hash_field}（需用新版 deepseek_gate.py 生成），提交被拒", file=sys.stderr)
        return 1
    if dm.group(1) != cur_hash:
        print(f"✗ RV-{rv} 的 diff_hash({dm.group(1)}) ≠ 当前 staged({cur_hash})，提交被拒", file=sys.stderr)
        print("  原因：评审后改动过红线文件；请重新评审或提交未评审的新改动", file=sys.stderr)
        return 1
    print(f"[check-rv] 通过：RV-{rv} diff_hash 匹配（{cur_hash}）")
    return 0


# ---------- 事前对齐门禁（RV-22/23 采纳实施） ----------

# RV-23 口径 2：功能提交触发正则定死（feat: / feat(scope): / feat!: / feat(scope)!: 均算）
# RV-24 修正：原 [)]? 形态对 feat: 不匹配（feat 后无 ( 即失败），改可选 (scope) 组
FEAT_RE = re.compile(r"^feat(?:\([^)]*\))?!?:")
# RV-23 口径 1：message 中出现的全部 F-xxx 都必须满足（防挂靠已批准 feature 包装未批准功能）
# 定长 F-[0-9]{8}-[0-9]{2}（RV-25 修正：\d→[0-9] 防 Unicode 数字误判）；findall set 去重逐条校验，"顺带提到"的 F 号无豁免（有意为之）
FID_RE = re.compile(r"\bF-([0-9]{8}-[0-9]{2})\b")

def _next_feature_no() -> int:
    if not FEATURE_DIR.exists():
        return 1
    used = []
    for f in FEATURE_DIR.glob("F-*.md"):
        m = re.search(r"F-(\d{8})-(\d+)", f.name)
        if m and m.group(1) == _today():
            used.append(int(m.group(2)))
    return (max(used) + 1) if used else 1


def _find_scheme_rv(fid: str) -> Path | None:
    """查功能登记 F-xxx 是否有已批准(adopted)且 phase=scheme 的方案评审档案。"""
    if not RV_DIR.exists():
        return None
    for f in sorted(RV_DIR.glob("*.md")):
        if f.name == "索引.md":
            continue
        head = f.read_text(encoding="utf-8", errors="replace")[:800]
        fm = re.search(r"phase:\s*(\S+)", head)
        feat = re.search(r"feature:\s*(\S+)", head)
        st = re.search(r"status:\s*(\S+)", head)
        if fm and fm.group(1) == "scheme" and feat and feat.group(1) == fid \
                and st and st.group(1) == "adopted":
            return f
    return None


def cmd_preflight(desc: str) -> int:
    """功能开发前立项（RV-22：定位=发号器）。生成 F-ID 功能登记；已有方案评审则提示可直接开发。"""
    no = _next_feature_no()
    fid = f"F-{_today()}-{no:02d}"
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    f = FEATURE_DIR / f"{fid}.md"
    f.write_text(
        f"# {fid} · 功能登记\n\n"
        f"- 时间：{datetime.datetime.now().isoformat(timespec='seconds')}\n"
        f"- 描述：{desc}\n"
        f"- 方案评审（phase=scheme RV）：待发起\n"
        f"- 状态：已立项，待方案评审\n",
        encoding="utf-8",
    )
    print(f"[preflight] 功能登记已建立：{fid}（{f.name}）")
    rv = _find_scheme_rv(fid)
    if rv:
        print(f"[preflight] 已存在已批准方案评审：{rv.name} → 可直接开发")
    else:
        print("[preflight] 尚无已批准方案评审 → 下一步：deepseek_gate.py --phase scheme --feature " + fid + " 发起方案评审，批准后再开发")
    print(f"[preflight] 功能提交规范：feat(scope): 描述 ({fid}) + 方案评审 RV-ID")
    return 0


def cmd_check_scheme(message: str) -> int:
    """功能提交第三闸门（commit-msg 调用）：message 以 feat 约定开头 **或** staged diff 命中
    需事前对齐类（demo_data）→ 必须有已批准 scheme-RV。

    RV-23 口径：
    - 触发正则 FEAT_RE = ^feat[(][^)]+[)]?[!]?:（feat:/feat(scope):/feat!:/feat(scope)!:）
    - RV-77 修正：触发条件扩展为 FEAT_RE 匹配 **或** staged diff 命中 demo_data
      （演示数据口径/语义改动，即使 fix: 前缀也要求 scheme 事前对齐；防"改口径用 fix 绕过"）
    - message 中出现的全部 F-xxx 都必须存在 adopted scheme-RV（防挂靠包装）
    - adopted 判据：档案 status=adopted 且 phase=scheme 且 feature 匹配（RV-23 口径 4：任一即可，保留多轮评审历史）
    - adopted 为执行者按用户拍板填写 → 属诚实边界（防忘不防绕，RV-23 口径 3 方案 A）
    - 方案档案与代码同次提交：check 读工作区档案，存在即通过（RV-23 口径 5 预期行为）
    """
    import subprocess
    diff = subprocess.run(["git", "diff", "--cached", "--binary", "--", ".", ":!agent-communication-demo/raw/"],
                          capture_output=True, text=True, cwd=ROOT).stdout
    hits, _ = _classify(diff)
    # RV-77：需事前对齐类 = demo_data（演示数据口径语义）；红线类仍由 check-rv 管
    NEEDS_SCHEME = {"demo_data"}
    diff_trigger = bool(set(hits) & NEEDS_SCHEME)
    if not FEAT_RE.match(message.lstrip()) and not diff_trigger:
        print("[check-scheme] 非功能提交且 diff 未命中需事前对齐类（demo_data），跳过")
        return 0
    if diff_trigger:
        print(f"[check-scheme] diff 命中需事前对齐类：{sorted(set(hits) & NEEDS_SCHEME)}（演示数据口径改动，须已批准方案评审）")
    fids = sorted(set("F-" + m for m in FID_RE.findall(message)))
    if not fids:
        print("✗ 需事前对齐的提交必须引用功能登记 F-xxx（如 F-20260923-01）", file=sys.stderr)
        print("  流程：先跑 trace_gate.py preflight --desc \"...\" 立项 → 再 deepseek_gate.py --phase scheme 评审方案 → 批准后提交", file=sys.stderr)
        return 1
    for fid in fids:
        rv = _find_scheme_rv(fid)
        if not rv:
            print(f"✗ {fid} 无已批准方案评审（phase=scheme 且 status=adopted）", file=sys.stderr)
            print("  下一步：deepseek_gate.py --phase scheme --feature " + fid + " 发起方案评审 → 用户批准(adopted)后重提交", file=sys.stderr)
            return 1
        head = rv.read_text(encoding="utf-8", errors="replace")[:400]
        idm = re.search(r"id:\s*(RV-\S+)", head)
        print(f"[check-scheme] {fid} 已批准方案评审 {idm.group(1) if idm else rv.name}")
    print("[check-scheme] 通过：需事前对齐的提交均有已批准方案评审（方案先于实施，事前对齐成立）")
    return 0


def cmd_audit_scheme() -> int:
    """事后审计（RV-22/23 修正）：对比方案评审入库时间 vs 功能代码首次提交时间，产出"方案后补"清单。

    RV-23 口径：
    - 评审时间锚点 = scheme-RV 档案首次入库的 git 提交时间（git log --diff-filter=A --format=%ci），
      不可自填（frontmatter date 可被手工改，git 时间难伪造）——避免"同日/改时间"绕过
    - 代码锚点 = 首次引用 F-xxx 的提交时间（git log main 分支，取最早一条；committer date）
    - 评审时间 > 代码时间 = 方案后补（流程违规，exit 1）
    - 不在 commit-msg 做墙钟比对（RV-22：commit 对象未创建，恒为假）
    """
    import subprocess
    from datetime import datetime
    if not FEATURE_DIR.exists():
        print("[audit-scheme] 无功能登记目录，无审计对象")
        return 0
    print(f"{'F-ID':<14}{'方案评审入库':<22}{'代码首次提交':<22}判定")
    print("-" * 72)
    any_flag = False

    def _ts(ci_line: str):
        """%ci 行（2026-09-23 14:30:00 +0800）→ UTC epoch；时区偏移参与比较（RV-26）。

        RV-27 修正：用 strptime %z（全 Python 版本可靠，%z 支持无冒号 +0800），
        不用 fromisoformat（<3.11 对无冒号偏移抛 ValueError，naive/aware 处理也易错）。
        %z 产出 aware datetime → timestamp() 为真实 UTC epoch。
        """
        try:
            return datetime.strptime(ci_line.strip(), "%Y-%m-%d %H:%M:%S %z").timestamp()
        except Exception:
            return None

    for f in sorted(FEATURE_DIR.glob("F-*.md")):
        fid = f.stem
        rv = _find_scheme_rv(fid)
        rv_ts = None
        if rv:
            try:
                # 档案首次入库 git 提交时间（不可自填）；档案在 project-trace 仓 → cwd=TRACE
                # RV-24 修正：--reverse 取首条（最早入库），非默认最新；统一 committer date(%ci)
                out = subprocess.run(
                    ["git", "log", "--reverse", "--diff-filter=A", "--format=%ci", "--",
                     ARCHIVE_DIR + "/" + rv.name],  # rv.name 为 glob 结果的 basename（RV-30 确认）
                    capture_output=True, text=True, cwd=TRACE,
                ).stdout.strip().splitlines()
                if out:
                    rv_ts = _ts(out[0])
            except Exception:
                rv_ts = None
        # 代码锚点：main 分支首次引用 F-xxx 的提交 committer date（--reverse 取首条；
        # RV-24 口径 3：merge 进 main 的提交计入范围；--fixed-strings 防 F-xxx 正则歧义）
        code_ts = None
        try:
            out = subprocess.run(
                ["git", "log", "main", "--reverse", "--fixed-strings", "--grep=" + fid, "--format=%ci"],
                capture_output=True, text=True, cwd=ROOT,
            ).stdout.strip().splitlines()
            if out:
                code_ts = _ts(out[0])
        except Exception:
            code_ts = None
        rv_time = datetime.fromtimestamp(rv_ts).strftime("%Y-%m-%d %H:%M:%S") if rv_ts else None
        first_code = datetime.fromtimestamp(code_ts).strftime("%Y-%m-%d %H:%M:%S") if code_ts else None
        # RV-24 口径 6：同次提交空锚点分支——档案与代码同一次提交时两者相等，判"方案先行"（<= 含相等）；
        # rv_time 为空（档案未入库）= 待审计；first_code 为空（代码未提交）= 未提交代码
        if rv_ts is not None and code_ts is not None:
            verdict = "正常（方案先行）" if rv_ts <= code_ts else "方案后补 ⚠"
        elif rv_ts is not None and code_ts is None:
            verdict = "未提交代码"
        elif rv_ts is None and code_ts is not None:
            verdict = "档案未入库 ⚠"
        else:
            verdict = "未评审"
        if verdict == "方案后补 ⚠":
            any_flag = True
        print(f"{fid:<14}{str(rv_time or '未评审'):<22}{str(first_code or '（无）'):<22}{verdict}")
    print("-" * 72)
    if any_flag:
        print("[audit-scheme] 存在方案后补：功能代码提交早于方案评审入库——流程违规，下次功能开发须先 preflight + 方案评审")
        return 1
    print("[audit-scheme] 全部正常：方案评审均先于代码提交（事前对齐成立）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="项目硬门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # RV-82（声明式 requires_trace）：默认依赖 TRACE，classify 显式免除——
    # 不靠字符串比较 cmd != "classify"（防别名/默认命令误判）
    g = sub.add_parser("gate"); g.add_argument("--task", required=True)
    c = sub.add_parser("check"); c.add_argument("--message", required=True)
    i = sub.add_parser("ids"); i.add_argument("--grep", required=True)
    cl = sub.add_parser("classify"); cl.add_argument("--staged", action="store_true")
    cl.add_argument("--rules", default="", help="自定义规则文件（默认本仓 gate_rules.yaml；project-trace 用其 gate_rules.trace.yaml）")
    cl.set_defaults(requires_trace=False)
    cr = sub.add_parser("check-rv"); cr.add_argument("--message", required=True)
    cr.add_argument("--rules", default="", help="自定义规则文件（默认本仓 gate_rules.yaml）")
    cr.add_argument("--hash-field", default="diff_hash",
                    help="档案中对比的哈希字段（project-trace 钩子传 project_trace_diff_hash）")
    pf = sub.add_parser("preflight"); pf.add_argument("--desc", required=True)
    cs = sub.add_parser("check-scheme"); cs.add_argument("--message", required=True)
    au = sub.add_parser("audit-scheme")
    for p in (g, c, i, cr, pf, cs, au):
        p.set_defaults(requires_trace=True)
    args = ap.parse_args()
    # RV-79/82：依赖 TRACE 的 cmd 在入口统一断言（import 阶段不再 SystemExit）；
    # classify 只读 gate_rules.yaml 不依赖 TRACE，声明免除
    if getattr(args, "requires_trace", True):
        _require_trace()
    if args.cmd == "gate":
        return cmd_gate(args.task)
    if args.cmd == "check":
        return cmd_check(args.message)
    if args.cmd == "ids":
        return cmd_ids(args.grep)
    if args.cmd == "classify":
        return cmd_classify(args.staged, args.rules)
    if args.cmd == "check-rv":
        return cmd_check_rv(args.message, args.rules, args.hash_field)
    if args.cmd == "preflight":
        return cmd_preflight(args.desc)
    if args.cmd == "check-scheme":
        return cmd_check_scheme(args.message)
    if args.cmd == "audit-scheme":
        return cmd_audit_scheme()
    return 0


if __name__ == "__main__":
    sys.exit(main())
