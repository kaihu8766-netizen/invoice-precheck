"""T-030 解析安全攻击样本回归（RV-20260930-341 方案 adopted）。

覆盖：OFD 四重限制中未测的「单条目超限」「文件本体超限」、zip 路径穿越条目、
高压缩比 zip bomb、嵌套 zip；XML 元素数超限、超大文本节点截断。
XXE / billion laughs 已在 test_parser.py 覆盖（不重复）。
P0 正向回归：合法 OFD / XML 样本必须仍通过（防"一律拒绝"实现假绿）。
"""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ofd import extract_invoice_xml, MAX_OFD_BYTES, MAX_OFD_ENTRY, MAX_OFD_TOTAL
from app.parser import parse_xml, MAX_ELEMENTS, _MAX_TEXT


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


# 合法数电票 XML 样本（与 test_parser 同源语义，字段齐备）
NS = "http://example.com/invoice"
SAMPLE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<发票 xmlns="{NS}">
  <发票号码>04300260031112345678</发票号码>
  <开票日期>20260815</开票日期>
  <发票类型>增值税专用发票</发票类型>
  <购买方><购买方名称>示例科技</购买方名称><购买方纳税人识别号>91310000MA1FL1XXXX</购买方纳税人识别号></购买方>
  <销售方><销售方名称>北京华信</销售方名称><销售方纳税人识别号>91110108XXXXXXXXXX</销售方纳税人识别号></销售方>
  <合计金额>1000.00</合计金额>
  <合计税额>130.00</合计税额>
  <价税合计>1130.00</价税合计>
</发票>
"""


def _valid_ofd() -> bytes:
    """构造合法 OFD（含发票 XML 的 zip）。"""
    return _make_zip([("Doc_0/Document.xml", SAMPLE_XML.encode("utf-8"))])


class TestOfdSecurity(unittest.TestCase):
    """OFD 容器安全（未覆盖上限 + 路径穿越 + bomb + 嵌套）。"""

    def test_single_entry_exceeds_limit_rejected(self):
        """单条目 > 20MB → 拒绝（MAX_OFD_ENTRY，RV-341 指出未覆盖）。"""
        big = SAMPLE_XML.encode() + b"A" * (MAX_OFD_ENTRY + 1)
        z = _make_zip([("Doc_0/Document.xml", big)])
        with self.assertRaises(ValueError) as ctx:
            extract_invoice_xml(z)
        self.assertIn("超大条目", str(ctx.exception))

    def test_file_body_exceeds_limit_rejected(self):
        """OFD 文件本体 > 10MB → 拒绝（MAX_OFD_BYTES，RV-341 指出未覆盖）。"""
        # 用大量重复条目凑出 >10MB 的 zip（但单条目不超限）
        entries = []
        chunk = SAMPLE_XML.encode() * 40  # 每条约 10KB
        for i in range(60):
            entries.append((f"Doc_{i}/Document.xml", chunk))  # 共约 600KB，不够
        z = _make_zip(entries)
        # 追加一个占位把本体撑大：直接构造超大容器
        big = _make_zip([("Doc_0/Document.xml", SAMPLE_XML.encode())])
        big += b"padding" * (MAX_OFD_BYTES + 1)  # 破坏 zip 结构但先测大小检查
        # 大小检查在 ZipFile 打开前，所以非法 zip 也能测到大小拒绝
        from app.ofd import extract_invoice_xml as _ex
        with self.assertRaises(ValueError) as ctx:
            _ex(big)
        self.assertIn("文件过大", str(ctx.exception))

    def test_path_traversal_entry_not_crash(self):
        """zip 条目含 ../ 或绝对路径 → 不崩溃（实现不写盘仅内存读取，语义=不可利用）。"""
        for evil_name in ("../evil.xml", "/etc/evil.xml", "..\\evil.xml", "a/../../evil.xml"):
            z = _make_zip([(evil_name, SAMPLE_XML.encode())])
            try:
                result = extract_invoice_xml(z)
                # 若被识别为合法发票（路径规范化后）也应正常返回不崩溃
                self.assertIsInstance(result, bytes)
            except ValueError:
                pass  # 拒绝也符合安全语义（fail-closed）

    def test_high_ratio_zip_bomb_rejected(self):
        """高压缩比 zip bomb（小容器解压巨大）→ 总大小上限拒绝。"""
        # 压缩比约 1000:1：1KB 压缩数据解压出 50MB+（用重复字节，zip 压缩率高）
        big_content = "<发票>".encode() + b"A" * (MAX_OFD_TOTAL + 1) + "</发票>".encode()
        z = _make_zip([("Doc_0/Document.xml", big_content)])
        with self.assertRaises(ValueError) as ctx:
            extract_invoice_xml(z)
        self.assertIn("zip 炸弹", str(ctx.exception))

    def test_nested_zip_rejected(self):
        """zip 内嵌 zip（无发票 XML）→ 拒绝。"""
        inner = _make_zip([("inner.xml", SAMPLE_XML.encode())])
        z = _make_zip([("Doc_0/nested.zip", inner)])
        with self.assertRaises(ValueError):
            extract_invoice_xml(z)

    def test_positive_valid_ofd_passes(self):
        """P0 正向回归：合法 OFD 必须通过（防"一律拒绝"假绿）。"""
        result = extract_invoice_xml(_valid_ofd())
        self.assertIn("发票号码", result.decode("utf-8", errors="ignore"))


class TestXmlSecurity(unittest.TestCase):
    """XML 解析安全（元素数/文本上限 + 正向回归）。"""

    def test_element_count_exceeds_limit_rejected(self):
        """XML 元素数 > 50000 → 拒绝（防深度嵌套 DoS）。"""
        # 生成 > MAX_ELEMENTS 个元素
        children = "".join("<x/>" for _ in range(MAX_ELEMENTS + 10))
        xml = f'<?xml version="1.0"?><发票><发票号码>04300260031112345678</发票号码>{children}</发票>'
        with self.assertRaises(ValueError) as ctx:
            parse_xml(xml.encode())
        self.assertIn("元素数", str(ctx.exception))

    def test_huge_text_node_truncated_not_crash(self):
        """超大文本节点（>20000 字符）→ 截断不崩溃。"""
        big_text = "值" * (_MAX_TEXT + 5000)
        xml = SAMPLE_XML.replace("北京华信", big_text)
        inv = parse_xml(xml.encode())
        self.assertIsNotNone(inv)
        # 截断后的字段不应超过上限太多
        self.assertLessEqual(len(inv.seller_name or ""), _MAX_TEXT + 100)

    def test_positive_valid_xml_passes(self):
        """P0 正向回归：合法 XML 必须通过。"""
        inv = parse_xml(SAMPLE_XML.encode())
        self.assertEqual(inv.invoice_no, "04300260031112345678")
        self.assertEqual(str(inv.total), "1130.00")


if __name__ == "__main__":
    unittest.main()
