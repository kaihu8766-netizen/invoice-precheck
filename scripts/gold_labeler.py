#!/usr/bin/env python3
"""gold_labeler.py —— 基准集确定性派生标签器（T-053，RV-20261001-360 修订）。

只从「真值字段」的数学/格式关系派生"该报/不该报"标签，
**不调用规则引擎 R1-R11、不依赖 parser 输出** —— 杜绝"规则自己定标签又自己跑分"的循环论证。

派生规则（可扩展；每项给出判定依据）：
- reconcile_ok  : |amount + tax - total| <= 0.005（金额容差；发票勾稽关系）
- date_valid    : YYYY-MM-DD 且为合法日期（2000-2100）
- invno_valid   : 数电票号 = 20 位数字
- amount_nonneg : 金额 >= 0（负数 = 红冲/异常，该报）
- tax_nonneg    : 税额 >= 0（负数/超常该报）

用法：
    python3 scripts/gold_labeler.py --truth '{"amount":"100","tax":"13","total":"113","issue_date":"2026-08-01","invoice_no":"25300000000090000000"}'
    python3 scripts/gold_labeler.py --json  # 从 goldset.json 派生全清单 expected_issues（L3 需 --private-index）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOL = Decimal("0.005")  # 金额容差（元）


def derive_issues(truth: dict) -> list[dict]:
    """从 truth 字段派生 expected_issues。truth 缺失字段 → 跳过对应规则。"""
    issues: list[dict] = []
    try:
        amount, tax, total = (Decimal(str(truth[k])) for k in ("amount", "tax", "total"))
    except (KeyError, TypeError, ValueError):
        amount = tax = total = None

    if amount is not None and tax is not None and total is not None:
        if abs(amount + tax - total) > TOL:
            issues.append({"code": "RECONCILE", "level": "error",
                           "note": f"勾稽不符：amount+tax={amount + tax} ≠ total={total}"})
    if amount is not None and amount < 0:
        issues.append({"code": "AMOUNT_NEG", "level": "warn", "note": "金额为负（红冲/异常）"})
    if tax is not None and tax < 0:
        issues.append({"code": "TAX_NEG", "level": "warn", "note": "税额为负（红冲/异常）"})

    d = truth.get("issue_date", "")
    if d:
        ok = False
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            ok = 2000 <= dt.year <= 2100
        except ValueError:
            ok = False
        if not ok:
            issues.append({"code": "DATE_INVALID", "level": "error", "note": f"开票日期非法：{d}"})

    no = truth.get("invoice_no", "")
    if no and not (len(no) == 20 and no.isdigit()):
        issues.append({"code": "INVNO_INVALID", "level": "error",
                       "note": f"票号非 20 位数字：{no}"})

    return issues


def _load_l3_truth(index_path: Path, samples: list[dict]) -> dict[int, dict]:
    """L3 真值从本地 private 索引补全（金额真实值不入库）。返回 sample_id -> truth。"""
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    out = {}
    for s in samples:
        if s.get("source_tier") != "L3":
            continue
        ref = s.get("private_ref") or {}
        i = ref.get("index")
        if i is None or i >= len(idx):
            raise SystemExit(f"[gold_labeler] L3 条目 {s['sample_id']} private_ref.index 越界")
        out[s["sample_id"]] = idx[i]["truth"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", help="单条 truth JSON（示例输出派生标签）")
    ap.add_argument("--json", action="store_true", help="对 goldset.json 全清单派生并输出到 stdout")
    ap.add_argument("--private-index", type=Path, default=ROOT / "benchmark/private/real_private_index.json")
    args = ap.parse_args()

    if args.truth:
        issues = derive_issues(json.loads(args.truth))
        print(json.dumps(issues, ensure_ascii=False, indent=1))
        return 0

    if args.json:
        gs = json.loads((ROOT / "benchmark/goldset/goldset.json").read_text(encoding="utf-8"))
        l3 = {}
        if args.private_index.exists():
            l3 = _load_l3_truth(args.private_index, gs["samples"])
        for s in gs["samples"]:
            truth = s.get("truth") or l3.get(s["sample_id"])
            s["expected_issues"] = derive_issues(truth) if truth else []
            s["_labeler_note"] = ("L3：真值来自本地 private（hash 校验见 run_goldset）"
                                  if s.get("source_tier") == "L3" else None)
        print(json.dumps(gs, ensure_ascii=False, indent=1))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
