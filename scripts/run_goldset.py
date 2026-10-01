#!/usr/bin/env python3
"""run_goldset.py —— 基准集真值比对一键跑分（T-053，RV-20261001-360 修订）。

与旧 benchmark.py 的本质区别：
- 旧版"reconcile"是 parser 输出自洽（synthetic 无真值文件、official 组硬编码 false）——不能作为基线；
- 本脚本对每个样本做**真值比对**：parsed 对比"应能解析"、字段逐项比对 truth、派生标签（gold_labeler，独立于
  规则引擎）对比产品 review_needed 输出 → 漏报率/误报率。
- **旧指标与新指标不可同表比较**（README 已声明）：旧 latest.json 100% reconcile 是自证/硬编码，属先行版。

指标口径（分层报告 L1/L2/L3，绝不合并单一准确率）：
- 解析成功率   : 成功解析出有效发票 / 样本总数
- 字段准确率   : 逐字段比对（truth 存在字段）/ 可比字段总数（含 发票号/日期/金额/税额/合计/销方/购方）
- 漏报率       : 该报（expected_issues 非空）而未报（review_needed=false）/ 应报总数
- 误报率       : 不该报而报（review_needed=true）/ 不应报总数
- 端到端耗时   : 单张平均 / P95

用法：
    python3 scripts/run_goldset.py            # 全清单跑分（L3 需本地 private）
    python3 scripts/run_goldset.py --json     # 结果落盘 benchmark/results/goldset_report.json
    python3 scripts/run_goldset.py --tier L2  # 只跑指定分层
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.parser import parse_document  # noqa: E402
from scripts.gold_labeler import derive_issues, _load_l3_truth  # noqa: E402

FIELDS = ("invoice_no", "issue_date", "amount", "tax", "total", "buyer_name", "seller_name")
PRIVATE_IDX = ROOT / "benchmark/private/real_private_index.json"


def _dec_eq(a: str, b: str) -> bool:
    """金额字段比对：Decimal 归一化容差。"""
    try:
        return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.005")
    except Exception:
        return str(a).strip() == str(b).strip()


def _norm(v) -> str:
    if isinstance(v, Decimal):
        return str(v)
    return "" if v is None else str(v)


def _field_match(field: str, truth: str, got) -> bool:
    if field in ("amount", "tax", "total"):
        return _dec_eq(_norm(truth), _norm(got))
    return _norm(truth).strip() == _norm(got).strip()


def run_sample(sample: dict, truth: dict) -> dict:
    """单个样本：解析 + 字段比对 + 派生标签比对。样本本体缺失 → skipped（复现条件未满足，非解析失败）。"""
    path = ROOT / sample["file"]
    if not path.exists():
        return {
            "sample_id": sample["sample_id"], "tier": sample["source_tier"],
            "file": sample["file"], "parsed": False, "skipped": True,
            "field_acc": {"matched": 0, "comparable": 0, "detail": {}},
            "expected_issues": derive_issues(truth), "got_review_needed": False,
            "missed": 0, "false_positive": 0, "elapsed_ms": 0.0,
            "error": "样本本体缺失（L3 复现条件=本地脱敏副本，见 README）",
        }
    t0 = time.time()
    try:
        inv = parse_document(path.read_bytes())
        parsed = bool(inv.invoice_no and inv.total > 0)
        err = ""
    except Exception as e:
        parsed, inv, err = False, None, f"{type(e).__name__}: {str(e)[:60]}"
    elapsed_ms = (time.time() - t0) * 1000

    expected = derive_issues(truth)
    expected_codes = {i["code"] for i in expected}
    got_report = bool(inv and inv.review_needed)

    # 字段比对（仅比对 truth 中存在的字段）
    matched, comparable = 0, 0
    field_detail = {}
    if inv and truth:
        for f in FIELDS:
            if f in truth and truth[f] is not None:
                comparable += 1
                ok = _field_match(f, truth[f], getattr(inv, f, None))
                matched += 1 if ok else 0
                field_detail[f] = "ok" if ok else f"truth={truth[f]}|got={_norm(getattr(inv, f, None))}"

    # 漏报/误报（按"该报=expected 非空"口径）
    if expected_codes:
        miss = 0 if got_report else 1
        false_pos = 0
    else:
        miss = 0
        false_pos = 1 if got_report else 0

    return {
        "sample_id": sample["sample_id"], "tier": sample["source_tier"],
        "file": sample["file"], "parsed": parsed, "skipped": False,
        "field_acc": {"matched": matched, "comparable": comparable, "detail": field_detail},
        "expected_issues": expected,
        "got_review_needed": got_report,
        "missed": miss, "false_positive": false_pos,
        "elapsed_ms": round(elapsed_ms, 1),
        "error": err,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="结果落盘 benchmark/results/goldset_report.json")
    ap.add_argument("--tier", choices=["L1", "L2", "L3"], default=None)
    args = ap.parse_args()

    gs = json.loads((ROOT / "benchmark/goldset/goldset.json").read_text(encoding="utf-8"))
    samples = [s for s in gs["samples"] if args.tier is None or s["source_tier"] == args.tier]

    # L3 真值补全（本地 private；hash 校验 fail-closed——数据源不一致禁止跑 L3）
    l3_truth = {}
    if any(s["source_tier"] == "L3" for s in samples):
        if not PRIVATE_IDX.exists():
            print("[run_goldset] L3 真值源缺失（本地 private 索引）→ L3 样本走 skipped（复现条件见 README）",
                  file=sys.stderr)
        else:
            cur = hashlib.sha256(PRIVATE_IDX.read_bytes()).hexdigest()
            if cur != gs.get("sha256_private_index"):
                print(f"[run_goldset] FAIL：private 索引 hash 与清单冻结不一致（清单={gs.get('sha256_private_index')} 当前={cur}）"
                      f"→ 数据源被修改/过期，L3 本轮全部 skipped（fail-closed）", file=sys.stderr)
            else:
                l3_truth = _load_l3_truth(PRIVATE_IDX, samples)

    rows = [run_sample(s, s.get("truth") or l3_truth.get(s["sample_id"]) or {}) for s in samples]

    # 分层聚合
    by_tier: dict[str, dict] = {}
    for r in rows:
        t = by_tier.setdefault(r["tier"], {"n": 0, "skipped": 0, "parsed": 0, "field_m": 0, "field_c": 0,
                                           "should_report": 0, "missed": 0, "no_report": 0, "fp": 0,
                                           "times": []})
        t["n"] += 1
        if r.get("skipped"):
            t["skipped"] += 1
            continue
        t["parsed"] += 1 if r["parsed"] else 0
        t["field_m"] += r["field_acc"]["matched"]
        t["field_c"] += r["field_acc"]["comparable"]
        if r["expected_issues"]:
            t["should_report"] += 1
            t["missed"] += r["missed"]
        else:
            t["no_report"] += 1
            t["fp"] += r["false_positive"]
        t["times"].append(r["elapsed_ms"])

    report = {"goldset_version": gs["goldset_version"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "note": "真值比对口径（RV-20261001-360 修订）；旧 benchmark.py 自洽 reconcile 不可与本报告同表比较",
              "tiers": {}, "rows": rows}
    for tier, t in by_tier.items():
        times = sorted(t["times"])
        p95 = times[int(len(times) * 0.95) - 1] if times else 0
        report["tiers"][tier] = {
            "n": t["n"],
            "skipped": t["skipped"],
            "parse_rate": f"{t['parsed']}/{t['n']}",
            "field_accuracy": round(t["field_m"] / t["field_c"], 4) if t["field_c"] else None,
            "field_comparable": f"{t['field_m']}/{t['field_c']}",
            "miss_rate": f"{t['missed']}/{t['should_report']}" if t["should_report"] else "0/0",
            "false_positive_rate": f"{t['fp']}/{t['no_report']}" if t["no_report"] else "0/0",
            "avg_ms": round(statistics.fmean(t["times"]), 1) if t["times"] else 0,
            "p95_ms": round(p95, 1),
        }

    # 控制台输出
    print(f"goldset {gs['goldset_version']} | 样本 {len(rows)} 条（skipped=样本本体缺失，不计入指标）\n")
    for tier, t in report["tiers"].items():
        print(f"[{tier}] 解析成功率 {t['parse_rate']} | 字段准确率 {t['field_accuracy']} ({t['field_comparable']}) "
              f"| 漏报 {t['miss_rate']} | 误报 {t['false_positive_rate']} | 耗时 avg {t['avg_ms']}ms / P95 {t['p95_ms']}ms"
              + (f" | skipped {t['skipped']}" if t['skipped'] else ""))
    miss_total = sum(t["missed"] for t in by_tier.values())
    fp_total = sum(t["fp"] for t in by_tier.values())
    print(f"\n合计：漏报 {miss_total} | 误报 {fp_total}")

    if args.json:
        out = ROOT / "benchmark/results/goldset_report.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已落盘：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
