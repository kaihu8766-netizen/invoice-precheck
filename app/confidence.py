#!/usr/bin/env python3
"""confidence.py —— 字段级置信度计算（T-054 · RV-20261001-362 修订）。

公式（DeepSeek 有条件通过后的修订版，弃"全行 min 聚合"）：
    field_conf = 路径先验 × Π(字段级证据惩罚因子) × OCR 行乘子（仅 OCR 路径）

- 路径先验（parse_path）：直读 XML/OFD=0.99 > PDF 文本层=0.95 > OCR=0.85（ADR-001 解析优先级）
- 字段级证据惩罚：解析层（pdf.py）按字段输出的 0-1 因子——qr 与文本不一致、勾稽不符、
  版式反转、字段缺失；**按字段可解释，不做全票一值**（页脚/水印噪声不再拉爆所有字段）
- OCR 行乘子：OCR 路径时用行 confs 的 P25（而非 min，避免单条噪声行过度惩罚）；
  无行 confs 时乘子=1
- 缺失字段：置信度=0（"没有值"本身就是最强信号）
"""
from __future__ import annotations

from dataclasses import dataclass

# 路径先验（parse_path 常量；与 app/parser.py 的 detect_type 语义对齐）
PATH_PRIOR = {
    "xml": 0.99, "ofd": 0.99,
    "qr+text": 0.95, "text": 0.93,
    "qr+ocr": 0.85, "image+ocr": 0.85,
}
DEFAULT_PRIOR = 0.90

# 三档阈值（与现有 review 阈值 0.80 对齐；high 界定"可直接通过"）
HIGH = 0.95
MID = 0.80

# 参与置信度计算的字段（与基准集字段比对口径一致）
CONF_FIELDS = ("invoice_no", "issue_date", "amount", "tax", "total",
               "buyer_name", "buyer_taxid", "seller_name", "seller_taxid")


def _p25(confs: list[float]) -> float:
    """行 confs 的近似 P25（保守但抗单条噪声；小 n 时索引退化为更低分位）。

    注意：n<5 时 int(n*0.25)-1≤0，实为 min——近似分位，文档如实标注
    （RV-363 偏差2：非严格 P25；OCR 行无字段映射，整票单乘子为已知局限）。"""
    if not confs:
        return 1.0
    s = sorted(float(c) for c in confs)
    i = max(0, int(len(s) * 0.25) - 1)
    return min(1.0, max(0.0, s[i]))


def field_confidence(inv) -> dict[str, float]:
    """计算字段级置信度并回填 inv.field_conf（0-1，缺失字段=0）。"""
    path = (inv.raw_fields or {}).get("parse_path", "")
    prior = PATH_PRIOR.get(path, DEFAULT_PRIOR)
    ev = inv.field_evidence or {}
    is_ocr = "ocr" in path
    ocr_factor = _p25(inv.ocr_confs or []) if is_ocr else 1.0

    conf: dict[str, float] = {}
    for f in CONF_FIELDS:
        v = getattr(inv, f, "")
        if v is None or str(v) == "":
            conf[f] = 0.0
            continue
        c = prior * float(ev.get(f, 1.0)) * ocr_factor
        conf[f] = round(min(1.0, max(0.0, c)), 3)
    inv.field_conf = conf
    return conf


def bucket(score: float) -> str:
    """三档：high≥0.95 / mid 0.80-0.95 / low<0.80（阈值与 review 对齐）。"""
    if score >= HIGH:
        return "high"
    if score >= MID:
        return "mid"
    return "low"


def bucket_of(inv, field: str) -> str:
    return bucket(inv.field_conf.get(field, 0.0))


@dataclass
class WilsonResult:
    p: float
    lo: float
    hi: float
    n: int

    def __str__(self) -> str:
        return f"{self.p:.3f} [{self.lo:.3f}, {self.hi:.3f}] (n={self.n})"


def wilson_interval(k: int, n: int, z: float = 1.96) -> WilsonResult | None:
    """95% Wilson score 区间（小样本二项比例；n<5 返回 None=样本不足）。"""
    if n < 5:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return WilsonResult(p=p, lo=centre - margin, hi=centre + margin, n=n)
