"""OCR Provider（DeepSeek 评审：本地 OCR 服务，接口可插拔，数据不出本机）。

架构：
- OcrProvider 协议：name / requires_network / recognize()——云端可插拔不锁死；
- RapidOcrProvider：ONNX Runtime 本地推理，requires_network=False（敏感数据只能走本地）；
- 惰性加载引擎（首次调用才 import/初始化，避免无 OCR 需求时拖慢启动）；
- PDF 渲染：300dpi PNG（DeepSeek 验收标准）→ OCR。
"""
from __future__ import annotations

import io
import os
from typing import Protocol, runtime_checkable

import fitz


@runtime_checkable
class OcrProvider(Protocol):
    name: str
    requires_network: bool

    def recognize(self, image_bytes: bytes) -> str:
        """输入图片字节，返回识别文本（多行）。"""


class RapidOcrProvider:
    """本地 RapidOCR（ONNX CPU 推理，数据不出本机）。"""

    name = "rapid_ocr"
    requires_network = False
    _engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            RapidOcrProvider._engine = RapidOCR()
        return RapidOcrProvider._engine

    def recognize(self, image_bytes: bytes) -> str:
        return self.recognize_with_conf(image_bytes)[0]

    def recognize_with_conf(self, image_bytes: bytes) -> tuple[str, list[float]]:
        """返回 (文本, 每行置信度)；低置信度供调用方标'需人工复核'。"""
        engine = self._get_engine()
        result, _ = engine(image_bytes)
        if not result:
            return "", []
        lines = [line[1] for line in result]
        confs = [float(line[2]) if len(line) > 2 and line[2] is not None else 1.0 for line in result]
        return "\n".join(lines), confs


def render_pdf_page(pdf_bytes: bytes, page_index: int = 0, dpi: int | None = None) -> bytes:
    """PDF 页 → PNG bytes（OCR 输入）。

    速度优化（DeepSeek 优化序，OPEN_ISSUES #54）：
    - 文本层/二维码短路已内置（有文本层不 OCR）；
    - dpi 可经环境变量 OCR_DPI 调（默认 300 保精度；内测提速可 200，需精度回归后定）；
    - 版面裁剪/并行分页待真实样本回归后启用（暂不动，防伤精度）。
    """
    if dpi is None:
        dpi = int(os.environ.get("OCR_DPI", "300"))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        return pix.tobytes("png")
    finally:
        doc.close()


def provider_factory(name: str = "rapid_ocr") -> OcrProvider:
    """按名取 Provider；未知名称回退本地（敏感数据安全兜底）。"""
    if name == "rapid_ocr":
        return RapidOcrProvider()
    return RapidOcrProvider()
