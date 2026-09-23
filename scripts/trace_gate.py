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


def main() -> int:
    ap = argparse.ArgumentParser(description="项目硬门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate"); g.add_argument("--task", required=True)
    c = sub.add_parser("check"); c.add_argument("--message", required=True)
    i = sub.add_parser("ids"); i.add_argument("--grep", required=True)
    args = ap.parse_args()
    if args.cmd == "gate":
        return cmd_gate(args.task)
    if args.cmd == "check":
        return cmd_check(args.message)
    if args.cmd == "ids":
        return cmd_ids(args.grep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
