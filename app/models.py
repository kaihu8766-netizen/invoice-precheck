"""数据契约（P1 技术方案第 2 节 · DeepSeek 评审采纳；G0 行级中间表示升级 2026-09-23）。

规则引擎只依赖 NormalizedInvoice，不触碰 XML 树；解析层产出后立即映射为统一模型。
金额全链路 Decimal（禁 float）；字段缺失进 parse_warnings，不中断整批。
G0：增加行级明细 ItemDetail（多税率勾稽/差额 KCE/红冲关联的前置）+ 票面语义字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Optional

Severity = Literal["高", "中", "低"]
Confidence = Literal["确定", "疑似"]


@dataclass
class ItemDetail:
    """发票明细行（G0 行级中间表示；多税率勾稽/行级税率一致性校验的前置）。"""

    name: str = ""                          # 商品/服务名称
    amount: Decimal = Decimal("0")          # 金额（不含税）
    tax_rate: str = ""                      # 税率原文（"13%"/"6%"/"9%"/"0%"/"免税"/"*"等，保留原样）
    tax_amount: Decimal = Decimal("0")      # 税额
    total_incl: Decimal = Decimal("0")      # 含税金额（行级价税合计；可缺省=0）


@dataclass
class NormalizedInvoice:
    """解析层输出的统一发票模型。"""

    # 基础标识
    invoice_no: str                    # 发票号码（数电票 20 位）
    invoice_type: str                  # 票种：专票 / 普票 / 电子票等
    issue_date: str                    # 开票日期（ISO yyyy-MM-dd）

    # 金额（Decimal，禁止 float；勾稽：amount + tax == total，容差 ±0.01）
    amount: Decimal                    # 金额（不含税）
    tax: Decimal                       # 税额
    total: Decimal                     # 价税合计

    # 购销方
    buyer_name: str
    buyer_taxid: str
    seller_name: str                   # 销售方名称（规则 R3 连号按供应商分组）
    seller_taxid: str = ""             # 销售方税号（分组键 (名称,税号)，防同名不同税号合并）

    # 报销侧（可选：CSV 批量导入时携带；单 XML 上传时为空）
    category: Optional[str] = None     # 类别：差旅/招待/办公/交通/其他
    reimburse_date: Optional[str] = None  # 报销日期 ISO

    # 批次与溯源
    batch_id: str = ""                 # 批次标识（R1/R3/R6 跨票规则按 batch 分组）
    source_hash: str = ""              # 来源文件哈希（去重用）
    parse_warnings: list[str] = field(default_factory=list)  # 解析告警（字段缺失等）

    # 原始证据（报告展示用）
    raw_fields: dict = field(default_factory=dict)

    # 票面语义标志（P0-8 金额异常类型化基础；parser 提取，规则 R8 消费）
    is_red_letter: bool = False    # 红字发票（票种含"红"或备注含红冲/红字）
    is_differential: bool = False  # 差额征税票（备注含"差额征税"或存在 KCE 扣除额字段）

    # G0 行级中间表示 + 票面语义字段（2026-09-23）
    items: list[ItemDetail] = field(default_factory=list)  # 明细行（EInvoice 英文结构行级；中文/拼音方言待真实票核验）
    differential_deduction: Optional[Decimal] = None       # 差额征税扣除额 KCE（Decimal；无则 None）
    red_letter_blue_no: str = ""      # 被红冲蓝字发票号码（红冲关联占位；中文方言字段，EInvoice 布局待真实票核验）

    # 人工复核标志（DeepSeek OCR 评审 D 补丁：低置信度/勾稽不一致 → 需人工复核，不自动通过）
    review_needed: bool = False       # 需人工复核（OCR 低置信度 / 勾稽不一致等防错场景）
    review_reason: str = ""           # 复核原因（展示给财务人员）


@dataclass
class EvidenceLink:
    """证据链单条定位（G1：合规预审可审计——每条命中能定位到字段/原文/行号/计算）。

    - field：命中的字段标识（票面字段名/明细行字段/系统字段）
    - raw：原始值（已脱敏：税号类打码）
    - value：规范化值（Decimal 的字符串/日期 ISO/数值）
    - row：明细行号（行级证据时 ≥1；票面/系统证据 = 0）
    - note：计算过程或说明（如"金额+税额≠价税合计，差 -0.01"）
    """

    field: str = ""
    raw: str = ""
    value: str = ""
    row: int = 0
    note: str = ""


@dataclass
class Finding:
    """单条风险发现（规则引擎输出）。"""

    rule_id: str                       # R1/R2/R3/R4/R6/R7/R8
    severity: Severity                 # 严重度：看财务后果
    confidence: Confidence             # 置信度：看证据强度（双标签分离，防单色阶误导）
    invoice_no: str
    field: str                         # 命中字段（单字段，一条 Finding 只报一个字段）
    message: str                       # 人类可读描述
    evidence: str                      # 原始证据（票号/金额/供应商等；敏感字段已脱敏）
    suggestion: str = ""               # 建议动作
    ruleset_version: str = ""          # 规则包版本（报告溯源用，审计要求）
    evidence_chain: list[EvidenceLink] = field(default_factory=list)  # G1 结构化证据链（可审计定位）
