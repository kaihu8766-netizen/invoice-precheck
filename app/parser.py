"""数电票 XML 解析（P1 技术方案第 1 节 · DeepSeek 评审采纳 + 2026-09-22 代码审查修订）。

设计要点：
- localname 匹配（tag.rsplit('}',1)[-1]）兼容命名空间/厂商差异；
- 金额/税额/合计类字段取"最后出现"（合计节点通常在文档尾部，避免采到明细行金额）；
- 金额全链路 Decimal(str)，禁 float；税额 '*' 或空 → 0 + 告警；
- 勾稽 amount + tax == total，容差 ±0.01，不符记"勾稽异常"不抛错；
- iterparse 读 BytesIO，规避编码声明不符；字段缺失进 parse_warnings 不中断；
- detect_type 前置：剥 BOM、支持无 prolog 的合法 XML；非 XML 输入明确报"不支持类型"；
- raw_fields 白名单收集 + 税号脱敏（防 PII 外泄）；
- 文件大小上限（防超大文件/资源耗尽）。
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.etree import ElementTree

from .models import NormalizedInvoice

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 中文标签 → 模型字段（别名映射；金额/税额/合计只用"合计类"明确标签，防歧义）
_ALIAS = {
    "invoice_no": ["发票号码", "发票代码及号码"],
    "issue_date": ["开票日期", "发票开具日期"],
    "buyer_name": ["购买方名称", "购方名称"],
    "buyer_taxid": ["购买方纳税人识别号", "购方税号", "购买方统一社会信用代码"],
    "seller_name": ["销售方名称", "销方名称"],
    "seller_taxid": ["销售方纳税人识别号", "销方税号", "销售方统一社会信用代码"],
    "amount": ["合计金额", "TotalAmWithoutTax"],
    "tax": ["合计税额", "TotalTaxAm"],
    "total": ["价税合计(小写)", "价税合计", "TotalTaxIncludedAm", "合计"],
    "invoice_type": ["发票类型", "票种"],
}

# 取"最后出现"的字段（合计节点通常在文档尾部，明细行在前；避免采到首行明细金额）
_LAST_WINS = {"合计金额", "合计税额", "价税合计", "价税合计(小写)", "合计",
              "TotalAmWithoutTax", "TotalTaxAm", "TotalTaxIncludedAm"}

# raw_fields 白名单（仅保留规则用得到的键，防整张票面外泄）
_RAW_WHITELIST = {
    "发票号码", "开票日期", "发票类型",
    "购买方名称", "购买方纳税人识别号",
    "销售方名称", "销售方纳税人识别号",
    "合计金额", "合计税额", "价税合计",
}


def _localname(tag: str) -> str:
    """取 localname，兼容 '{uri}tag' 形式。"""
    return tag.rsplit("}", 1)[-1]


def _find_by_alias(fields: dict, key: str) -> str | None:
    for alias in _ALIAS.get(key, []):
        if alias in fields:
            return fields[alias]
    return None


def _mask(value: str) -> str:
    """税号/账号类脱敏：保留前 2 后 2，中间打码。"""
    if len(value) >= 8:
        return value[:2] + "****" + value[-2:]
    return value


def _to_decimal(raw: str | None, warnings: list[str], field_name: str) -> Decimal:
    if raw is None or raw.strip() in ("", "*"):
        warnings.append(f"{field_name} 为空或 '*'，按 0 处理")
        return Decimal("0")
    s = str(raw).strip()
    s = s.replace(",", "").replace("，", "")
    for sym in ("¥", "￥", "元", "rmb", "RMB"):
        s = s.replace(sym, "")
    s = s.strip()
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        warnings.append(f"{field_name} 无法解析为金额：{raw!r}，按 0 处理")
        return Decimal("0")


def _to_iso_date(raw: str | None, warnings: list[str]) -> str:
    if raw is None or not raw.strip():
        warnings.append("开票日期缺失")
        return ""
    s = raw.strip()
    m = re.fullmatch(r"(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    warnings.append(f"开票日期格式未知：{raw!r}")
    return s


def detect_type(data: bytes) -> str:
    """magic bytes 类型探测（P1 一级能力：不支持的输入明确提示）。"""
    b = data[:1024]
    if b.startswith(b"\xef\xbb\xbf"):  # 剥 UTF-8 BOM（Windows/Excel 导出常见）
        b = b[3:]
    b = b.lstrip()
    if b.startswith(b"<?xml") or b.startswith(b"<"):
        return "xml"  # 含 prolog 或无 prolog 直接以根节点开头
    if data[:4] == b"PK\x03\x04":
        return "ofd"  # OFD/zip 容器
    if data[:4] == b"%PDF":
        return "pdf"
    return "unknown"


def _collect_fields(data: bytes, warnings: list[str]) -> dict[str, str]:
    """扁平化收集字段：普通字段取首个，金额/合计类取最后出现。"""
    fields: dict[str, str] = {}
    try:
        context = ElementTree.iterparse(BytesIO(data), events=("end",))
        for _event, elem in context:
            name = _localname(elem.tag)
            if elem.text and elem.text.strip():
                text = elem.text.strip()
                if name in _LAST_WINS:
                    fields[name] = text  # 合计类覆盖（文档尾部才是合计）
                else:
                    fields.setdefault(name, text)
            elem.clear()
    except ElementTree.ParseError as e:
        raise ValueError(f"XML 结构损坏：{e}") from e
    return fields


def parse_xml(data: bytes) -> NormalizedInvoice:
    """解析数电票 XML → NormalizedInvoice。

    约定：调用方须逐文件 try/except（文件级失败不上报批次）；
    非 XML / 超大文件在此抛 ValueError，可分类处理。
    """
    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"文件过大（>{MAX_FILE_SIZE // 1024 // 1024}MB），已拒绝解析")
    dtype = detect_type(data)
    if dtype != "xml":
        raise ValueError(f"不支持的输入类型：{dtype}（仅支持数电票 XML，请转 XML 后上传）")

    warnings: list[str] = []
    fields = _collect_fields(data, warnings)

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

    # raw_fields 白名单 + 税号脱敏
    raw_fields = {
        k: (_mask(v) if "税号" in k or "识别号" in k else v)
        for k, v in fields.items() if k in _RAW_WHITELIST
    }

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
        seller_taxid=pick("seller_taxid") or "",
        source_hash=hashlib.sha256(data).hexdigest()[:32],
        parse_warnings=warnings,
        raw_fields=raw_fields,
    )
