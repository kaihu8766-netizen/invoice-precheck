"""规则引擎服务化（P1 技术方案第 2 节 · DeepSeek 评审采纳 + 2026-09-22 代码审查修订）。

契约：
- 输入：list[NormalizedInvoice]（整批——R1/R3/R6 是跨票规则，逐张调用会失效）
- 输出：list[Finding]（rule_id / severity / confidence 双标签 + ruleset_version 溯源）
- 红线保护：关键字段缺失或企业主体未配置时，禁止产出"确定"级对外结论（降级为低危/疑似提示）
- 规则隔离：单条规则异常不拖垮整批（逐规则 try/except，异常降级为批次级 Finding）
- 规则分级（validation-week 规则清单）：确定性规则（A 级）可上线；需真实数据的标疑似。
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .models import Finding, NormalizedInvoice

RULESET_VERSION = "0.1.1"

# 严重度/置信度常量（双标签）
_SEV_HIGH = "高"
_SEV_MED = "中"
_SEV_LOW = "低"
_CONF_SURE = "确定"
_CONF_MAYBE = "疑似"


def _mask_taxid(value: str) -> str:
    """evidence 中的税号脱敏：保留前 2 后 2。"""
    if len(value) >= 8:
        return value[:2] + "****" + value[-2:]
    return value


@dataclass
class RulesConfig:
    """企业规则配置。

    注意：company_name / company_taxid **无默认值**（防未配置时 R2 把全部发票误判为抬头不符）。
    P1 调用方未配置时 R2 自动降级为"未执行"提示。
    """

    company_name: str | None = None
    company_taxid: str | None = None
    # 类别限额：差旅超标按财政部基准做"可能超标"提示，企业可自定；无配置则用默认限额并显式告警
    category_limits: dict[str, Decimal] = field(default_factory=lambda: {
        "差旅": Decimal("5000"),
        "招待": Decimal("3000"),
        "办公": Decimal("2000"),
        "交通": Decimal("1000"),
        "其他": Decimal("5000"),
    })
    default_limit: Decimal = Decimal("5000")  # 类别不在配置表时使用，并在报告注明
    serial_tail_len: int = 4          # 连号取票号末 N 位
    serial_max_gap: int = 2           # 窗口内相邻最大差值
    serial_min_count: int = 3         # 窗口内最少票数（≥3 才提示）
    concentrate_threshold: int = 6    # 同供应商同日票数阈值
    max_carry_days: int = 365         # 开票距报销最长天数


def _finding(rule_id: str, inv: NormalizedInvoice, severity: str, confidence: str,
             field_: str, message: str, evidence: str, suggestion: str = "") -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        confidence=confidence,
        invoice_no=inv.invoice_no,
        field=field_,
        message=message,
        evidence=evidence,
        suggestion=suggestion,
        ruleset_version=RULESET_VERSION,
    )


def _tail(inv: NormalizedInvoice, n: int) -> int | None:
    """票号末 N 位数值；空串/长度不足/非数字 → None（不击穿批次）。"""
    s = (inv.invoice_no or "").strip()
    if len(s) >= n and s[-n:].isdigit():
        return int(s[-n:])
    return None


# ---------- 规则实现（纯函数，便于独立测试） ----------

def r1_duplicate(invoices: list[NormalizedInvoice]) -> list[Finding]:
    """重复报销：发票号码+开票日期+价税合计 完全一致 → 组内>1 全部标记；
    整文件重复上传（source_hash 相同）也属确定性重复。

    红线保护：票号缺失或价税合计<=0 的票不参与判重（数据缺失≠合规事实），降级为低危提示。
    """
    groups: dict[tuple, list[NormalizedInvoice]] = defaultdict(list)
    hash_groups: dict[str, list[NormalizedInvoice]] = defaultdict(list)
    incomplete: list[NormalizedInvoice] = []
    for inv in invoices:
        if not inv.invoice_no or inv.total <= 0:
            incomplete.append(inv)
            continue
        key = (inv.invoice_no, inv.issue_date, f"{inv.total:.2f}")
        groups[key].append(inv)
        if inv.source_hash:
            hash_groups[inv.source_hash].append(inv)

    out: list[Finding] = []
    for inv in incomplete:
        out.append(_finding(
            "R1", inv, _SEV_LOW, _CONF_MAYBE, "invoice_no",
            "数据不完整：无法判重（票号缺失或价税合计<=0）",
            f"票号：{inv.invoice_no or '(空)'}；价税合计：{inv.total}",
        ))
    for key, group in groups.items():
        if len(group) > 1:
            for inv in group:
                others = "、".join(f"与 {g.invoice_no}" for g in group if g is not inv)
                out.append(_finding(
                    "R1", inv, _SEV_HIGH, _CONF_SURE, "invoice_no",
                    "重复报销：相同票号+日期+金额出现多次",
                    f"{key[0]} / {key[1]} / 金额 {key[2]}（{others}）",
                    "核查是否为同一张发票重复报销",
                ))
    for h, group in hash_groups.items():
        if len(group) > 1:
            first = group[0]
            out.append(_finding(
                "R1", first, _SEV_MED, _CONF_SURE, "source_hash",
                "整文件重复上传：相同来源文件出现多次",
                f"文件哈希 {h} 出现 {len(group)} 次",
                "同一文件重复导入，确认是否重复录入",
            ))
    return out


def r2_header(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """抬头/税号不符：购买方名称、税号与企业主体不匹配。

    未配置企业主体 → 输出"R2 未执行"提示（不误报）；
    名称/税号为空 → 记为疑似（明示未检查，避免漏报无提示）。
    """
    out: list[Finding] = []
    if not cfg.company_name or not cfg.company_taxid:
        if invoices:
            out.append(Finding(
                rule_id="R2", severity=_SEV_LOW, confidence=_CONF_MAYBE,
                invoice_no="-", field="config",
                message="R2 未执行：未配置企业主体（抬头/税号校验需要企业名称与税号）",
                evidence="请在规则配置中填写 company_name / company_taxid",
                ruleset_version=RULESET_VERSION,
            ))
        return out
    for inv in invoices:
        if not inv.buyer_name or not inv.buyer_taxid:
            out.append(_finding(
                "R2", inv, _SEV_LOW, _CONF_MAYBE, "buyer_name/buyer_taxid",
                "抬头数据不完整：购买方名称或税号缺失，未校验",
                f"名称：{inv.buyer_name or '(空)'}；税号：{_mask_taxid(inv.buyer_taxid) or '(空)'}",
                "补齐抬头信息后复核",
            ))
            continue
        norm_name = inv.buyer_name.strip().replace(" ", "")
        norm_cfg = cfg.company_name.strip().replace(" ", "")
        if norm_name != norm_cfg or inv.buyer_taxid.strip() != cfg.company_taxid.strip():
            out.append(_finding(
                "R2", inv, _SEV_HIGH, _CONF_SURE, "buyer_name/buyer_taxid",
                "抬头/税号与企业主体信息不符",
                f"票面抬头：{inv.buyer_name} / {_mask_taxid(inv.buyer_taxid)}；"
                f"企业主体：{cfg.company_name} / {_mask_taxid(cfg.company_taxid)}",
                "核对发票抬头是否开错（退票重开或补充说明）",
            ))
    return out


def r3_serial(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """连号异常：同一供应商（名称+税号分组）票号末 N 位连续或间隔极小（窗口内≥3 张）。"""
    by_seller: dict[tuple, list[NormalizedInvoice]] = defaultdict(list)
    incomplete: list[NormalizedInvoice] = []
    for inv in invoices:
        if not inv.seller_name or _tail(inv, cfg.serial_tail_len) is None:
            incomplete.append(inv)
            continue
        by_seller[(inv.seller_name, inv.seller_taxid)].append(inv)

    out: list[Finding] = []
    for inv in incomplete:
        out.append(_finding(
            "R3", inv, _SEV_LOW, _CONF_MAYBE, "invoice_no",
            "数据不完整：供应商缺失或票号不可解析，未做连号检查",
            f"供应商：{inv.seller_name or '(空)'}；票号：{inv.invoice_no or '(空)'}",
        ))
    for (seller, taxid), group in by_seller.items():
        if len(group) < cfg.serial_min_count:
            continue
        tails = sorted(int(g.invoice_no[-cfg.serial_tail_len:]) for g in group)
        found = False
        for i in range(len(tails)):
            if found:
                break
            for j in range(i + 1, len(tails)):
                if tails[j] - tails[i] <= cfg.serial_max_gap:
                    window = sorted(t for t in tails if tails[j] - cfg.serial_max_gap <= t <= tails[j])
                    if len(window) >= cfg.serial_min_count:
                        marked = [g for g in group if int(g.invoice_no[-cfg.serial_tail_len:]) in window]
                        nums = "、".join(sorted(g.invoice_no for g in marked))
                        for g in marked:
                            out.append(_finding(
                                "R3", g, _SEV_MED, _CONF_MAYBE, "invoice_no",
                                "连号异常：同一供应商发票号码连续/间隔极小",
                                f"供应商：{seller}（{_mask_taxid(taxid)}）；票号：{nums}（末{cfg.serial_tail_len}位差值≤{cfg.serial_max_gap}）",
                                "关注同一商家集中开票是否拆分/凑票",
                            ))
                        found = True
                        break  # 每供应商只提示一组，避免重复刷屏
    return out


def r4_overlimit(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """超标准：价税合计超类别限额（口径=报销通常看价税合计；企业可自定）。
    类别缺失 → 不判定（避免误报）；类别不在配置表 → 用默认限额并注明。"""
    out: list[Finding] = []
    for inv in invoices:
        if not inv.category:
            continue
        limit = cfg.category_limits.get(inv.category)
        if limit is None:
            limit = cfg.default_limit
            note = f"（类别未配置，使用默认限额 {limit} 元）"
        else:
            note = ""
        if inv.total > limit:
            out.append(_finding(
                "R4", inv, _SEV_MED, _CONF_MAYBE, "total",
                f"可能超标：{inv.category}类限额 {limit} 元{note}",
                f"价税合计 {inv.total} 元 > 限额 {limit} 元（类别：{inv.category}）",
                "按企业报销标准复核；限额可配置",
            ))
    return out


def r6_concentrated(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """异常集中：同一供应商（名称+税号分组）同日票数≥阈值。"""
    groups: dict[tuple, list[NormalizedInvoice]] = defaultdict(list)
    incomplete: list[NormalizedInvoice] = []
    for inv in invoices:
        if not inv.seller_name or not inv.issue_date:
            incomplete.append(inv)
            continue
        groups[(inv.seller_name, inv.seller_taxid, inv.issue_date)].append(inv)

    out: list[Finding] = []
    for inv in incomplete:
        out.append(_finding(
            "R6", inv, _SEV_LOW, _CONF_MAYBE, "seller_name/issue_date",
            "数据不完整：供应商或开票日期缺失，未做集中度检查",
            f"供应商：{inv.seller_name or '(空)'}；日期：{inv.issue_date or '(空)'}",
        ))
    for (seller, taxid, date), group in groups.items():
        if len(group) >= cfg.concentrate_threshold:
            nums = "、".join(g.invoice_no for g in group)
            for g in group:
                out.append(_finding(
                    "R6", g, _SEV_MED, _CONF_MAYBE, "seller_name/issue_date",
                    "异常集中：同一供应商单日大量开票",
                    f"{seller}（{_mask_taxid(taxid)}） / {date} 共 {len(group)} 张（{nums}）",
                    "关注供应商集中开票是否异常（建议关注）",
                ))
    return out


def r7_date_anomaly(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """日期异常：开票晚于报销 / 开票晚于今天（未来日期）/ 跨期超 365 天。
    无报销日期时只查"未来日期"分支（单 XML 上传场景）。"""
    today = _dt.date.today()
    out: list[Finding] = []
    for inv in invoices:
        try:
            issue = _dt.date.fromisoformat(inv.issue_date)
        except ValueError:
            continue  # 日期解析失败由 parser 告警负责
        if issue > today:
            out.append(_finding(
                "R7", inv, _SEV_MED, _CONF_SURE, "issue_date",
                "日期异常：开票日期晚于当前日期（疑似录入/开票错误）",
                f"开票 {inv.issue_date} 晚于今天 {today}",
                "核实开票日期是否正确",
            ))
            continue
        if not inv.reimburse_date:
            continue
        try:
            reimb = _dt.date.fromisoformat(inv.reimburse_date)
        except ValueError:
            continue
        if issue > reimb:
            out.append(_finding(
                "R7", inv, _SEV_MED, _CONF_SURE, "issue_date",
                "日期异常：开票日期晚于报销日期",
                f"开票 {inv.issue_date} 晚于报销 {inv.reimburse_date}",
                "核实是否票期倒挂（开票滞后/录入错误）",
            ))
        elif (reimb - issue).days > cfg.max_carry_days:
            out.append(_finding(
                "R7", inv, _SEV_MED, _CONF_SURE, "issue_date",
                "日期异常：跨期报销（开票距报销超 365 天）",
                f"开票 {inv.issue_date} 距报销 {inv.reimburse_date} 共 {(reimb - issue).days} 天",
                "按财务制度核实跨期报销是否允许",
            ))
    return out


def run_rules(invoices: list[NormalizedInvoice], config: RulesConfig | None = None) -> list[Finding]:
    """整批执行全部规则（R1/R3/R6 跨票，必须一次入参）。

    隔离：逐规则 try/except，单条规则异常降级为批次级 Finding，不拖垮整批。
    红线保护：关键字段缺失时只产出低危/疑似提示，不产出"确定"级对外结论。
    """
    cfg = config or RulesConfig()
    findings: list[Finding] = []
    checks = [
        ("R1", lambda: r1_duplicate(invoices)),
        ("R2", lambda: r2_header(invoices, cfg)),
        ("R3", lambda: r3_serial(invoices, cfg)),
        ("R4", lambda: r4_overlimit(invoices, cfg)),
        ("R6", lambda: r6_concentrated(invoices, cfg)),
        ("R7", lambda: r7_date_anomaly(invoices, cfg)),
    ]
    for rule_id, fn in checks:
        try:
            findings.extend(fn())
        except Exception as e:  # 规则隔离：异常不拖垮整批
            findings.append(Finding(
                rule_id=rule_id, severity=_SEV_LOW, confidence=_CONF_MAYBE,
                invoice_no="-", field="system",
                message=f"规则 {rule_id} 执行异常，该规则结果未纳入本次报告",
                evidence=f"{type(e).__name__}: {e}",
                suggestion="联系开发者排查；本报告其余规则不受影响",
                ruleset_version=RULESET_VERSION,
            ))
    # 稳定排序：严重度 高>中>低 → 置信度 确定>疑似 → 规则号 → 票号
    sev_order = {_SEV_HIGH: 0, _SEV_MED: 1, _SEV_LOW: 2}
    findings.sort(key=lambda f: (sev_order.get(f.severity, 3), f.confidence != _CONF_SURE,
                                f.rule_id, f.invoice_no))
    return findings
