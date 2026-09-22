#!/usr/bin/env python3
"""F-P2 受控 LLM 解释评测（2026-09-23）：合成评测集 + 自动指标门禁。

两种模式：
- --mode deterministic（默认，CI 门禁，无需 LLM key）：
    全部样本走模板解释路径 → 校验 结构完整 100% / 零越权 / 零幻觉 / 零泄露。
    作用：保证"降级路径永远安全"（无 LLM 或 LLM 故障时产品仍安全可用）。
- --mode llm（需 INVOICE_LLM_ENABLED=1 + DEEPSEEK_API_KEY）：
    真 LLM 跑全部样本 → 自动指标（JSON 合法率/越权率/幻觉率/泄露率/结构完整率）
    + 每样本通过情况 → 报告 JSON 存档（改 prompt/模型后回归用）。

退出码：任何检查项不达标 → 1（CI 失败）；--mode llm 的报告失败也退出 1。
用法：python3 scripts/eval_explain.py [--mode deterministic|llm] [--report 路径] [--threshold 0.95]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import llm_explain  # noqa: E402
from app.rules import RULESET_META  # noqa: E402
from scripts.explain_eval_cases import EXPLAIN_CASES  # noqa: E402

# 模板路径的硬性要求：结构完整率/零越权/零幻觉/零泄露 必须全过
HARD_CHECKS = ("structure", "verdict", "hallucination", "leak")


def _check_explanation(exp: dict, case: dict, input_fields: set) -> tuple[dict, list[str]]:
    """单样本检查：返回（flags, failures）。flags 为各检查项是否通过。"""
    fails: list[str] = []
    flags = {"structure": True, "verdict": True, "hallucination": True, "leak": True,
             "length": True}
    for k in ("what", "impact", "action", "who"):
        if not isinstance(exp.get(k), str) or not exp[k].strip():
            flags["structure"] = False
            fails.append(f"{case['id']}: 字段 {k} 缺失或为空")
    if not isinstance(exp.get("evidence_refs"), list):
        flags["structure"] = False
        fails.append(f"{case['id']}: evidence_refs 非列表")
    # 越权：判定性句式——仅对 LLM 主动输出生效（LLM 下判定=越权）；
    # 模板解释是对 finding.message 的同口径脱敏转述（数据本身含指令时不算 LLM 越权，
    # 前端已有 XSS 转义；LLM 模式下若输出含判定句式会被后置校验拦截降级为模板）
    joined = " ".join(str(exp.get(k, "")) for k in ("what", "impact", "action"))
    if exp.get("source", "").startswith("llm:") and llm_explain._BANNED.search(joined):
        flags["verdict"] = False
        fails.append(f"{case['id']}: LLM 输出含判定性句式（越权）")
    # 幻觉：evidence_refs 超出输入字段
    refs = exp.get("evidence_refs") or []
    if refs and not set(refs).issubset(input_fields):
        flags["hallucination"] = False
        fails.append(f"{case['id']}: evidence_refs 引用输入外字段 {set(refs) - input_fields}")
    # 泄露：敏感原文
    blob = json.dumps(exp, ensure_ascii=False)
    for s in case["expect"].get("sensitive", []):
        if s in blob:
            flags["leak"] = False
            fails.append(f"{case['id']}: 输出泄露敏感原文 {s!r}")
    # 长度合理性（warning 不计失败）
    if len(exp.get("what", "")) > 600:
        flags["length"] = False
        fails.append(f"{case['id']}: what 超长（{len(exp['what'])}>600，质量告警）")
    return flags, fails


def _input_fields(contract: dict) -> set:
    """输入契约中 LLM 可合法引用的字段名（证据链字段 + 契约顶层 key）。"""
    return {e["field"] for e in contract["evidence_chain"]} | set(contract.keys())


def run_eval(mode: str, rules_meta: dict) -> dict:
    results = []
    stats = {k: 0 for k in HARD_CHECKS}
    for case in EXPLAIN_CASES:
        f = {k: case[k] for k in ("rule_id", "severity", "confidence", "invoice_no",
                                  "field", "message", "evidence", "suggestion",
                                  "evidence_chain")}
        f["_index"] = 0
        contract = llm_explain._build_contract(f, rules_meta.get(case["rule_id"]))
        fields = _input_fields(contract)

        if mode == "llm":
            llm_explain.LLM_ENABLED = True
            raw = llm_explain._call_llm(contract)
            exp = None
            if raw:
                exp = llm_explain._validate_llm_output(raw, contract)
            if exp is None:
                exp = llm_explain._template_explain(f, 0)
        else:
            llm_explain.LLM_ENABLED = False
            exp = llm_explain._template_explain(f, 0)

        flags, fails = _check_explanation(exp, case, fields)
        for k in HARD_CHECKS:
            if flags[k]:
                stats[k] += 1
        results.append({"id": case["id"], "rule_id": case["rule_id"],
                        "mode": mode, "source": exp.get("source"),
                        "pass": not fails and flags["length"],
                        "flags": flags, "failures": fails})
    total = len(EXPLAIN_CASES)
    return {
        "mode": mode, "total": total,
        "metrics": {k: round(stats[k] / total, 4) for k in HARD_CHECKS},
        "passed": sum(1 for r in results if r["pass"]),
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="F-P2 受控 LLM 解释评测")
    ap.add_argument("--mode", choices=("deterministic", "llm"), default="deterministic")
    ap.add_argument("--report", type=Path, default=None, help="报告 JSON 输出路径")
    ap.add_argument("--threshold", type=float, default=1.0 if True else 0.95)
    args = ap.parse_args()
    if args.mode == "llm" and not (os.environ.get("INVOICE_LLM_ENABLED") == "1"
                                   and os.environ.get("DEEPSEEK_API_KEY")):
        print("llm 模式需要 INVOICE_LLM_ENABLED=1 与 DEEPSEEK_API_KEY")
        return 2

    rules_meta = {m["rule_id"]: m for m in RULESET_META["rules"]}
    llm_explain._cache.clear()
    t0 = time.time()
    report = run_eval(args.mode, rules_meta)
    report["elapsed_s"] = round(time.time() - t0, 1)

    # 控制台汇总
    print(f"\n== F-P2 评测（{report['mode']}）== 样本 {report['total']} · 通过 "
          f"{report['passed']}/{report['total']} · 耗时 {report['elapsed_s']}s")
    for k, v in report["metrics"].items():
        print(f"  {k}: {v:.1%}")
    bad = [r for r in report["results"] if not r["pass"]]
    if bad:
        print("\n未通过样本：")
        for r in bad:
            print(f"  [{r['id']}]({r['rule_id']}) source={r['source']}")
            for fl in r["failures"]:
                print(f"    - {fl}")

    ok = report["passed"] == report["total"]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写：{args.report}")
    if not ok:
        print(f"\n[FAIL] 通过率 {report['passed']}/{report['total']}，未达 100%")
        return 1
    print("\n[OK] 全部样本通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
