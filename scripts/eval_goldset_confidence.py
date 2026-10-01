#!/usr/bin/env python3
"""eval_goldset_confidence.py —— 字段级置信度评估（T-054 · RV-20261001-362）。

在 goldset 真值比对上叠加置信度评估：
- 三档错误率：按字段置信度分档（high/mid/low），统计各档字段错误率 + 95% Wilson 区间
  （n<5 标 insufficient，不报区间）
- 排序质量：字段置信度升序 vs 字段是否错误 → AUC（错误样本应集中在低置信度端）
  （AUC<0.5=反序，≈0.5=无区分，>0.7=可用的排序信号；样本少时如实标注）
- 分层报告 L1/L2/L3（与 run_goldset 同口径；L3 无本地副本时 skipped）

用法：
    python3 scripts/eval_goldset_confidence.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.confidence import field_confidence, bucket, wilson_interval  # noqa: E402
from app.parser import parse_document  # noqa: E402
from scripts.gold_labeler import derive_issues, _load_l3_truth  # noqa: E402
from scripts.run_goldset import FIELDS, _field_match, _norm  # noqa: E402

PRIVATE_IDX = ROOT / "benchmark/private/real_private_index.json"


def _field_error(field: str, truth: str, inv) -> bool:
    """字段错误：truth 存在且解析值不匹配。"""
    if truth is None or str(truth) == "":
        return False
    return not _field_match(field, truth, getattr(inv, field, None))


def _auc(pairs: list[tuple[float, int]]) -> float:
    """置信度(升序) vs 错误(0/1)：错误样本应在低置信端 → AUC。
    秩：按 (score, error) 升序（平局时错误=1 排后=保守）。"""
    pos = sum(1 for _, e in pairs if e == 1)
    neg = len(pairs) - pos
    if pos == 0 or neg == 0:
        return float("nan")
    s = sorted(enumerate(pairs), key=lambda x: (x[1][0], x[1][1]))
    rank_sum = sum(i + 1 for i, (_, (_, err)) in enumerate(s) if err == 1)
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="结果落盘 benchmark/results/confidence_report.json")
    ap.add_argument("--tier", choices=["L1", "L2", "L3"], default=None)
    args = ap.parse_args()

    gs = json.loads((ROOT / "benchmark/goldset/goldset.json").read_text(encoding="utf-8"))
    samples = [s for s in gs["samples"] if args.tier is None or s["source_tier"] == args.tier]

    l3_truth = {}
    if any(s["source_tier"] == "L3" for s in samples) and PRIVATE_IDX.exists():
        l3_truth = _load_l3_truth(PRIVATE_IDX, samples)

    rows, buckets = [], {"high": {"err": 0, "n": 0}, "mid": {"err": 0, "n": 0}, "low": {"err": 0, "n": 0}}
    auc_pairs: list[tuple[float, int]] = []

    for s in samples:
        path = ROOT / s["file"]
        if not path.exists():
            rows.append({"sample_id": s["sample_id"], "tier": s["source_tier"], "skipped": True,
                         "reason": "样本本体缺失"})
            continue
        truth = s.get("truth") or l3_truth.get(s["sample_id"]) or {}
        try:
            inv = parse_document(path.read_bytes())
        except Exception as e:
            rows.append({"sample_id": s["sample_id"], "tier": s["source_tier"], "skipped": True,
                         "reason": f"{type(e).__name__}: {str(e)[:50]}"})
            continue
        conf = field_confidence(inv)
        field_rows = {}
        for f in FIELDS:
            if f not in truth or truth[f] is None or str(truth[f]) == "":
                continue
            err = _field_error(f, truth[f], inv)
            sc = conf.get(f, 0.0)
            bk = bucket(sc)
            buckets[bk]["n"] += 1
            buckets[bk]["err"] += 1 if err else 0
            auc_pairs.append((sc, 1 if err else 0))
            field_rows[f] = {"conf": sc, "bucket": bk, "error": err}
        rows.append({"sample_id": s["sample_id"], "tier": s["source_tier"], "skipped": False,
                     "parse_path": (inv.raw_fields or {}).get("parse_path", ""),
                     "fields": field_rows})

    # 三档报告
    tier_report: dict[str, dict] = {}
    for tier in ("L1", "L2", "L3"):
        t_rows = [r for r in rows if r.get("tier") == tier and not r.get("skipped")]
        tier_report[tier] = {"n": len(t_rows), "skipped": sum(1 for r in rows if r.get("tier") == tier and r.get("skipped"))}

    bucket_report = {}
    for bk, st in buckets.items():
        wi = wilson_interval(st["err"], st["n"])
        bucket_report[bk] = {
            "error_rate": round(st["err"] / st["n"], 4) if st["n"] else None,
            "err/n": f"{st['err']}/{st['n']}",
            "wilson95": str(wi) if wi else "insufficient(n<5)",
        }
    auc = _auc(auc_pairs)

    report = {
        "goldset_version": gs["goldset_version"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "字段级置信度评估（RV-20261001-362 修订）：conf=路径先验×字段证据惩罚×OCR行P25；"
                "AUC=置信度升序 vs 错误；n<5 档位不报 Wilson 区间",
        "buckets": bucket_report,
        "auc": round(auc, 4) if auc == auc else None,
        "auc_note": ("错误样本不足，AUC 仅参考" if sum(1 for _, e in auc_pairs if e == 1) < 5 else "有效"),
        "tiers": tier_report,
        "rows": rows,
    }

    print(f"goldset {gs['goldset_version']} 字段级置信度评估\n")
    for bk, st in bucket_report.items():
        print(f"[{bk}] 错误率 {st['error_rate']} ({st['err/n']}) | Wilson95 {st['wilson95']}")
    print(f"AUC（置信度升序 vs 错误）: {report['auc']}（{report['auc_note']}）")
    for tier, tr in tier_report.items():
        print(f"[{tier}] 参与 {tr['n']} 样本 | skipped {tr['skipped']}")

    if args.json:
        out = ROOT / "benchmark/results/confidence_report.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已落盘：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
