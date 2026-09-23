"""数电票 PDF 解析（Week0 · DeepSeek 评审：PDF 主路径；真实票验证驱动重构 2026-09-23）。

设计要点（真实数电票 PDF 版式观察修正）：
- 二维码是"税务核验短码"（逗号分隔：版本,票种,发票号,价税合计,开票日期,校验码），
  不是全字段 JSON——只能拿发票号/合计/日期，完整字段靠版式文本；
- 文本抽取改"值类型识别 + 勾稽三元组"（真实版式标签块与值块分离，标签匹配不可靠）：
  发票号=20位数字、日期=YYYY年M月D日、税号=91开头18位、公司名=含'公司|集团|厂'行、
  金额=¥价税合计 + 勾稽驱动的 (amount, tax) 三元组选择；
- 多页遍历找二维码（二维码可能不在第 1 页）；
- 扫描型分流：无可提取文本才判扫描件；加密/页数/大小限制；
- 输出 NormalizedInvoice（对齐 XML 契约），勾稽不符告警不抛错。
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

import fitz  # PyMuPDF

from .models import NormalizedInvoice

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB（与 XML 对齐）
MAX_PAGES = 20                    # 页数上限（防超大 PDF）
_QR_SCALE = 6                     # 二维码渲染缩放（真实票 6 倍解码稳定）

_INVOICE_NO_RE = re.compile(r"\b(\d{20})\b")
_DATE_RE = re.compile(r"(\d{4})\s*(?:年|[/\-.])\s*(\d{1,2})\s*(?:月|[/\-.])\s*(\d{1,2})\s*日?")
_TAXID_RE = re.compile(r"\b(91[0-9A-Z]{16})\b")          # 统一社会信用代码 18 位，91 开头
_COMPANY_RE = re.compile(r"[\u4e00-\u9fff（）()A-Za-z0-9]{2,40}(?:公司|集团|中心|厂|研究院|事务所)")
_YEN_RE = re.compile(r"[¥￥]\s*([\d,]+\.\d{2})")
_NUM_RE = re.compile(r"\d[\d,]*\.\d{2}")


def _clean_money(s: str) -> Decimal:
    try:
        return Decimal(s.replace(",", "").replace("¥", "").replace("￥", "").strip())
    except InvalidOperation:
        return Decimal("0")


# ---------------- 二维码路径 ----------------

def _decode_qr(pix: "fitz.Pixmap") -> Optional[str]:
    """灰度 pixmap → numpy 数组 → zxingcpp 解码，返回首个 QR 文本。"""
    try:
        import numpy as np
        import zxingcpp
    except Exception:
        return None
    try:
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        for r in zxingcpp.read_barcodes(arr):
            if r.format == zxingcpp.BarcodeFormat.QRCode:
                return r.text
    except Exception:
        return None
    return None


def _parse_qr_core(txt: str) -> dict:
    """数电票二维码短码：逗号分隔（版本,票种,发票号,价税合计,开票日期YYYYMMDD,校验码）。
    部分平台二维码是 JSON 变体，兼容解析。"""
    out = {}
    no = _INVOICE_NO_RE.search(txt)
    if no:
        out["invoice_no"] = no.group(1)
    m = re.search(r"(\d{4})(\d{2})(\d{2})", txt)
    if m and 1900 < int(m.group(1)) < 2100:
        out["issue_date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    for tok in txt.split(","):
        tok = tok.strip()
        if re.fullmatch(r"\d[\d,]*\.\d{2}", tok) and len(tok) < 20:
            out["total"] = _clean_money(tok)
            break
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            out["_json"] = data
    except Exception:
        pass
    return out


# ---------------- 文本路径（值类型识别 + 勾稽三元组） ----------------

def _extract_text_fields(text: str, qr_total: Decimal = Decimal("0")) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    joined = "\n".join(lines)

    no = _INVOICE_NO_RE.search(joined)
    date = _DATE_RE.search(joined)
    taxids = _TAXID_RE.findall(joined)
    cos = _COMPANY_RE.findall(joined)
    yens = [_clean_money(x) for x in _YEN_RE.findall(joined)]

    # 公司名去重保序（购买方=第一个，销售方=第二个）
    seen, unique = set(), []
    for c in cos:
        if c not in seen:
            seen.add(c); unique.append(c)

    # total：优先二维码核验码（真实票可靠）→ '小写'标签后首个¥ → 兜底最大¥
    total = Decimal("0")
    if yens:
        total = max(yens)  # 兜底：价税合计通常是最大¥（税额≤合计）
    total = max(total, qr_total)
    # 金额三元组：amount+tax≈total（容差 ±0.01），选最大 amount
    amount = tax = Decimal("0")
    if total > 0:
        nums = sorted({_clean_money(x) for x in _NUM_RE.findall(joined) if _clean_money(x) < total})
        best = None
        for a in nums:
            tt = total - a
            # tax 须是数字集中真实存在的值（amount+tax==total 勾稽）
            if any(abs(x - tt) < Decimal("0.011") for x in nums):
                if best is None or a > best[0]:
                    best = (a, tt if tt >= 0 else Decimal("0"))
        if best:
            amount, tax = best
        elif "免税" in joined or "***" in joined:
            # 免税/整额票：明细金额整数且无税额（普票常见），价税合计即金额
            amount, tax = total, Decimal("0")

    return {
        "invoice_no": no.group(1) if no else "",
        "issue_date": f"{int(date.group(1)):04d}-{int(date.group(2)):02d}-{int(date.group(3)):02d}"
        if date and 1 <= int(date.group(2)) <= 12 and 1 <= int(date.group(3)) <= 31 else "",
        "amount": amount, "tax": tax, "total": total,
        "buyer_name": unique[0] if unique else "",
        "seller_name": unique[1] if len(unique) > 1 else "",
        "buyer_taxid": taxids[0] if taxids else "",
        "seller_taxid": taxids[1] if len(taxids) > 1 else "",
        "invoice_type": "电子发票",
    }


# ---------------- 主流程 ----------------

def parse_pdf(data: bytes, source_hash: str = "") -> NormalizedInvoice:
    """解析 PDF，输出 NormalizedInvoice；扫描件抛 PDFNeedsOCR，加密抛 PDFEncrypted。"""
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("文件超过 10MB 上限")
    if not source_hash:
        source_hash = hashlib.sha256(data).hexdigest()[:16]

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF 页数 {doc.page_count} 超过上限 {MAX_PAGES}")
        if doc.is_encrypted:
            raise PDFEncrypted("PDF 已加密，请提供未加密文件")

        qr_core = {}
        full_text = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(_QR_SCALE, _QR_SCALE), colorspace=fitz.csGRAY)
            qr = _decode_qr(pix)
            if qr and not qr_core:
                qr_core = _parse_qr_core(qr)
            full_text.append(page.get_text("text"))

        text = "\n".join(full_text)
        if len(text.strip()) < 10:
            raise PDFNeedsOCR(f"PDF 无可提取文本（{len(text.strip())} 字符），疑似扫描件")

        tf = _extract_text_fields(text, qr_total=qr_core.get("total", Decimal("0")))
        warnings = []

        # 二维码核心字段与文本校验（不一致告警，不覆盖）
        if qr_core.get("invoice_no") and tf["invoice_no"] and qr_core["invoice_no"] != tf["invoice_no"]:
            warnings.append("二维码发票号与版式文本不一致")
        if qr_core.get("total") and tf["total"] and qr_core["total"] != tf["total"]:
            warnings.append("二维码价税合计与版式文本不一致")

        inv = NormalizedInvoice(
            invoice_no=tf["invoice_no"] or qr_core.get("invoice_no", ""),
            issue_date=tf["issue_date"] or qr_core.get("issue_date", ""),
            amount=tf["amount"], tax=tf["tax"],
            total=tf["total"],
            buyer_name=tf["buyer_name"], buyer_taxid=tf["buyer_taxid"],
            seller_name=tf["seller_name"], seller_taxid=tf["seller_taxid"],
            invoice_type=tf["invoice_type"],
            source_hash=source_hash,
            raw_fields={"parse_path": "qr+text", "qr_core": qr_core, "pdf_text_head": text[:300]},
        )
        missing = [k for k in ("invoice_no", "issue_date", "total", "buyer_name", "seller_name")
                   if not getattr(inv, k)]
        if missing:
            warnings.append(f"字段缺失: {','.join(missing)}")
        if inv.total > 0 and inv.amount + inv.tax != inv.total:
            warnings.append("价税勾稽不符（需人工复核）")
        inv.parse_warnings = warnings
        return inv
    finally:
        doc.close()


class PDFNeedsOCR(Exception):
    """扫描型 PDF：当前阶段不支持，需 OCR（Week2 启用 OcrProvider）。"""


class PDFEncrypted(Exception):
    """加密 PDF：无法解析。"""
