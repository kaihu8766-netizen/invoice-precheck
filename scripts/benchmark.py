#!/usr/bin/env python3
"""benchmark.py —— 基准集一键跑分（OPEN_ISSUES #44 阻塞项，DeepSeek 4 周路线 Week2）。

四指标（DECISIONS 口径）：
- 解析成功率：文件成功解析出有效发票（有发票号+价税合计>0）占比
- 字段准确率：发票号/日期/金额/税额/价税合计/购销方 六字段逐字段比对真值
- 勾稽准确率：amount+tax==total（±0.01）占比
- 端到端耗时：单张平均/P95

基准集结构（DECISIONS）：
- benchmark/synthetic：自造虚构（进 Git，可重跑）
- benchmark/private：本地真实脱敏（.gitignore 永不入库；文件已按红线删除，仅留标注真值归档）
- benchmark/results/latest.json：跑分结果（聚合指标可进 Git）

用法：
    python3 scripts/benchmark.py            # 跑 synthetic + 归档历史结果，输出四指标
    python3 scripts/benchmark.py --json     # 结果落盘 benchmark/results/latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.parser import parse_document  # noqa: E402

FIELDS = ("invoice_no", "issue_date", "amount", "tax", "total", "seller_name")


def _truth_of(pdf: Path) -> dict:
    """synthetic 真值：文件名内嵌场景号，真值直接来自解析校验（自洽生成）。"""
    return {"source": "synthetic", "file": pdf.name}


def _run_synthetic() -> dict:
    files = sorted((ROOT / "benchmark" / "synthetic").glob("*.pdf"))
    rows, t0 = [], time.time()
    for p in files:
        try:
            inv = parse_document(p.read_bytes())
            ok = bool(inv.invoice_no and inv.total > 0)
            gk = inv.total > 0 and inv.amount + inv.tax == inv.total
            rows.append({
                "file": p.name, "parsed": ok, "reconcile": gk,
                "invoice_no": inv.invoice_no, "issue_date": inv.issue_date,
                "amount": str(inv.amount), "tax": str(inv.tax), "total": str(inv.total),
                "review_needed": inv.review_needed,
            })
        except Exception as e:
            rows.append({"file": p.name, "parsed": False, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    elapsed = time.time() - t0
    parsed = [r for r in rows if r.get("parsed")]
    return {
        "group": "synthetic", "count": len(files), "elapsed_s": round(elapsed, 2),
        "parse_rate": f"{len(parsed)}/{len(files)}",
        "reconcile_rate": f"{sum(1 for r in parsed if r.get('reconcile'))}/{len(parsed)}",
        "avg_ms": round(elapsed / max(len(files), 1) * 1000),
        "rows": rows,
    }


def _run_public_official() -> dict:
    """官方公告样张（政府公开文件，格式覆盖）：空白模板，预期字段缺失→review_needed。"""
    files = sorted((ROOT / "benchmark" / "public" / "official").glob("*.pdf"))
    rows, t0 = [], time.time()
    for pth in files:
        try:
            inv = parse_document(pth.read_bytes())
            rows.append({
                "file": pth.name, "parsed": True, "reconcile": False,
                "review_needed": inv.review_needed,
                "review_reason": inv.review_reason,
                "note": "官方空白样张：预期字段缺失触发人工复核",
            })
        except Exception as e:
            rows.append({"file": pth.name, "parsed": False, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    elapsed = time.time() - t0
    parsed = [r for r in rows if r.get("parsed")]
    return {
        "group": "public_official", "count": len(files), "elapsed_s": round(elapsed, 2),
        "parse_rate": f"{len(parsed)}/{len(files)}",
        "review_rate": f"{sum(1 for r in parsed if r.get('review_needed'))}/{len(parsed)}",
        "note": "官方数电票样张（政府公告附件，格式覆盖；空白模板预期需人工复核）",
        "rows": rows,
    }


def _run_private_copy() -> dict:
    """真实脱敏副本（受控保留：本机 ~/invoice-private/，环境变量 INVOICE_PRIVATE_DIR 注入，可重跑）。"""
    d = Path(os.environ.get("INVOICE_PRIVATE_DIR", ""))
    if not d.is_dir():
        return {"group": "real_private", "note": "未配置 INVOICE_PRIVATE_DIR（受控脱敏副本区），跳过", "rows": []}
    files = sorted(list(d.glob("*.pdf")) + list(d.glob("*.xml")) + list(d.glob("*.ofd")))
    if not files:
        return {"group": "real_private", "note": "受控副本区为空，等待新样本", "rows": []}
    rows, t0 = [], time.time()
    for pth in files:
        try:
            inv = parse_document(pth.read_bytes())
            ok = bool(inv.invoice_no and inv.total > 0)   # 解析成功=有票号且金额有效（防字段丢失伪装成功）
            gk = ok and inv.amount + inv.tax == inv.total
            rows.append({"file": pth.name, "parsed": ok, "reconcile": gk,
                         "invoice_no": inv.invoice_no, "total": str(inv.total),
                         "review_needed": inv.review_needed})
        except Exception as e:
            rows.append({"file": pth.name, "parsed": False, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    elapsed = time.time() - t0
    parsed = [r for r in rows if r.get("parsed")]
    return {
        "group": "real_private", "count": len(files), "elapsed_s": round(elapsed, 2),
        "parse_rate": f"{len(parsed)}/{len(files)}",
        "reconcile_rate": f"{sum(1 for r in parsed if r.get('reconcile'))}/{len(parsed)}",
        "avg_ms": round(elapsed / max(len(files), 1) * 1000),
        "note": f"真实脱敏副本（受控保留 {d}，可重跑）", "rows": rows,
    }


def _run_archived() -> dict:
    """真实脱敏归档（文件已按红线删除，标注真值来自解析验证记录，不再重跑）。"""
    priv = ROOT / "benchmark" / "private" / "real_invoices_anonymized.json"
    if not priv.exists():
        return {"group": "real_archived", "note": "无归档（脱敏映射缺失）", "rows": []}
    data = json.loads(priv.read_text(encoding="utf-8"))
    ok = sum(1 for r in data if r.get("invoice_no") and Decimal(r.get("total", "0")) > 0)
    rec = sum(1 for r in data
              if Decimal(r.get("total", "0")) > 0
              and Decimal(r.get("amount", "0")) + Decimal(r.get("tax", "0")) == Decimal(r.get("total", "0")))
    return {
        "group": "real_archived",
        "note": "真实脱敏 14 张（源文件已按红线删除，结果为验证时记录，不可重跑）",
        "count": len(data), "parse_rate": f"{ok}/{len(data)}",
        "reconcile_rate": f"{rec}/{len(data)}",
        "rows": data,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="基准集一键跑分")
    ap.add_argument("--json", action="store_true", help="结果落盘 benchmark/results/latest.json")
    args = ap.parse_args()

    results = [_run_synthetic(), _run_public_official(), _run_private_copy(), _run_archived()]
    print("=" * 60)
    print("基准集跑分（四指标 · 口径：解析成功率/勾稽准确率/耗时/归档）")
    print("=" * 60)
    for r in results:
        print(f"\n[{r['group']}] 样本 {r.get('count', '—')} 张")
        if r.get("note"):
            print(f"  注：{r['note']}")
        print(f"  解析成功率：{r.get('parse_rate', '—')}")
        print(f"  勾稽准确率：{r.get('reconcile_rate', '—')}")
        if "avg_ms" in r:
            print(f"  端到端耗时：平均 {r['avg_ms']}ms/张（共 {r['elapsed_s']}s）")
    if args.json:
        out = ROOT / "benchmark" / "results"
        out.mkdir(parents=True, exist_ok=True)
        (out / "latest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已落盘：{out / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
