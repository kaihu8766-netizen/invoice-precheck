"""图片发票解析（F-20260923-06 手机拍照，RV-49）。

链路：图片字节 → EXIF 转正 → 长边限制 → RapidOCR（本机，数据不出）→ 字段提取
（复用 pdf.py 的 _extract_text_fields 纯函数，防两条路径抽取规则漂移）。

契约（RV-49）：
- 单张图片 = 单张发票（多票同框首版不支持，UI 提示）
- OCR 结果为空 → 明确报错（前端提示重拍）
- 关键字段（发票号+金额）缺失 → 明确报错（防静默出空报告）
- 部分字段缺失 → review_needed=True（低置信度/缺失走人工复核）
- 即用即删：main.py 内存读入，本模块不落盘
"""
from __future__ import annotations

from decimal import Decimal

from .models import NormalizedInvoice

MAX_EDGE = 1800  # RV-49 P0-3：OCR 前长边上限（4000×3000 原图直喂是性能/内存风险）


def preprocess_image(data: bytes, max_edge: int = MAX_EDGE) -> bytes:
    """图片预处理：解码 → EXIF 转正（手机拍照约九成带 Orientation tag，不转 OCR 报废）→
    长边缩放 → 编码 PNG bytes（RapidOCR 输入）。失败抛 ValueError（友好提示）。
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as e:  # pragma: no cover
        raise ValueError("图片处理依赖 Pillow 未安装") from e
    try:
        with Image.open(__import__("io").BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)  # RV-49 P0-2：EXIF 旋转必须做
            im = im.convert("RGB")
            w, h = im.size
            if max(w, h) > max_edge:
                scale = max_edge / float(max(w, h))
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                               Image.LANCZOS)
            buf = __import__("io").BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    except ValueError as e:
        raise ValueError("图片无法解码（格式不支持或文件损坏）") from e


def _text_to_invoice(data: bytes, text: str) -> NormalizedInvoice:
    """OCR 文本 → NormalizedInvoice（与 xml/ofd/pdf 完全同构；复用 pdf._extract_text_fields）。"""
    from .pdf import _extract_text_fields

    tf = _extract_text_fields(text)
    no = tf["invoice_no"]
    total = tf["total"]
    if not no and total <= 0:
        raise ValueError("未识别到关键字段（发票号/金额），请正拍重试或更换光线")
    warnings = ["图片路径：OCR 识别（本机），版面与字段以识别结果为准"]
    review = not (no and total > 0)  # 关键字段缺失 → 人工复核，不自动放行
    return NormalizedInvoice(
        invoice_no=no,
        invoice_type="电子发票",
        issue_date=tf["issue_date"],
        amount=tf["amount"],
        tax=tf["tax"],
        total=tf["total"],
        buyer_name=tf["buyer_name"],
        buyer_taxid=tf["buyer_taxid"],
        seller_name=tf["seller_name"],
        seller_taxid=tf["seller_taxid"],
        source_hash=__import__("hashlib").sha256(data).hexdigest()[:32],
        parse_warnings=warnings,
        raw_fields={"parse_path": "image+ocr", **tf},
        is_red_letter=False,
        is_differential=False,
        items=[],
        differential_deduction=None,
        red_letter_blue_no="",
        review_needed=review,
    )


def parse_image(data: bytes) -> NormalizedInvoice:
    """图片发票主入口：预处理 → OCR → 字段 → NormalizedInvoice。"""
    png = preprocess_image(data)
    from .ocr import provider_factory

    try:
        ocr = provider_factory()
        text, confs = ocr.recognize_with_conf(png)
    except Exception as e:
        raise ValueError(f"本地 OCR 不可用：{e}（图片路径需本机 RapidOCR）") from e
    if not text.strip():
        raise ValueError("未识别到发票内容，请正拍重试（避免反光/歪斜）")
    inv = _text_to_invoice(data, text)
    # 低置信度（<0.8 的关键行）→ 人工复核
    if confs:
        low = [c for c in confs if c < 0.80]
        if low:
            inv.review_needed = True
            inv.parse_warnings.append(
                f"OCR 置信度偏低（{len(low)} 行 <0.80），建议人工核对")
    return inv
