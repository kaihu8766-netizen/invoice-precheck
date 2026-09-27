"""规则引擎服务化（P1 技术方案第 2 节 · DeepSeek 评审采纳 + 2026-09-22 代码审查修订 + G1 规则版本化/证据链）。

契约：
- 输入：list[NormalizedInvoice]（整批——R1/R3/R6 是跨票规则，逐张调用会失效）
- 输出：list[Finding]（rule_id / severity / confidence 双标签 + ruleset_version 溯源 + evidence_chain 证据链）
- 红线保护：关键字段缺失或企业主体未配置时，禁止产出"确定"级对外结论（降级为低危/疑似提示）
- 规则隔离：单条规则异常不拖垮整批（逐规则 try/except，异常降级为批次级 Finding）
- 规则分级（validation-week 规则清单）：确定性规则（A 级）可上线；需真实数据的标疑似。
- G1（2026-09-23）：规则包单一来源 RULESET_META（版本+生效日期+政策依据），report 不再各自维护
  规则清单（实证漂移：R8 曾缺失于 report 的 RULES_META）；Finding 携带 evidence_chain（可审计定位）。
"""
from __future__ import annotations

import datetime as _dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .models import EvidenceLink, Finding, NormalizedInvoice

# ---------- G1 规则包元数据（单一权威来源；report/前端均引用此处） ----------

RULESET_VERSION = "0.3.0"
RULESET_EFFECTIVE_DATE = "2026-09-23"  # 规则包生效日期（报告溯源：按哪版规则判的）

# 政策依据说明（G1）：按主题引用法规/管理办法/业界实践，不编造条款号；
# 条款级引用与阈值校准登记为待核验项（scope_note 承载诚实声明）。
RULESET_META = {
    "version": RULESET_VERSION,
    "name": "invoice-precheck 规则包",
    "effective_date": RULESET_EFFECTIVE_DATE,
    "scope_note": "政策依据按主题引用（法规/管理办法/业界实践），条款级引用待法务/税务核验；"
                  "规则阈值（限额/连号窗口/大额上限）为占位口径，待真实数据校准。",
    "rules": [
        {"rule_id": "R1", "name": "重复报销 / 整文件重复", "severity": "高",
         "basis": "企业内部报销管理制度（防重复报销/重复录入）"},
        {"rule_id": "R2", "name": "抬头/税号校验", "severity": "高",
         "basis": "《中华人民共和国发票管理办法》发票开具基本要求；企业报销制度"},
        {"rule_id": "R3", "name": "连号异常", "severity": "中",
         "basis": "反拆分开票风险提示（业界通用实践：同日同供应商连号疑拆分凑票）"},
        {"rule_id": "R4", "name": "超标准（类别限额）", "severity": "中",
         "basis": "财政部差旅费管理办法等开支标准；企业自定限额（默认值占位待校准）"},
        {"rule_id": "R6", "name": "供应商集中度异常", "severity": "中",
         "basis": "税务风险与反洗票实践（同供应商单日大量开票）"},
        {"rule_id": "R7", "name": "日期异常", "severity": "中",
         "basis": "企业所得税税前扣除凭证管理办法（国家税务总局公告2018年第28号）跨期口径"},
        {"rule_id": "R8", "name": "金额异常类型化", "severity": "高",
         "basis": "数电票会计数据标准（勾稽关系）；差额征税与红冲规则（总局公告2024年第11号口径）"},
        {"rule_id": "R9", "name": "差额征税专项", "severity": "中",
         "basis": "GB/T《电子发票业务数据规范 第2部分：特定要素》差额征税要素 EI386（KCE 扣除额）；"
                 "服务商公开文档（金蝶/百望）差额开票口径；真实差额票字段形态待核验"},
        {"rule_id": "R10", "name": "明细行勾稽（行级一致性）", "severity": "高",
         "basis": "数电票会计数据标准（明细-合计勾稽关系）；GB/T 电子发票业务数据规范"},
        {"rule_id": "R11", "name": "红冲关联（占位）", "severity": "中",
         "basis": "国家税务总局公告2024年第11号（红字数电票开具规则）；EI390/EI391；"
                 "完整红冲关联需历史发票库（当前单票上传仅票面检查）"},
    ],
}

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


def _link(field_: str, raw: str, value: str = "", row: int = 0, note: str = "") -> EvidenceLink:
    """构造单条证据链（G1）。"""
    return EvidenceLink(field=field_, raw=raw, value=value, row=row, note=note)


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
    # P0-8 金额异常阈值（真实样本校准 09-23：31 张汽车行业票价税合计 P95≈1819 万、max≈2229 万；
    # 上限取 3000 万=真实 max×1.35 留业务余量。局限：样本行业有偏（机动车批发），
    # 办公/差旅类小额票补充样本后需复核。回收前召优先原则：宁可放过不可误报）
    max_plausible_total: Decimal = Decimal("30000000")


def _finding(rule_id: str, inv: NormalizedInvoice, severity: str, confidence: str,
             field_: str, message: str, evidence: str, suggestion: str = "",
             evidence_chain: list[EvidenceLink] | None = None) -> Finding:
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
        evidence_chain=evidence_chain or [],
    )


def _tail(inv: NormalizedInvoice, n: int) -> int | None:
    """票号末 N 位数值；空串/长度不足/非数字 → None（不击穿批次）。"""
    s = (inv.invoice_no or "").strip()
    if len(s) >= n and s[-n:].isdigit():
        return int(s[-n:])
    return None


# ---------- 规则实现（纯函数，便于独立测试） ----------

def r1_duplicate(invoices: list[NormalizedInvoice]) -> list[Finding]:
    """重复判定：发票号码+开票日期+价税合计 完全一致 → 组内>1 全部标记；
    整文件重复上传（source_hash 相同）也属确定性重复。

    红线保护：票号/开票日期缺失或价税合计<=0 的票不参与判重（数据缺失≠合规事实），
    降级为低危提示（None 短路——里程碑评审：禁止解析失败的票互相撞 key 误判重复）。
    """
    groups: dict[tuple, list[NormalizedInvoice]] = defaultdict(list)
    hash_groups: dict[str, list[NormalizedInvoice]] = defaultdict(list)
    incomplete: list[NormalizedInvoice] = []
    for inv in invoices:
        key_fields = ((inv.invoice_no or "").strip(),
                      (inv.issue_date or "").strip(),
                      f"{inv.total:.2f}")
        if not key_fields[0] or not key_fields[1] or inv.total <= 0:
            incomplete.append(inv)
            continue
        key = tuple(key_fields)
        groups[key].append(inv)
        if inv.source_hash:
            hash_groups[inv.source_hash].append(inv)

    out: list[Finding] = []
    for inv in incomplete:
        out.append(_finding(
            "R1", inv, _SEV_LOW, _CONF_MAYBE, "invoice_no",
            "数据不完整：无法判重（票号/开票日期缺失或价税合计<=0）",
            f"票号：{inv.invoice_no or '(空)'}；日期：{inv.issue_date or '(空)'}；价税合计：{inv.total}",
        ))
    for key, group in groups.items():
        if len(group) > 1:
            for inv in group:
                others = "、".join(f"与 {g.invoice_no}" for g in group if g is not inv)
                out.append(_finding(
                    "R1", inv, _SEV_HIGH, _CONF_SURE, "invoice_no",
                    "本批次内存在票号+开票日期+价税合计相同的发票",
                    f"{key[0]} / {key[1]} / 金额 {key[2]}（{others}）",
                    "核查是否为同一张发票重复报销或重复录入",
                    evidence_chain=[
                        _link("invoice_no", key[0], note="判重键①：发票号码"),
                        _link("issue_date", key[1], note="判重键②：开票日期"),
                        _link("total", f"{inv.total:.2f}", key[2], note="判重键③：价税合计"),
                        _link("source_hash", inv.source_hash or "(空)",
                              note=f"本组 {len(group)} 张票同键"),
                    ],
                ))
    for h, group in hash_groups.items():
        if len(group) > 1:
            first = group[0]
            out.append(_finding(
                "R1", first, _SEV_MED, _CONF_SURE, "source_hash",
                "整文件重复上传：相同来源文件出现多次",
                f"文件哈希 {h} 出现 {len(group)} 次",
                "同一文件重复导入，确认是否重复录入",
                evidence_chain=[
                    _link("source_hash", h[:16] + "…", h, note=f"同文件 {len(group)} 次"),
                ],
            ))
    return out


def r2_header(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """抬头/税号校验（RV-42/43 重写：税号优先分级，收票场景首版只校验购买方）。

    分级（防误报优先 + 防 fail-open）：
    - 未配置企业主体 → 整批一条"R2 未执行"低风险提示（保留原降级，不误报）
    - 票面税号无效/缺失 + 名称一致 → 低风险"无法校验"（税号缺失无法强校验）
    - 票面税号无效/缺失 + 名称不一致/缺失 → 中风险"可疑"（RV-43 补档：不得 fail-open 掉到低）
    - 税号有效一致 → 通过（名称不一致仅低风险"名称差异"提示，不报高）
    - 税号有效不一致 → 高风险"抬头/税号不符"（即使名称一致）
    - 归一化：全半角/空格/括号/大小写统一（config_store 单一实现，票面/配置共用）
    """
    from .config_store import is_plausible_tax_id, normalize_name, normalize_tax_id

    out: list[Finding] = []
    if not cfg.company_name or not cfg.company_taxid:
        if invoices:
            out.append(Finding(
                rule_id="R2", severity=_SEV_LOW, confidence=_CONF_MAYBE,
                invoice_no="-", field="config",
                message="R2 未执行：未配置企业主体（抬头/税号校验需要企业名称与税号）",
                evidence="请在设置面板填写企业名称与统一社会信用代码",
                ruleset_version=RULESET_VERSION,
            ))
        return out
    cfg_name = normalize_name(cfg.company_name)
    cfg_taxid = normalize_tax_id(cfg.company_taxid)
    for inv in invoices:
        ticket_taxid = normalize_tax_id(inv.buyer_taxid or "")
        if not is_plausible_tax_id(inv.buyer_taxid):
            # 税号缺失/占位符：名称一致→低·无法校验；名称不一致→中·可疑（防 fail-open）
            name_ok = bool(inv.buyer_name) and normalize_name(inv.buyer_name) == cfg_name
            if not name_ok:
                out.append(_finding(
                    "R2", inv, "中", _CONF_MAYBE, "buyer_taxid",
                    "票面税号缺失或无效且购买方名称与配置主体不一致：疑似非本公司发票",
                    f"票面名称：{inv.buyer_name or '(空)'}；税号：(缺失/无效)；"
                    f"企业主体：{cfg.company_name} / {_mask_taxid(cfg.company_taxid)}",
                    "核对发票抬头：若确非本公司请退回；若为解析失败请人工补录税号后复核",
                    evidence_chain=[
                        _link("buyer_name", inv.buyer_name or "(空)", normalize_name(inv.buyer_name or ""),
                              note="票面购买方名称（归一后比对）"),
                        _link("config.company_taxid", _mask_taxid(cfg.company_taxid),
                              cfg.company_taxid, note="企业主体配置税号（脱敏展示）"),
                    ],
                ))
            else:
                out.append(_finding(
                    "R2", inv, _SEV_LOW, _CONF_MAYBE, "buyer_taxid",
                    "票面税号缺失或无效，无法强校验（名称与配置主体一致）",
                    f"名称：{inv.buyer_name or '(空)'}；税号：(缺失/无效)",
                    "补齐/修正税号后复核",
                ))
            continue
        if ticket_taxid != cfg_taxid:
            out.append(_finding(
                "R2", inv, _SEV_HIGH, _CONF_SURE, "buyer_name/buyer_taxid",
                "抬头/税号与企业主体信息不符",
                f"票面购买方：{inv.buyer_name or '(空)'} / {_mask_taxid(inv.buyer_taxid)}；"
                f"企业主体：{cfg.company_name} / {_mask_taxid(cfg.company_taxid)}",
                "核对发票抬头是否开错（退票重开或补充说明）",
                evidence_chain=[
                    _link("buyer_taxid", _mask_taxid(inv.buyer_taxid), inv.buyer_taxid,
                          note="票面购买方税号（脱敏展示，归一后比对）"),
                    _link("config.company_taxid", _mask_taxid(cfg.company_taxid),
                          cfg.company_taxid, note="企业主体配置税号（脱敏展示，归一后比对）"),
                    _link("buyer_name", inv.buyer_name or "(空)", ticket_taxid,
                          note="票面购买方名称（归一后仅提示，不参与高风险判定）"),
                ],
            ))
            continue
        # 税号一致：名称仅辅助提示（低风险），不报高
        if not inv.buyer_name or normalize_name(inv.buyer_name) != cfg_name:
            out.append(_finding(
                "R2", inv, _SEV_LOW, _CONF_MAYBE, "buyer_name",
                "税号一致但名称差异或缺失：票面购买方名称与配置主体名称不一致",
                f"票面名称：{inv.buyer_name or '(空)'}；配置名称：{cfg.company_name}",
                "若为简称/括号差异可忽略；名称严重不符建议核对",
            ))
    return out


def r3_serial(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """连号异常（弱信号提示）：同一供应商（名称+税号）**同开票日**票号末 N 位连续/间隔极小
    （窗口内≥3 张）。里程碑评审口径收敛：
    - 加"同开票日"约束（同日批量开票才构成拆分嫌疑；跨天连号不再提示，降误报）；
    - 红冲票排除（红字发票编号规则不同，不参与连号判定）；
    - 置信度固定"疑似"（弱信号），输出为提示层级。
    """
    by_seller: dict[tuple, list[NormalizedInvoice]] = defaultdict(list)
    incomplete: list[NormalizedInvoice] = []
    for inv in invoices:
        if not inv.seller_name or _tail(inv, cfg.serial_tail_len) is None:
            incomplete.append(inv)
            continue
        if "红" in (inv.invoice_type or ""):
            continue  # 红冲票排除
        by_seller[(inv.seller_name, inv.seller_taxid, inv.issue_date)].append(inv)

    out: list[Finding] = []
    for inv in incomplete:
        out.append(_finding(
            "R3", inv, _SEV_LOW, _CONF_MAYBE, "invoice_no",
            "数据不完整：供应商缺失或票号不可解析，未做连号检查",
            f"供应商：{inv.seller_name or '(空)'}；票号：{inv.invoice_no or '(空)'}",
        ))
    for (seller, taxid, day), group in by_seller.items():
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
                                "R3", g, _SEV_LOW, _CONF_MAYBE, "invoice_no",
                                "同一供应商同日有发票号码连续/间隔极小",
                                f"供应商：{seller}（{_mask_taxid(taxid)}）；开票日：{day}；票号：{nums}"
                                f"（末{cfg.serial_tail_len}位差值≤{cfg.serial_max_gap}）",
                                "弱信号提示：关注同日集中开票是否拆分/凑票，需人工核实业务合理性",
                                evidence_chain=[
                                    _link("seller_name", seller, note="供应商分组键①"),
                                    _link("seller_taxid", _mask_taxid(taxid), taxid,
                                          note="供应商分组键②（脱敏展示）"),
                                    _link("issue_date", day, note="同开票日分组键③"),
                                    _link("invoice_no", nums,
                                          note=f"末{cfg.serial_tail_len}位差值≤{cfg.serial_max_gap}，共 {len(marked)} 张"),
                                ],
                            ))
                        found = True
                        break  # 每供应商每日只提示一组，避免重复刷屏
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
                evidence_chain=[
                    _link("total", f"{inv.total:.2f}", f"{inv.total:.2f}", note="票面价税合计"),
                    _link("category", inv.category or "", note="报销类别"),
                    _link("config.category_limits", f"{limit}", f"{limit}",
                          note="该类别限额（配置/默认占位）"),
                ],
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
                    evidence_chain=[
                        _link("seller_name", seller, note="供应商分组键①"),
                        _link("seller_taxid", _mask_taxid(taxid), taxid,
                              note="供应商分组键②（脱敏展示）"),
                        _link("issue_date", date, note="同开票日分组键③"),
                        _link("invoice_no", nums, note=f"同组 {len(group)} 张（≥阈值 {cfg.concentrate_threshold}）"),
                    ],
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
                evidence_chain=[
                    _link("issue_date", inv.issue_date, inv.issue_date, note="票面开票日期"),
                    _link("system.today", str(today), str(today), note="当前日期"),
                ],
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
                evidence_chain=[
                    _link("issue_date", inv.issue_date, inv.issue_date, note="票面开票日期"),
                    _link("reimburse_date", inv.reimburse_date, inv.reimburse_date,
                          note="报销日期（批次携带）"),
                ],
            ))
        elif (reimb - issue).days > cfg.max_carry_days:
            out.append(_finding(
                "R7", inv, _SEV_MED, _CONF_SURE, "issue_date",
                "日期异常：跨期报销（开票距报销超 365 天）",
                f"开票 {inv.issue_date} 距报销 {inv.reimburse_date} 共 {(reimb - issue).days} 天",
                "按财务制度核实跨期报销是否允许",
                evidence_chain=[
                    _link("issue_date", inv.issue_date, inv.issue_date, note="票面开票日期"),
                    _link("reimburse_date", inv.reimburse_date, inv.reimburse_date,
                          note="报销日期（批次携带）"),
                    _link("system.gap_days", str((reimb - issue).days),
                          str((reimb - issue).days), note=f"距报销 {(reimb - issue).days} 天（>365）"),
                ],
            ))
    return out


# ---------- P0-8 金额异常类型化（DeepSeek 评审 required_for_v1；2026-09-23 落地） ----------

# 金额异常类型（type field 承载，规则 R8 输出类型化 findings，不做一刀切"金额异常"）
ANOMALY_RECONCILE = "勾稽不符"          # amount+tax ≠ total（防御性复核：parser 已挡，规则层独立验证）
ANOMALY_NEGATIVE_NON_RED = "负数非红冲"   # 负金额但非红字票（红冲负数合法，非红负数疑似异常）
ANOMALY_ABSURD_TOTAL = "金额超合理阈值"    # 单票价税合计超合理上限（占位阈值，可配置）


def r8_amount_anomaly(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """金额异常类型化（P0-8 落地 + EI386 差额占位）。

    判定（红字/差额优先豁免误报）：
    - 勾稽不符（|amount+tax-total|>0.01）→ 高危（确定性）；parser 已拦截，此为独立防御复核
    - 负数金额且非红冲 → 高危（疑似异常）
    - 负数金额且红冲 → 通过（红冲负数合法）
    - 差额征税票 → 低危提示：勾稽按扣除后口径，KCE 专项校验 P2 待支持（占位，不误报为异常）
    - 单票价税合计绝对值超 max_plausible_total → 疑似（占位阈值，待真实数据校准）
    """
    out: list[Finding] = []
    for inv in invoices:
        if abs((inv.amount + inv.tax) - inv.total) > Decimal("0.01"):
            diff = inv.amount + inv.tax - inv.total
            out.append(_finding(
                "R8", inv, _SEV_HIGH, _CONF_SURE, "total",
                f"金额异常（{ANOMALY_RECONCILE}）：金额+税额≠价税合计",
                f"{inv.amount}+{inv.tax}={inv.amount + inv.tax} ≠ {inv.total}（差 {diff}）",
                "金额字段可能缺失/被篡改，人工核对票面",
                evidence_chain=[
                    _link("amount", f"{inv.amount:.2f}", f"{inv.amount:.2f}", note="票面金额（不含税）"),
                    _link("tax", f"{inv.tax:.2f}", f"{inv.tax:.2f}", note="票面税额"),
                    _link("total", f"{inv.total:.2f}", f"{inv.total:.2f}", note="票面价税合计"),
                    _link("calc", f"{inv.amount}+{inv.tax}={inv.amount + inv.tax}",
                          f"{inv.amount + inv.tax:.2f}", note=f"勾稽差 {diff}（容差 ±0.01）"),
                ],
            ))
            continue
        if inv.total < 0:
            if inv.is_red_letter:
                continue  # 红冲负数合法
            out.append(_finding(
                "R8", inv, _SEV_HIGH, _CONF_MAYBE, "total",
                f"金额异常（{ANOMALY_NEGATIVE_NON_RED}）：负金额但非红字发票",
                f"价税合计 {inv.total}（票种：{inv.invoice_type or '未知'}）",
                "负数金额通常仅红冲/退款出现，核实票面与业务",
                evidence_chain=[
                    _link("total", f"{inv.total:.2f}", f"{inv.total:.2f}", note="票面价税合计（负数）"),
                    _link("invoice_type", inv.invoice_type or "未知", note="票种（非红字）"),
                    _link("is_red_letter", str(inv.is_red_letter), note="红冲标志=False"),
                ],
            ))
            continue
        if inv.is_differential:
            # C1：差额票专项校验移交 R9（存在性/合理性/一致性），R8 不再输出占位声明
            continue
        if abs(inv.total) > cfg.max_plausible_total:
            out.append(_finding(
                "R8", inv, _SEV_LOW, _CONF_MAYBE, "total",
                f"金额异常（{ANOMALY_ABSURD_TOTAL}）：单票价税合计超出合理阈值",
                f"价税合计 {inv.total} 元 > 阈值 {cfg.max_plausible_total} 元（占位阈值，可配置）",
                "大额票需人工复核业务合理性；阈值待真实数据校准",
                evidence_chain=[
                    _link("total", f"{inv.total:.2f}", f"{inv.total:.2f}", note="票面价税合计"),
                    _link("config.max_plausible_total", f"{cfg.max_plausible_total}",
                          f"{cfg.max_plausible_total}", note="占位阈值（待真实数据校准）"),
                ],
            ))
    return out


def _parse_rate(raw: str) -> Decimal | None:
    """税率原文 → Decimal 比例（"13%"→0.13、"0.06"→0.06、"6"→0.06）；
    不可解析（"*"/"免税"/"不征税"/空）→ None（C1 R10 行级税率自洽用）。"""
    s = (raw or "").strip()
    if not s or s in ("*", "免税", "不征税"):
        return None
    try:
        if s.endswith("%"):
            return Decimal(s[:-1]) / Decimal("100")
        v = Decimal(s)
        if v > 1:  # "6" → 0.06
            v = v / Decimal("100")
        return v
    except InvalidOperation:
        return None


# ---------- C1 规范驱动规则（2026-09-23：差额专项/行级勾稽/红冲关联占位） ----------

def r9_differential(invoices: list[NormalizedInvoice], cfg: RulesConfig) -> list[Finding]:
    """差额征税专项（C1，取代 R8 的占位声明）。

    触发：is_differential（备注含"差额征税"或存在 KCE 扣除额）。
    检查：① 差额票必须携带扣除额（KCE 缺失 → 中/疑似）
         ② 扣除额 ≤ 价税合计（KCE>total → 高/疑似，数据错误）
         ③ 备注"差额征税：XX。"金额与 KCE 一致（可解析时比对；不一致 → 中/疑似）
    口径登记：差额票计税基础 = 销售额 - 扣除额（合成依据 EI386 + 金蝶差额开票口径）；
    完整"扣除额-税额联动核验"待真实差额票核验（真实字段形态未核验，诚实声明）。
    """
    out: list[Finding] = []
    for inv in invoices:
        if not inv.is_differential:
            continue
        remark = inv.raw_fields.get("Remark") or inv.raw_fields.get("备注") or ""
        kce = inv.differential_deduction
        if kce is None:
            out.append(_finding(
                "R9", inv, _SEV_MED, _CONF_MAYBE, "differential_deduction",
                "差额征税票缺少扣除额（KCE）字段，无法核验差额口径",
                f"备注：{remark or '(空)'}；票种：{inv.invoice_type or '未知'}",
                "差额征税票应携带扣除额（EI386/KCE）；真实票字段布局待核验",
                evidence_chain=[
                    _link("Remark", remark or "(空)", note="备注原文"),
                    _link("differential_deduction", "(空)", note="KCE 扣除额缺失"),
                ],
            ))
            continue
        if kce > inv.total:
            out.append(_finding(
                "R9", inv, _SEV_HIGH, _CONF_MAYBE, "differential_deduction",
                "差额征税票扣除额大于价税合计（数据错误）",
                f"扣除额 {kce} > 价税合计 {inv.total}",
                "扣除额不能大于票面合计，核对票面与扣除额字段",
                evidence_chain=[
                    _link("differential_deduction", f"{kce:.2f}", f"{kce:.2f}", note="扣除额 KCE"),
                    _link("total", f"{inv.total:.2f}", f"{inv.total:.2f}", note="票面价税合计"),
                ],
            ))
            continue
        if remark:
            m = re.search(r"差额征税[:：]\s*([\d.]+)", remark)
            if m:
                try:
                    remark_kce = Decimal(m.group(1))
                except InvalidOperation:
                    remark_kce = None
                if remark_kce is not None and remark_kce != kce:
                    out.append(_finding(
                        "R9", inv, _SEV_MED, _CONF_MAYBE, "Remark",
                        "差额征税备注金额与扣除额（KCE）不一致",
                        f"备注：{remark}；扣除额：{kce}",
                        "备注申报口径与扣除额字段应一致；真实票备注格式多样，人工复核",
                        evidence_chain=[
                            _link("Remark", remark, note="备注原文"),
                            _link("differential_deduction", f"{kce:.2f}", f"{kce:.2f}",
                                  note="KCE 扣除额（备注解析值 {remark_kce}）"),
                        ],
                    ))
    return out


def r10_item_reconciliation(invoices: list[NormalizedInvoice],
                            cfg: RulesConfig) -> list[Finding]:
    """明细行勾稽（C1，行级一致性）。

    检查（items 非空时）：
    ① Σ行金额 ≠ 票面金额（容差 ±0.01）→ 高/疑似（金额口径不一致）
    ② Σ行税额 ≠ 票面税额 → 高/疑似
    ③ 行级税率自洽：amount×rate ≈ tax_amount（税率原文可解析为数值时；"*"/免税跳过）
    items 空（无行容器方言/单行汇总票）→ 不产出（三态=未执行：数据不足）。
    """
    out: list[Finding] = []
    for inv in invoices:
        if not inv.items:
            continue
        sum_amount = sum((i.amount for i in inv.items), Decimal("0"))
        sum_tax = sum((i.tax_amount for i in inv.items), Decimal("0"))
        if abs(sum_amount - inv.amount) > Decimal("0.01"):
            out.append(_finding(
                "R10", inv, _SEV_HIGH, _CONF_MAYBE, "items",
                "明细行金额合计与票面金额不符",
                f"Σ行 {sum_amount} ≠ 票面金额 {inv.amount}（{len(inv.items)} 行）",
                "行级与票面金额口径不一致，核对解析与票面",
                evidence_chain=[
                    _link("items", f"{len(inv.items)} 行", f"Σ{sum_amount:.2f}",
                          note="Σ行金额合计"),
                    _link("amount", f"{inv.amount:.2f}", f"{inv.amount:.2f}", note="票面金额"),
                ],
            ))
        if abs(sum_tax - inv.tax) > Decimal("0.01"):
            out.append(_finding(
                "R10", inv, _SEV_HIGH, _CONF_MAYBE, "items",
                "明细行税额合计与票面税额不符",
                f"Σ行 {sum_tax} ≠ 票面税额 {inv.tax}（{len(inv.items)} 行）",
                "行级与票面税额口径不一致，核对解析与票面",
                evidence_chain=[
                    _link("items", f"{len(inv.items)} 行", f"Σ{sum_tax:.2f}",
                          note="Σ行税额合计"),
                    _link("tax", f"{inv.tax:.2f}", f"{inv.tax:.2f}", note="票面税额"),
                ],
            ))
        for idx, it in enumerate(inv.items, start=1):
            rate = _parse_rate(it.tax_rate)
            if rate is None:
                continue  # 税率不可解析（"*"/"免税"/"不征税"）→ 跳过
            expected = (it.amount * rate).quantize(Decimal("0.01"))
            if abs(expected - it.tax_amount) > Decimal("0.01"):
                out.append(_finding(
                    "R10", inv, _SEV_MED, _CONF_MAYBE, "items",
                    f"明细行第 {idx} 行税额与税率不符",
                    f"行 {idx}：{it.name or '(未命名)'} 金额 {it.amount} × 税率 {it.tax_rate} = "
                    f"{expected} ≠ 税额 {it.tax_amount}",
                    "行级税额与票面税率口径不一致，核对票面",
                    evidence_chain=[
                        _link("items", f"行{idx}.{it.name or '(未命名)'}",
                              f"{it.amount}×{it.tax_rate}", row=idx,
                              note=f"期望税额 {expected}"),
                        _link("items", f"{it.tax_amount:.2f}", f"{it.tax_amount:.2f}", row=idx,
                              note="行级实际税额"),
                    ],
                ))
    return out


def r11_red_letter_link(invoices: list[NormalizedInvoice],
                        cfg: RulesConfig) -> list[Finding]:
    """红冲关联（C1 占位）。

    红字票必须携带被冲蓝字发票号码（EI390）；缺失 → 低/疑似（无法关联校验）。
    完整红冲关联（蓝票存在性/金额匹配/跨期状态）需历史发票库——当前单票上传仅票面检查（诚实声明）。
    真实红冲 EInvoice 的被冲蓝票号字段布局待真实票核验（中文方言已确认字段）。
    """
    out: list[Finding] = []
    for inv in invoices:
        if not inv.is_red_letter:
            continue
        if not inv.red_letter_blue_no:
            out.append(_finding(
                "R11", inv, _SEV_LOW, _CONF_MAYBE, "red_letter_blue_no",
                "红字发票未携带被冲蓝字发票号码，无法做红冲关联校验",
                f"票种：{inv.invoice_type or '未知'}；票号：{inv.invoice_no or '(空)'}",
                "红冲票应能关联被冲蓝票（EI390 被红冲蓝字电子发票号码）；真实红冲 EInvoice 字段布局待核验；"
                "完整红冲关联需历史发票库（当前仅票面检查）",
                evidence_chain=[
                    _link("is_red_letter", str(inv.is_red_letter), note="红冲标志=True"),
                    _link("red_letter_blue_no", "(空)", note="被红冲蓝字发票号码缺失"),
                ],
            ))
    return out


def _state_for(rule_id: str, rs: list[Finding], cfg: RulesConfig,
               invoices: list[NormalizedInvoice]) -> str:
    """规则三态（里程碑评审）：命中 / 未命中 / 未执行（数据不足或未配置）。

    原则：因缺数据/未配置而无法判定的规则必须显式标注，禁止用沉默暗示合规。
    """
    real = [f for f in rs if not any(k in f.message for k in ("数据不完整", "未执行", "未配置"))]
    if real:
        return "命中"
    if rule_id == "R2" and (not cfg.company_name or not cfg.company_taxid):
        return "未执行（未配置企业主体）"
    if rule_id == "R4" and not any(i.category for i in invoices):
        return "未执行（缺少报销类别）"
    if rule_id == "R10" and not any(i.items for i in invoices):
        return "未执行（无明细行数据）"
    if rule_id == "R9" and not any(i.is_differential for i in invoices):
        return "未执行（本批次无差额征税票）"
    if rule_id == "R11" and not any(i.is_red_letter for i in invoices):
        return "未执行（本批次无红字票）"
    if rs:  # 有"数据不完整"类提示但无真正命中
        return "未执行（本次数据不足）"
    return "未命中"


def run_rules(invoices: list[NormalizedInvoice], config: RulesConfig | None = None) -> list[Finding]:
    """整批执行全部规则（兼容入口，返回 findings；如需三态用 run_rules_with_states）。"""
    return run_rules_with_states(invoices, config)[0]


def run_rules_with_states(invoices: list[NormalizedInvoice],
                          config: RulesConfig | None = None) -> tuple[list[Finding], dict[str, str]]:
    """整批执行全部规则，返回 (findings, rule_states)。

    隔离：逐规则 try/except，单条规则异常降级为批次级 Finding，不拖垮整批。
    红线保护：关键字段缺失时只产出低危/疑似提示，不产出"确定"级对外结论。
    """
    cfg = config or RulesConfig()
    findings: list[Finding] = []
    states: dict[str, str] = {}
    checks = [
        ("R1", lambda: r1_duplicate(invoices)),
        ("R2", lambda: r2_header(invoices, cfg)),
        ("R3", lambda: r3_serial(invoices, cfg)),
        ("R4", lambda: r4_overlimit(invoices, cfg)),
        ("R6", lambda: r6_concentrated(invoices, cfg)),
        ("R7", lambda: r7_date_anomaly(invoices, cfg)),
        ("R8", lambda: r8_amount_anomaly(invoices, cfg)),
        ("R9", lambda: r9_differential(invoices, cfg)),
        ("R10", lambda: r10_item_reconciliation(invoices, cfg)),
        ("R11", lambda: r11_red_letter_link(invoices, cfg)),
    ]
    for rule_id, fn in checks:
        try:
            rs = fn()
            findings.extend(rs)
            states[rule_id] = _state_for(rule_id, rs, cfg, invoices)
        except Exception as e:  # 规则隔离：异常不拖垮整批
            findings.append(Finding(
                rule_id=rule_id, severity=_SEV_LOW, confidence=_CONF_MAYBE,
                invoice_no="-", field="system",
                message=f"规则 {rule_id} 执行异常，该规则结果未纳入本次报告",
                evidence=f"{type(e).__name__}: {e}",
                suggestion="联系开发者排查；本报告其余规则不受影响",
                ruleset_version=RULESET_VERSION,
            ))
            states[rule_id] = "未执行（规则执行异常）"
    # 稳定排序：严重度 高>中>低 → 置信度 确定>疑似 → 规则号 → 票号
    sev_order = {_SEV_HIGH: 0, _SEV_MED: 1, _SEV_LOW: 2}
    findings.sort(key=lambda f: (sev_order.get(f.severity, 3), f.confidence != _CONF_SURE,
                                f.rule_id, f.invoice_no))
    return findings, states
