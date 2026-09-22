"""风险报告组装（P1 技术方案第 3 节 · DeepSeek 评审采纳 + 2026-09-22 二轮审查修订 + G1 规则包/证据链）。

信息架构（自上而下）：
① 汇总条：文件数 / 成功解析票数 / 金额合计 / 未见异常 / 待复核 / 解析失败
② 风险清单：按严重度降序，每条含规则名/票号/命中字段/证据链/建议
③ 发票明细：关键字段 + 状态标签（含字段缺失提示）
④ 规则清单与规则包版本、生效日期、生成时间、批次标识
⑤ 免责（固定文案，单一来源由后端注入前端）

红线：本模块不产出"拦截/拒报/对外合规结论"——只有提示与建议。
措辞纪律：不用"通过/合规"字样（避免隐性合规结论），用"未见异常"。
G1：规则清单单一来源（引用 rules.RULESET_META，修复 R8 曾缺失于本地清单的漂移）；
     risk_list 输出结构化证据链 evidence_chain（可审计定位：字段/原文/行号/计算）。
"""
from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import asdict
from decimal import Decimal

from .models import EvidenceLink, Finding, NormalizedInvoice
from .rules import RULESET_META, RULESET_VERSION

DISCLAIMER = (
    "基于 XML 结构化数据的规则预审，不替代人工复核与税务判断；"
    "命中项需核原始凭证，未命中不等于合规；规则版本与覆盖范围已列明。"
)
SCOPE_NOTE = "本报告基于数电票 XML 结构化数据的确定性规则生成；公开+合成集验证通过，真实分布未验证。"


def _money(value: Decimal | None) -> str:
    """金额 → 字符串：防 None/非 Decimal/NaN，统一普通计数法（禁科学计数）。
    展示格式化不影响审计口径，原始值以接口字符串为准。"""
    if value is None:
        return "—"
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except Exception:
            return "—"
    if not value.is_finite():
        return "—"
    return format(value, "f")


def _invoice_status(inv: NormalizedInvoice, review_ids: set[str]) -> str:
    """状态优先级：数据不完整（无票号）> 待复核（命中规则）> 待复核（字段缺失）> 未见异常。"""
    if not inv.invoice_no:
        return "数据不完整"
    if inv.invoice_no in review_ids:
        return "待复核"
    if inv.parse_warnings:
        return "待复核（字段缺失）"
    return "未见异常"


def build_report(
    invoices: list[NormalizedInvoice],
    findings: list[Finding],
    failed: list[dict],
    ruleset_version: str,
    file_count: int | None = None,
    rule_states: dict[str, str] | None = None,
) -> dict:
    """组装一页风险报告 JSON。rule_states：{rule_id: 命中/未命中/未执行（…）} 三态。"""
    review_ids = {f.invoice_no for f in findings if f.invoice_no != "-"}

    # ① 汇总（按"张"计数；"未见异常"= 无命中且无解析告警）
    total_amount = sum((i.total for i in invoices), Decimal("0"))
    statuses = [_invoice_status(i, review_ids) for i in invoices]
    review_count = sum(1 for s in statuses if s != "未见异常")
    no_finding_count = len(invoices) - review_count

    summary = {
        "file_count": file_count if file_count is not None else len(invoices) + len(failed),
        "total_count": len(invoices),          # 成功解析的发票数（失败文件见 failed_count）
        "total_amount": _money(total_amount),  # 仅含成功解析发票，失败文件金额未知
        "no_finding_count": no_finding_count,
        "review_count": review_count,
        "failed_count": len(failed),
    }

    # ② 风险清单（findings 已按严重度/置信度排序；evidence_chain 结构化证据链）
    risk_list = [
        {
            "rule_id": f.rule_id,
            "severity": f.severity,
            "confidence": f.confidence,
            "invoice_no": f.invoice_no,
            "field": f.field,
            "message": f.message,
            "evidence": f.evidence,
            "suggestion": f.suggestion,
            "evidence_chain": [_evidence_link_to_dict(e) for e in f.evidence_chain],
        }
        for f in findings
    ]

    # ③ 发票明细（raw_fields 默认不外传，防敏感字段外泄；调试接口才返回）
    invoice_list = [
        {
            "invoice_no": i.invoice_no or "(空)",
            "invoice_type": i.invoice_type,
            "issue_date": i.issue_date,
            "amount": _money(i.amount),
            "tax": _money(i.tax),
            "total": _money(i.total),
            "seller_name": i.seller_name,
            "status": _invoice_status(i, review_ids),
            "warning_count": len(i.parse_warnings),
            "item_count": len(i.items),  # G0 行级：明细行数（行级展示随 D 阶段交互上线）
        }
        for i in invoices
    ]
    failed_list = [{"file": f["name"], "error": f["error"]} for f in failed]

    # ④ 规则三态（里程碑评审）：未执行的规则必须可见，禁止沉默；规则包单一来源（G1）
    rule_states = rule_states or {}
    rules = [{**m, "state": rule_states.get(m["rule_id"], "未执行")} for m in RULESET_META["rules"]]
    state_counts = {"命中": 0, "未命中": 0, "未执行": 0}
    for m in rules:
        st = m["state"]
        state_counts["命中" if st == "命中" else ("未命中" if st == "未命中" else "未执行")] += 1

    return {
        "summary": summary,
        "findings": risk_list,
        "invoices": invoice_list,
        "failed": failed_list,
        "ruleset_version": ruleset_version,
        "ruleset": {
            "version": RULESET_META["version"],
            "name": RULESET_META["name"],
            "effective_date": RULESET_META["effective_date"],
            "scope_note": RULESET_META["scope_note"],
        },
        "rules": rules,
        "rules_summary": {
            "enabled_count": len(rules),
            **state_counts,
            "note": "未执行的规则因数据不足或未配置未判定，不计入命中；仅列出已启用规则",
        },
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "batch_id": uuid.uuid4().hex[:12],
        "scope_note": SCOPE_NOTE,
        "disclaimer": DISCLAIMER,
    }


def _evidence_link_to_dict(e: EvidenceLink) -> dict:
    """证据链单条 → JSON dict（G1 可审计定位）。"""
    return {
        "field": e.field,
        "raw": e.raw,
        "value": e.value,
        "row": e.row,
        "note": e.note,
    }


def invoice_to_dict(inv: NormalizedInvoice) -> dict:
    """单个 NormalizedInvoice → JSON 安全 dict（Decimal 转字符串；不含 raw_fields）。"""
    d = asdict(inv)
    for k in ("amount", "tax", "total"):
        d[k] = _money(d[k])
    d.pop("raw_fields", None)
    return d
