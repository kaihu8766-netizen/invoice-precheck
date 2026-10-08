#!/usr/bin/env python3
"""F-20261009-02 ⑦：scheme 闸门反例测试（4 类）。

覆盖：
1. 跨 feature 拼凑：F-A 的提交引用 F-B 的 scheme 档案 → 必须找不到（拒）
2. 缺 phase 旧档案（无 legacy_scheme 标记）→ 必须找不到（fail-closed，防删 phase 行绕过）
3. 缺 phase 但显式 legacy_scheme: true → 找到（legacy 白名单放行）
4. 双源：frontmatter=scheme 但索引行非 adopted → 找不到（单源改 frontmatter 不算）
"""
import sys, tempfile, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import trace_gate as t

PASS = []
FAIL = []

def case(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}")

def write_rv(d: Path, fname, fm_body, index_line=None):
    (d / fname).write_text("---\n" + fm_body + "---\n", encoding="utf-8")
    if index_line:
        idx = d / "索引.md"
        with open(idx, "a", encoding="utf-8") as f:
            f.write(index_line + "\n")

def run():
    tmp = Path(tempfile.mkdtemp())
    # 索引基线：先建空索引
    (tmp / "索引.md").write_text("# 评审索引\n", encoding="utf-8")
    orig = t.RV_DIR
    t.RV_DIR = tmp
    try:
        print("== 1. 跨 feature 拼凑 ==")
        # F-B 的 scheme 档案
        write_rv(tmp, "2026-10-09-900-b.md",
                 "phase: scheme\nfeature: F-0000-B\nstatus: adopted\n",
                 "| 900 | 2026-10-09 | B | 见档案（2026-10-09-900-b.md） | adopted |")
        r = t._find_scheme_rv("F-0000-A")  # A 不能用 B 的档案
        case("F-A 用 F-B 的 scheme 档案 → 拒绝", r is None)

        print("== 2. 缺 phase 旧档案（无 legacy 标记）= fail-closed ==")
        write_rv(tmp, "2026-10-09-901-old.md",
                 "feature: F-0000-C\nstatus: adopted\n",  # 无 phase 无 legacy_scheme
                 "| 901 | 2026-10-09 | C | 见档案（2026-10-09-901-old.md） | adopted |")
        r = t._find_scheme_rv("F-0000-C")
        case("删 phase 行（无 legacy 标记）→ 拒绝", r is None)

        print("== 3. 缺 phase 但 legacy_scheme: true → 放行 ==")
        write_rv(tmp, "2026-10-09-902-leg.md",
                 "feature: F-0000-D\nstatus: adopted\nlegacy_scheme: true\n",
                 "| 902 | 2026-10-09 | D | 见档案（2026-10-09-902-leg.md） | adopted |")
        r = t._find_scheme_rv("F-0000-D")
        case("legacy_scheme: true 白名单 → 通过", r is not None)

        print("== 4. 双源：frontmatter=scheme 但索引行非 adopted ==")
        write_rv(tmp, "2026-10-09-903-idx.md",
                 "phase: scheme\nfeature: F-0000-E\nstatus: adopted\n",
                 "| 903 | 2026-10-09 | E | 见档案（2026-10-09-903-idx.md） | pending |")  # 索引是 pending
        r = t._find_scheme_rv("F-0000-E")
        case("frontmatter=scheme 但索引 pending → 拒绝（双源）", r is None)

        print("== 对照：正常严格匹配 ==")
        write_rv(tmp, "2026-10-09-904-ok.md",
                 "phase: scheme\nfeature: F-0000-F\nstatus: adopted\n",
                 "| 904 | 2026-10-09 | F | 见档案（2026-10-09-904-ok.md） | adopted |")
        r = t._find_scheme_rv("F-0000-F")
        case("正常 phase=scheme + 索引 adopted → 通过", r is not None)
    finally:
        t.RV_DIR = orig

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    return 0 if not FAIL else 1

if __name__ == "__main__":
    sys.exit(run())
