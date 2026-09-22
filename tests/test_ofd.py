"""OFD 容器解包测试（A1 2026-09-23）。

覆盖：
- 解包提取：合成 OFD（官方票样封装）→ 内嵌发票 XML == 源票样
- 统一入口 parse_document：OFD → NormalizedInvoice 字段断言（票号/合计/行级）
- 口径统一：OFD 与内嵌 XML 直接上传的 source_hash 一致（去重口径）
- 安全：非 zip / 无 XML / 无发票特征 / 条目超限 / 解压总大小超限 → ValueError
"""
import io
import sys
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ofd import MAX_OFD_ENTRIES, extract_invoice_xml
from app.parser import parse_document, parse_xml

CORPUS = Path(__file__).resolve().parent / "corpus"
OFD_SAMPLE = CORPUS / "ofd" / "ofd-container-gd-sample.ofd"
SRC_XML = CORPUS / "official-gd-special.xml"


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


class TestOfdExtraction(unittest.TestCase):
    def test_extract_matches_source_xml(self):
        """解包提取的内嵌 XML 必须与源票样逐字节一致。"""
        ofd = OFD_SAMPLE.read_bytes()
        inner = extract_invoice_xml(ofd)
        self.assertEqual(inner, SRC_XML.read_bytes())

    def test_parse_document_ofd_fields(self):
        """统一入口解析 OFD：字段与官方票样一致（票号/合计/行级/开票日期）。"""
        inv = parse_document(OFD_SAMPLE.read_bytes())
        self.assertEqual(inv.invoice_no, "23440000000000100001")
        self.assertEqual(inv.issue_date, "2023-02-23")
        self.assertEqual(str(inv.amount), "2.00")
        self.assertEqual(str(inv.tax), "0.12")
        self.assertEqual(str(inv.total), "2.12")
        self.assertEqual(len(inv.items), 1, "OFD 内嵌票样应提取行级")
        self.assertTrue(inv.items[0].name)

    def test_source_hash_consistent_with_inner_xml(self):
        """OFD 与内嵌 XML 直接上传的 source_hash 一致（去重口径统一，R1 判重不误判）。"""
        ofd_inv = parse_document(OFD_SAMPLE.read_bytes())
        xml_inv = parse_xml(SRC_XML.read_bytes())
        self.assertEqual(ofd_inv.source_hash, xml_inv.source_hash)

    def test_bad_zip_rejected(self):
        """非 zip 数据 → ValueError（明确报错）。"""
        with self.assertRaises(ValueError):
            extract_invoice_xml(b"not a zip file at all")

    def test_zip_without_xml_rejected(self):
        """zip 内无 XML 条目 → ValueError。"""
        z = _make_zip([("readme.txt", b"hello")])
        with self.assertRaises(ValueError):
            extract_invoice_xml(z)

    def test_zip_xml_without_invoice_rejected(self):
        """zip 内有 XML 但根节点非发票（如普通 Document.xml）→ ValueError。"""
        z = _make_zip([("Doc_0/Document.xml",
                        b'<?xml version="1.0"?><ofd:Document xmlns:ofd="x"/>')])
        with self.assertRaises(ValueError):
            extract_invoice_xml(z)

    def test_entry_count_limit(self):
        """条目数超限 → ValueError（防条目洪水）。"""
        z = _make_zip([(f"f{i}.xml", "<发票/>".encode()) for i in range(MAX_OFD_ENTRIES + 1)])
        with self.assertRaises(ValueError):
            extract_invoice_xml(z)

    def test_total_size_limit(self):
        """解压总大小超限 → ValueError（防 zip 炸弹）。"""
        big = "<发票>".encode() + b"A" * (50 * 1024 * 1024) + "</发票>".encode()
        z = _make_zip([("big.xml", big)])
        with self.assertRaises(ValueError):
            extract_invoice_xml(z)


if __name__ == "__main__":
    unittest.main()
