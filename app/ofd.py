"""OFD 容器解包（A1 2026-09-23）：数电票 OFD（zip 容器）→ 提取内嵌发票 XML 数据。

背景：数电票 OFD 版式文件为 GB/T 33190-2016 定义的 zip 容器，内嵌电子发票 XML 数据
（财政部数电票标准：OFD 版式 + XML 数据双层交付）。本模块只做解包与 XML 提取，
不做 OCR/LLM 兜底（DeepSeek 下一步讨论 A1 边界：仅官方方向、可验收）。

安全（对齐 parser 防 DoS 基线）：
- 文件大小 / 解压总大小 / 单条目大小 / 条目数 四重上限（zip 炸弹防护）
- 只读内存解包（不写盘，无路径穿越写入风险；条目名仅用于识别与日志）

识别策略：
- 候选 = zip 内全部 .xml 条目（排除 OFD.xml 版式入口描述）
- 发票 XML 特征打分：根节点 发票/Invoice/EInvoice（+20）+ 票面字段特征（发票号码/InvoiceNumber/
  EIid/价税合计/TotalTax，每条 +1）
- 取最高分候选；无候选或最高分=0 → ValueError（明确报错，不静默）

边界（诚实声明）：真实数电票 OFD 内部布局（内嵌 XML 命名/位置/是否存在多重数据文件）
待真实 OFD 文件核验——当前基于 GB/T 33190 骨架 + 官方票样合成容器验证。
"""
from __future__ import annotations

import io
import re
import zipfile

MAX_OFD_BYTES = 10 * 1024 * 1024       # 文件本体上限 10MB（与 parser.MAX_FILE_SIZE 对齐）
MAX_OFD_ENTRIES = 500                  # zip 条目数上限（防条目洪水）
MAX_OFD_TOTAL = 50 * 1024 * 1024       # 解压总大小上限 50MB（防 zip 炸弹）
MAX_OFD_ENTRY = 20 * 1024 * 1024       # 单条目大小上限 20MB

# 发票根节点特征（xml 数据文件；版式 Document.xml 根节点为 OFD/Document，不匹配）
_ROOT_HINTS = ("<发票", "<Invoice", "<EInvoice")
# 票面字段特征（每条 +1，辅助多候选排序）
_INVOICE_SCORE_RE = re.compile(r"发票号码|InvoiceNumber|EIid|价税合计|TotalTax|TotalAmWithoutTax")


def _safe_zip_name(name: str) -> str:
    """条目名规范化（仅识别/日志用，不写盘）。"""
    return (name or "").replace("\\", "/").lstrip("/")


def _score_xml(content: bytes) -> int:
    """发票 XML 候选打分：根节点特征 +20；票面字段特征每条 +1。"""
    head = content[:4096].decode("utf-8", errors="ignore")
    score = 0
    if any(h in head for h in _ROOT_HINTS):
        score += 20
    for _ in _INVOICE_SCORE_RE.findall(head):
        score += 1
    return score


def extract_invoice_xml(data: bytes) -> bytes:
    """OFD 容器解包 → 内嵌发票 XML bytes（最高分候选）。

    找不到/无法识别 → ValueError（明确报错，带安全文案，不泄漏条目细节）。
    """
    if len(data) > MAX_OFD_BYTES:
        raise ValueError(f"文件过大（>{MAX_OFD_BYTES // 1024 // 1024}MB），已拒绝解析")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("OFD 容器损坏（非合法 zip 包），无法解包") from e

    names = zf.namelist()
    if len(names) > MAX_OFD_ENTRIES:
        raise ValueError(f"OFD 条目数超过上限（>{MAX_OFD_ENTRIES}），疑似资源耗尽攻击，已拒绝")

    total = 0
    candidates: list[tuple[int, str, bytes]] = []
    for n in names:
        info = zf.getinfo(n)
        if info.file_size > MAX_OFD_ENTRY:
            raise ValueError("OFD 内存在超大条目，疑似 zip 炸弹，已拒绝")
        total += info.file_size
        if total > MAX_OFD_TOTAL:
            raise ValueError("OFD 解压总大小超过上限，疑似 zip 炸弹，已拒绝")
        safe = _safe_zip_name(n)
        if not safe.lower().endswith(".xml"):
            continue
        if safe.lower() == "ofd.xml":
            continue  # 版式入口描述（GB/T 33190 固定），非发票数据
        try:
            content = zf.read(n)
        except Exception as e:
            raise ValueError("OFD 条目读取失败（容器可能损坏）") from e
        if content:
            candidates.append((_score_xml(content), safe, content))

    if not candidates:
        raise ValueError("OFD 中未找到内嵌发票 XML（仅支持含电子发票 XML 数据的数电票 OFD）")
    best_score, best_name, best_content = max(candidates, key=lambda c: c[0])
    if best_score == 0:
        raise ValueError(
            "OFD 内未识别到发票 XML（内嵌 XML 根节点非 发票/Invoice/EInvoice；"
            "真实数电票 OFD 布局待核验）"
        )
    return best_content
