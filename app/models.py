"""数据契约（P1 技术方案第 2 节 · DeepSeek 评审采纳）。

规则引擎只依赖 NormalizedInvoice，不触碰 XML 树；解析层产出后立即映射为统一模型。
金额全链路 Decimal（禁 float）；字段缺失进 parse_warnings，不中断整批。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Optional

Severity = Literal["高", "中", "低"]
Confidence = Literal["确定", "疑似"]


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


@dataclass
class Finding:
    """单条风险发现（规则引擎输出）。"""

    rule_id: str                       # R1/R2/R3/R4/R6/R7
    severity: Severity                 # 严重度：看财务后果
    confidence: Confidence             # 置信度：看证据强度（双标签分离，防单色阶误导）
    invoice_no: str
    field: str                         # 命中字段（单字段，一条 Finding 只报一个字段）
    message: str                       # 人类可读描述
    evidence: str                      # 原始证据（票号/金额/供应商等；敏感字段已脱敏）
    suggestion: str = ""               # 建议动作
    ruleset_version: str = ""          # 规则版本（报告溯源用，审计要求）
