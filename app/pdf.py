"""数电票 PDF 解析（Week0 · DeepSeek 评审：PDF 主路径，当前只做文本型 PDF）。

设计要点（对齐 parser.py 契约，输出 NormalizedInvoice）：
- 二维码优先：数电票 PDF 版式通常带"数电票信息二维码"（结构化 JSON），
  zxingcpp 解码 + json 解析，字段最稳；解码失败/无码回落文本抽取；
- 文本抽取：PyMuPDF get_text → 宽容正则（中文标签/英文 key/常见方言），
  金额全链路 Decimal，防 float；
- 扫描型分流：页面文本长度低于阈值 → 标记 need_ocr（友好降级，OCR 延 Week2，
  架构预留 OcrProvider 接口）；
- 安全：文件大小上限、页数上限、解码结果白名单取字段（不信任任意 JSON 结构）。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

import fitz  # PyMuPDF

from .models import NormalizedInvoice

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB（与 XML 对齐）
MAX_PAGES = 20                    # 页数上限（防超大 PDF）
_TEXT_MIN_CHARS = 80              # 文本型/扫描型阈值：低于则视为扫描件（需 OCR）
_QR_SCALE = 3                     # 渲染缩放（二维码解码清晰度）

# 字段别名（中文/英文/常见方言宽容匹配；取"最后出现"与 XML 策略一致）
_FIELD_ALIAS = {
    "invoice_no": ["发票号码", "发票代码和号码", "InvoiceNo", "Invoice Number", "NO"],
    "issue_date": ["开票日期", "IssueDate", "Date", "开票时间"],
    "amount":     ["金额", "不含税金额", "Amount", "金额合计"],
    "tax":        ["税额", "TaxAmount", "Tax"],
    "total":      ["价税合计", "合计", "Total", "价税合计(小写)", "小写"],
    "buyer_name": ["购买方名称", "购方名称", "BuyerName", "购买方"],
    "buyer_taxid":["购买方税号", "购方税号", "购买方纳税人识别号", "BuyerTaxID", "BuyerTaxNo"],
    "seller_name":["销售方名称", "销方名称", "SellerName", "销售方"],
    "seller_taxid":["销售方税号", "销方税号", "销售方纳税人识别号", "SellerTaxID", "SellerTaxNo"],
}
_QR_ALIAS = {
    "invoice_no": ["发票号码", "InvoiceNo", "invoiceNo", "invoice_no"],
    "issue_date": ["开票日期", "IssueDate", "issueDate", "issue_date", "开票时间"],
    "amount":     ["金额", "Amount", "amount", "不含税金额"],
    "tax":        ["税额", "TaxAmount", "taxAmount", "tax"],
    "total":      ["价税合计", "Total", "total", "价税合计(小写)"],
    "buyer_name": ["购买方名称", "BuyerName", "buyerName", "购方名称"],
    "buyer_taxid":["购买方税号", "BuyerTaxID", "buyerTaxId", "购方税号", "购买方纳税人识别号"],
    "seller_name":["销售方名称", "SellerName", "sellerName", "销方名称"],
    "seller_taxid":["销售方税号", "SellerTaxID", "sellerTaxId", "销方税号", "销售方纳税人识别号"],
}
_AMOUNT_RE = re.compile(r"[-+]?\d[\d,]*\.\d{2}")
_DATE_RE = re.compile(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?")


def _clean_money(s: str) -> Decimal:
    s = s.replace(",", "").replace("¥", "").replace("￥", "").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _first_value(page_text: str, key: str, aliases: list[str]) -> Optional[str]:
    """按别名表在文本中找字段。匹配'别名 + 冒号 + 值'（真实数电票 PDF 标签均带冒号），
    避免前缀别名误匹配（如'购买方'撞上'购买方税号'）；长别名优先；取最后出现（与 XML 一致）。"""
    found = None
    for a in sorted(aliases, key=len, reverse=True):
        for m in re.finditer(rf"{re.escape(a)}\s*[:：]\s*([^\n]{{0,40}})", page_text):
            v = m.group(1).strip()
            if not v:
                continue
            found = v
    if found is None:
        return None
    # 金额字段：从候选串中抽首个金额
    if key in ("amount", "tax", "total"):
        m = _AMOUNT_RE.search(found)
        return m.group(0) if m else None
    return found.strip()


def _extract_by_qrcode(pix: "fitz.Pixmap") -> Optional[dict]:
    try:
        import zxingcpp
    except Exception:
        return None
    try:
        results = zxingcpp.read_barcodes(pix.samples, pix.width, pix.height, pixel_format=zxingcpp.ImageFormat.LumA)
    except Exception:
        return None
    for r in results:
        if r.format == zxingcpp.BarcodeFormat.QRCode:
            txt = r.text
            try:
                data = json.loads(txt)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data[0]
    return None


def _normalize_from_dict(data: dict, source: str, source_hash: str) -> NormalizedInvoice:
    def pick(aliases):
        for a in aliases:
            if a in data:
                v = data[a]
                if isinstance(v, dict):
                    # 部分二维码嵌套 {Invoice: {...}} 结构，逐层找标量
                    for k, vv in v.items():
                        if isinstance(vv, (str, int, float)) and not isinstance(vv, (dict, list)):
                            return str(vv)
                    continue
                if isinstance(v, (str, int, float)):
                    return str(v)
        return ""

    inv = NormalizedInvoice(
        invoice_no=pick(_QR_ALIAS["invoice_no"]),
        issue_date=pick(_QR_ALIAS["issue_date"]),
        amount=_clean_money(pick(_QR_ALIAS["amount"]) or "0"),
        tax=_clean_money(pick(_QR_ALIAS["tax"]) or "0"),
        total=_clean_money(pick(_QR_ALIAS["total"]) or "0"),
        buyer_name=pick(_QR_ALIAS["buyer_name"]),
        buyer_taxid=pick(_QR_ALIAS["buyer_taxid"]),
        seller_name=pick(_QR_ALIAS["seller_name"]),
        seller_taxid=pick(_QR_ALIAS["seller_taxid"]),
        invoice_type="电子发票",
        source_hash=source_hash,
        raw_fields={k: v for k, v in data.items() if isinstance(v, (str, int, float))},
    )
    if not inv.invoice_no:
        inv.parse_warnings.append(f"二维码 {source} 未识别到发票号码")
    if inv.amount + inv.tax != inv.total:
        inv.parse_warnings.append("二维码价税勾稽不符")
    return inv


def parse_pdf(data: bytes, source_hash: str = "") -> NormalizedInvoice:
    """解析 PDF（文本型），输出 NormalizedInvoice；扫描件抛 PDFNeedsOCR。"""
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("文件超过 10MB 上限")
    import hashlib
    if not source_hash:
        source_hash = hashlib.sha256(data).hexdigest()[:16]

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF 页数 {doc.page_count} 超过上限 {MAX_PAGES}")
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(_QR_SCALE, _QR_SCALE), colorspace=fitz.csGRAY)

        # 1) 二维码优先
        qr = _extract_by_qrcode(pix)
        if qr:
            return _normalize_from_dict(qr, "pdf-qrcode", source_hash)

        # 2) 文本型兜底
        text = page.get_text("text")
        if len(text.strip()) < _TEXT_MIN_CHARS:
            raise PDFNeedsOCR(f"PDF 页面文本量不足（{len(text.strip())} 字符），疑似扫描件，需 OCR")

        fields = {}
        for key, aliases in _FIELD_ALIAS.items():
            fields[key] = _first_value(text, key, aliases) or ""
        inv = NormalizedInvoice(
            invoice_no=fields["invoice_no"],
            issue_date=fields["issue_date"],
            amount=_clean_money(fields["amount"]) if fields["amount"] else Decimal("0"),
            tax=_clean_money(fields["tax"]) if fields["tax"] else Decimal("0"),
            total=_clean_money(fields["total"]) if fields["total"] else Decimal("0"),
            buyer_name=fields["buyer_name"],
            buyer_taxid=fields["buyer_taxid"],
            seller_name=fields["seller_name"],
            seller_taxid=fields["seller_taxid"],
            invoice_type="电子发票",
            source_hash=source_hash,
            raw_fields={"pdf_text_head": text[:500]},
        )
        missing = [k for k, v in fields.items() if not v]
        if missing:
            inv.parse_warnings.append(f"文本抽取缺失字段: {','.join(missing)}")
        if inv.invoice_no and inv.amount + inv.tax != inv.total and inv.total != 0:
            inv.parse_warnings.append("文本抽取价税勾稽不符（需人工复核）")
        return inv
    finally:
        doc.close()


class PDFNeedsOCR(Exception):
    """扫描型 PDF：当前阶段不支持，需 OCR（Week2 启用 OcrProvider）。"""
