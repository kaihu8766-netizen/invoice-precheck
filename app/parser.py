"""数电票 XML 解析（P1 技术方案第 1 节 · DeepSeek 评审采纳）。

设计要点：
- localname 匹配（tag.rsplit('}',1)[-1]）兼容命名空间/厂商差异；
- 扁平化收集字段 + 别名映射表，专/普票结构差异在此吸收；
- 金额全链路 Decimal(str)，禁 float；税额 '*' 或空 → 0 + 告警；
- 勾稽 amount + tax == total，容差 ±0.01，不符记"勾稽异常"不抛错；
- iterparse 读 bytes，规避编码声明不符；字段缺失进 parse_warnings 不中断。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.etree import ElementTree

from .models import NormalizedInvoice

# 中文标签 → 模型字段（别名映射，兼容不同开票平台）
_ALIAS = {
    "invoice_no": ["发票号码", "发票代码及号码"],
    "issue_date": ["开票日期", "发票开具日期"],
    "buyer_name": ["购买方名称", "购方名称"],
    "buyer_taxid": ["购买方纳税人识别号", "购方税号", "购买方统一社会信用代码"],
    "seller_name": ["销售方名称", "销方名称"],
    "seller_taxid": ["销售方纳税人识别号", "销方税号", "销售方统一社会信用代码"],
    "amount": ["合计金额", "金额", "不含税金额"],
    "tax": ["合计税额", "税额"],
    "total": ["价税合计", "价税合计(小写)", "小写合计", "合计"],
    "invoice_type": ["发票类型", "票种"],
}


def _localname(tag: str) -> str:
    """取 localname，兼容 '{uri}tag' 形式。"""
    return tag.rsplit("}", 1)[-1]


def _find_by_alias(fields: dict, key: str) -> str | None:
    for alias in _ALIAS.get(key, []):
        if alias in fields:
            return fields[alias]
    return None


def _to_decimal(raw: str | None, warnings: list[str], field_name: str) -> Decimal:
    if raw is None or raw.strip() in ("", "*"):
        warnings.append(f"{field_name} 为空或 '*'，按 0 处理")
        return Decimal("0")
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        warnings.append(f"{field_name} 无法解析为金额：{raw!r}，按 0 处理")
        return Decimal("0")


def _to_iso_date(raw: str | None, warnings: list[str]) -> str:
    if raw is None or not raw.strip():
        warnings.append("开票日期缺失")
        return ""
    s = raw.strip()
    # 常见格式：yyyyMMdd / yyyy-MM-dd / yyyy/MM/dd
    s = s.replace("/", "").replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    warnings.append(f"开票日期格式未知：{raw!r}")
    return s


def detect_type(data: bytes) -> str:
    """magic bytes 类型探测（P1 一级能力：不支持的输入明确提示）。"""
    if data[:5].lstrip().lower().startswith(b"<?xml"):
        return "xml"
    if data[:4] == b"PK\x03\x04":
        return "ofd"  # OFD/zip 容器
    if data[:4] == b"%PDF":
        return "pdf"
    return "unknown"


def parse_xml(data: bytes) -> NormalizedInvoice:
    """解析数电票 XML → NormalizedInvoice。"""
    warnings: list[str] = []
    fields: dict[str, str] = {}

    # iterparse 读 BytesIO，规避编码声明不符；遇解析错误降级为告警
    try:
        context = ElementTree.iterparse(BytesIO(data), events=("end",))
        for _event, elem in context:
            name = _localname(elem.tag)
            if elem.text and elem.text.strip():
                # 同名标签（明细行等）取第一个出现的顶层值
                fields.setdefault(name, elem.text.strip())
            elem.clear()
    except ElementTree.ParseError as e:
        raise ValueError(f"XML 解析失败：{e}") from e

    def pick(key: str) -> str | None:
        return _find_by_alias(fields, key)

    invoice_no = pick("invoice_no") or ""
    issue_date = _to_iso_date(pick("issue_date"), warnings)
    amount = _to_decimal(pick("amount"), warnings, "金额")
    tax = _to_decimal(pick("tax"), warnings, "税额")
    total = _to_decimal(pick("total"), warnings, "价税合计")

    # 勾稽校验（容差 ±0.01；不平时记告警，不中断）
    if total != 0 and abs((amount + tax) - total) > Decimal("0.01"):
        warnings.append(
            f"勾稽异常：金额+税额({amount}+{tax}) ≠ 价税合计({total})，差额 {amount + tax - total}"
        )

    if not invoice_no:
        warnings.append("发票号码缺失")

    return NormalizedInvoice(
        invoice_no=invoice_no,
        invoice_type=pick("invoice_type") or "未知",
        issue_date=issue_date,
        amount=amount,
        tax=tax,
        total=total,
        buyer_name=pick("buyer_name") or "",
        buyer_taxid=pick("buyer_taxid") or "",
        seller_name=pick("seller_name") or "",
        source_hash=hashlib.sha256(data).hexdigest()[:16],
        parse_warnings=warnings,
        raw_fields=dict(fields),
    )
