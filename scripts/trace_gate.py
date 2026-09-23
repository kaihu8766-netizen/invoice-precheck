#!/usr/bin/env python3
"""trace_gate.py —— 项目硬门禁脚本（DeepSeek 评审 #9 采纳）。

用途：
- gate    : 大动作前门禁：读评审索引/DECISIONS/OPEN_ISSUES → 输出相关 RV/DEC/ISS、
            判定是否需新评审 → 生成 GATE-YYYYMMDD-NN 记录（写 03-会议与日志/门禁记录/）
- check   : 校验 commit message 含合法 ID 引用（GATE-|RV-|DEC-|ISS-），供 commit-msg 钩子调用
- ids     : 按关键词列出相关 RV/DEC/ISS（讨论前查重，避免重复评审）

用法：
    python3 scripts/trace_gate.py gate --task "TASK-xxx 描述"
    python3 scripts/trace_gate.py check --message "feat: ... (RV-20260923-05)"
    python3 scripts/trace_gate.py ids --grep "OCR"

退出码：check 校验失败 = 1；其余正常 = 0。
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

# 项目根（本脚本位于 <root>/scripts/）
ROOT = Path(__file__).resolve().parent.parent

# 溯源仓库路径（与 invoice-precheck 平级）
TRACE = ROOT.parent / "project-trace"
RV_DIR = TRACE / "03-会议与日志" / "DeepSeek评审"
GATE_DIR = TRACE / "03-会议与日志" / "门禁记录"
DECISIONS = TRACE / "DECISIONS.md"
OPEN_ISSUES = TRACE / "OPEN_ISSUES.md"
RV_INDEX = RV_DIR / "索引.md"

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
    if ids:
        print(f"[check] OK：引用 {ids}")
        return 0
    print("[check] FAIL：commit message 必须含 ID 引用（GATE-* / RV-* / DEC-* / ISS-*）")
    print("[check] 示例：feat(scope): 描述 (RV-20260923-05, GATE-20260923-01)")
    return 1


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


def _classify(staged_diff: str) -> tuple[list[str], str]:
    """按 gate_rules.yaml 分类 staged diff：返回 (命中类别列表, diff_hash)。"""
    import hashlib, re, yaml
    rules_path = Path(__file__).parent / "gate_rules.yaml"
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
    HARD_GATE_SELF = ("scripts/trace_gate.py", ".githooks/commit-msg", "scripts/gate_rules.yaml",
                      "agent-communication-demo/deepseek_gate.py", "AGENTS.md")
    if any(f.startswith(h) for f in files for h in HARD_GATE_SELF):
        hits.append("gate_self")
    for name, rule in rules.items():
        paths = rule.get("paths", []); kws = rule.get("keywords", [])
        if any(f.startswith(p) for f in files for p in paths):
            hits.append(name); continue
        if any(k in f for f in files for k in kws):
            hits.append(name)
    return sorted(set(hits)), diff_hash


def cmd_classify(staged: bool) -> int:
    """classify --staged：分类当前 staged diff，输出命中红线类别 + diff_hash。"""
    import subprocess
    diff = subprocess.run(["git", "diff", "--cached", "--binary"],
                          capture_output=True, text=True).stdout if staged else ""
    if not staged:
        print("缺少 --staged；仅支持对 staged 改动分类（commit 前使用）")
        return 2
    hits, diff_hash = _classify(diff)
    if hits:
        print(f"[classify] 命中评审红线：{', '.join(hits)}")
    else:
        print("[classify] 未命中评审红线（可仅带常规 ID 提交）")
    print(f"[classify] diff_hash={diff_hash}")
    return 0 if not hits else 1


def cmd_check_rv(message: str) -> int:
    """check-rv --message <msg>：命中红线时校验提交带已批准且 diff_hash 匹配的 RV-ID。

    commit-msg 钩子调用：1) 分类 staged diff；2) 命中红线→必须有 RV-ID 且
    其档案记录的 diff_hash == 当前 staged diff hash；3) 无 RV 或哈希不符→拒绝。
    """
    import subprocess, re, yaml
    diff = subprocess.run(["git", "diff", "--cached", "--binary"],
                          capture_output=True, text=True).stdout
    hits, cur_hash = _classify(diff)
    if not hits:
        print("[check-rv] 未命中评审红线（常规 ID 校验由钩子继续）")
        return 0
    m = re.search(r"\bRV-(\d{6,8}(?:-\d+)?)\b", message)
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
    dm = re.search(r"diff_hash:\s*([0-9a-f]{16})", atext)
    if not dm:
        print(f"✗ RV-{rv} 档案未记录 diff_hash（需用新版 deepseek_gate.py 生成），提交被拒", file=sys.stderr)
        return 1
    if dm.group(1) != cur_hash:
        print(f"✗ RV-{rv} 的 diff_hash({dm.group(1)}) ≠ 当前 staged({cur_hash})，提交被拒", file=sys.stderr)
        print("  原因：评审后改动过红线文件；请重新评审或提交未评审的新改动", file=sys.stderr)
        return 1
    print(f"[check-rv] 通过：RV-{rv} diff_hash 匹配（{cur_hash}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="项目硬门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate"); g.add_argument("--task", required=True)
    c = sub.add_parser("check"); c.add_argument("--message", required=True)
    i = sub.add_parser("ids"); i.add_argument("--grep", required=True)
    cl = sub.add_parser("classify"); cl.add_argument("--staged", action="store_true")
    cr = sub.add_parser("check-rv"); cr.add_argument("--message", required=True)
    args = ap.parse_args()
    if args.cmd == "gate":
        return cmd_gate(args.task)
    if args.cmd == "check":
        return cmd_check(args.message)
    if args.cmd == "ids":
        return cmd_ids(args.grep)
    if args.cmd == "classify":
        return cmd_classify(args.staged)
    if args.cmd == "check-rv":
        return cmd_check_rv(args.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
