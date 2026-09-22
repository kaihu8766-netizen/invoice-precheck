"""数电票 XML 解析（P1 技术方案第 1 节 · DeepSeek 评审采纳 + 2026-09-22 代码审查修订 + 里程碑评审加固）。

设计要点：
- localname 匹配（tag.rsplit('}',1)[-1]）兼容命名空间/厂商差异；
- 金额/税额/合计类字段取"最后出现"（合计节点通常在文档尾部，避免采到明细行金额）；
- 金额全链路 Decimal(str)，禁 float；税额 '*' 或空 → 0 + 告警；
- 勾稽 amount + tax == total，容差 ±0.01，不符记"勾稽异常"不抛错；
- iterparse 读 BytesIO，规避编码声明不符；字段缺失进 parse_warnings 不中断；
- detect_type 前置：剥 BOM、支持无 prolog 的合法 XML；非 XML 输入明确报"不支持类型"；
- raw_fields 白名单收集 + 税号脱敏（防 PII 外泄）；
- 文件大小上限（防超大文件/资源耗尽）；
- 安全加固（DeepSeek 里程碑评审）：defusedxml 禁 DTD/外部实体/实体膨胀；签名子树剥离；
  元素数上限防深度嵌套 DoS；单 XML 多票节点检测（禁止静默丢票）。
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO

from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from .models import NormalizedInvoice

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_ELEMENTS = 50_000  # XML 元素数上限（防深度嵌套/事件膨胀 DoS）
_MAX_TEXT = 20_000  # 单节点文本长度上限（防超大文本节点）

# 签名子树（数电票普遍带 XMLDSig，内含 X509SubjectName/Base64 大块；
# 全局搜索会命中签名内同名节点，必须先剥离再取值）
_SIGNATURE_TAGS = {"Signature", "XMLSignature", "Signatures"}

# 发票根节点（用于多票检测：单 XML 含多个发票 → 显式告警，禁止静默取最后）
_ROOT_TAGS = {"发票", "Invoice", "EInvoice"}

# 固定标签容器（EInvoice 英文结构）：InherentLabel 下的 LabelCode/LabelName
# 需按父节点归位（多个 Label* 冲突，扁平化会丢失"专票/普票"语义）
_LABEL_CONTAINERS = {"InIssuType", "EInvoiceType", "GeneralOrSpecialVAT", "TaxpayerType"}

# 金额合计路径注册表（DeepSeek 评审 M4）：票面合计按全路径锁定，不靠大小写/拼写。
# 路径键优先于扁平别名——同名字段出现在不同路径（如明细行 TotaltaxIncludedAmount）不混入。
_PATH_KEYS = {
    "EInvoice/EInvoiceData/BasicInformation/TotalAmWithoutTax": "TotalAmWithoutTax#basic",
    "EInvoice/EInvoiceData/BasicInformation/TotalTaxAm": "TotalTaxAm#basic",
    "EInvoice/EInvoiceData/BasicInformation/TotalTax-includedAmount": "TotalTax-includedAmount#basic",
}

# 中文标签 → 模型字段（别名映射；金额/税额/合计只用"合计类"明确标签，防歧义）
# 三方言兼容（2026-09-23 语料库调研结论）：
#  ① 数电票 XML 中文标签（财政部电子凭证会计数据标准）
#  ② 传统电子发票国标拼音缩写（GB/T 电子发票业务数据规范：FPHM/HJJE/JSHJXX 等）
#  ③ EInvoice 英文结构（真实数电票 XML 主流：Header/EInvoiceData/TaxSupervisionInfo；
#     官方标准样例与网约车/打车软件等第三方开票系统均为此结构）
# 注意：TotalTax-includedAmount（票面合计）与 TotaltaxIncludedAmount（明细行含税金额）
# 字段名相近但语义不同——total 只映射前者，绝不含后者（否则明细负行会覆盖合计）。
# 开票日期语义（财政部元素清单）：对应"开票请求时间 RequestTime"（IssueTime 是发票生成时间，
# 两者日期一致但语义不同；数电票标准以 RequestTime 为开票日期）。
_ALIAS = {
    "invoice_no": ["发票号码", "发票代码及号码", "FPHM", "InvoiceNumber"],
    "issue_date": ["开票日期", "发票开具日期", "KPRQ", "RequestTime", "IssueTime"],
    "buyer_name": ["购买方名称", "购方名称", "GMFMC", "BuyerName"],
    "buyer_taxid": ["购买方纳税人识别号", "购方税号", "购买方统一社会信用代码", "GMFNSRSBH", "BuyerIdNum"],
    "seller_name": ["销售方名称", "销方名称", "XSFMC", "SellerName"],
    "seller_taxid": ["销售方纳税人识别号", "销方税号", "销售方统一社会信用代码", "XSFNSRSBH", "SellerIdNum"],
    "amount": ["TotalAmWithoutTax#basic", "合计金额", "TotalAmWithoutTax", "HJJE"],
    "tax": ["TotalTaxAm#basic", "合计税额", "TotalTaxAm", "HJSE"],
    "total": ["TotalTax-includedAmount#basic", "价税合计(小写)", "价税合计",
              "TotalTaxIncludedAm", "合计", "JSHJXX", "TotalTax-includedAmount"],
    "invoice_type": ["发票类型", "票种", "FPZL",
                     "GeneralOrSpecialVAT.LabelName", "EInvoiceType.LabelName"],
}

# 取"最后出现"的字段（合计节点通常在文档尾部，明细行在前；避免采到首行明细金额）
_LAST_WINS = {"合计金额", "合计税额", "价税合计", "价税合计(小写)", "合计",
              "TotalAmWithoutTax", "TotalTaxAm", "TotalTaxIncludedAm", "TotalTax-includedAmount",
              "TotalAmWithoutTax#basic", "TotalTaxAm#basic", "TotalTax-includedAmount#basic",
              "HJJE", "HJSE", "JSHJXX"}

# raw_fields 白名单（仅保留规则用得到的键，防整张票面外泄）
_RAW_WHITELIST = {
    "发票号码", "开票日期", "发票类型",
    "购买方名称", "购买方纳税人识别号",
    "销售方名称", "销售方纳税人识别号",
    "合计金额", "合计税额", "价税合计",
    # EInvoice 英文结构键（三方言）
    "InvoiceNumber", "IssueTime",
    "BuyerName", "BuyerIdNum", "SellerName", "SellerIdNum",
    "TotalAmWithoutTax", "TotalTaxAm", "TotalTax-includedAmount",
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


def _is_taxid_key(key: str) -> bool:
    """判断字段名是否税号/识别号类（中文键与 EInvoice 英文键都覆盖）。"""
    k = key.lower()
    return any(x in k for x in ("税号", "识别号", "idnum", "taxid"))


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
    # 兼容：20260815 / 2026-08-15 / 2026/08/15 / 2026-08-15 10:08:05（带时间截断取日期）
    m = re.match(r"(\d{4})([-/]?)(\d{1,2})\2(\d{1,2})(?:[\sT].*)?$", s)
    if m:
        y, _, mo, d = m.groups()
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


def _collect_fields(data: bytes, warnings: list[str]) -> tuple[dict[str, str], int]:
    """扁平化收集字段：普通字段取首个，金额/合计类取最后出现。

    返回 (fields, invoice_node_count)；count>1 表示单 XML 含多个发票节点。
    安全：defusedxml（禁 DTD/外部实体/膨胀）；签名子树剥离；元素数上限。
    """
    fields: dict[str, str] = {}
    invoice_node_count = 0
    depth = 0
    skip_depth: int | None = None
    elem_count = 0
    stack: list[str] = []  # 祖先 localname 栈（用于 InherentLabel 下 Label* 按父节点归位）
    try:
        context = DET.iterparse(BytesIO(data), events=("start", "end"))
        for event, elem in context:
            name = _localname(elem.tag)
            if event == "start":
                depth += 1
                elem_count += 1
                if elem_count > MAX_ELEMENTS:
                    raise ValueError(
                        f"XML 元素数超过上限（>{MAX_ELEMENTS}），疑似资源耗尽攻击，已拒绝"
                    )
                if name in _ROOT_TAGS and depth > 0:
                    invoice_node_count += 1
                if skip_depth is None and name in _SIGNATURE_TAGS:
                    skip_depth = depth  # 进入签名子树：以下节点全部跳过
                stack.append(name)
                continue
            # end 事件
            if skip_depth is not None:
                if depth == skip_depth:
                    skip_depth = None  # 离开签名子树
                depth -= 1
                stack.pop()
                elem.clear()
                continue
            if elem.text and elem.text.strip():
                text = elem.text.strip()
                if len(text) > _MAX_TEXT:
                    text = text[:_MAX_TEXT]
                parent = stack[-2] if len(stack) >= 2 else None
                path = "/".join(stack)
                if path in _PATH_KEYS:
                    # 金额合计路径注册表：全路径锁定（DeepSeek 评审 M4）
                    fields[_PATH_KEYS[path]] = text
                elif name in ("LabelCode", "LabelName") and parent in _LABEL_CONTAINERS:
                    # EInvoice 英文结构：InherentLabel 下多个 Label* 冲突，按父节点归位
                    fields[f"{parent}.{name}"] = text
                elif name in _LAST_WINS:
                    fields[name] = text  # 合计类覆盖（文档尾部才是合计）
                else:
                    fields.setdefault(name, text)
            depth -= 1
            stack.pop()
            elem.clear()
    except DET.ParseError as e:
        raise ValueError(f"XML 结构损坏：{e}") from e
    except DefusedXmlException as e:
        raise ValueError("XML 含被禁止的实体/DTD（外部实体与内部实体膨胀已拒绝）") from e
    return fields, invoice_node_count


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
    fields, invoice_node_count = _collect_fields(data, warnings)

    if invoice_node_count > 1:
        warnings.append(
            f"检测到 {invoice_node_count} 个发票节点（批量导出可能多票合一），仅解析最后一个，"
            "请单文件单票上传以避免丢票"
        )

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
    elif not (len(invoice_no) == 20 and invoice_no.isdigit()):
        # 数电票号码 20 位（年度2+区划2+渠道1+顺序15）；异常记告警，不判为致命
        warnings.append(f"发票号码位数/格式异常（数电票应为 20 位数字）：{invoice_no}")

    # raw_fields 白名单 + 税号脱敏
    raw_fields = {
        k: (_mask(v) if _is_taxid_key(k) else v)
        for k, v in fields.items() if k in _RAW_WHITELIST
    }

    # 票面语义标志（P0-8：红冲/差额的类型化识别，供 R8 金额异常规则消费）
    remark = fields.get("备注") or fields.get("Remark") or ""
    invoice_type_raw = pick("invoice_type") or "未知"
    is_red_letter = ("红" in invoice_type_raw) or ("红冲" in remark) or ("红字" in remark)
    is_differential = ("差额征税" in remark) or bool(fields.get("KCE"))

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
        is_red_letter=is_red_letter,
        is_differential=is_differential,
    )
