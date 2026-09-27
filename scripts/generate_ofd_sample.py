#!/usr/bin/env python3
"""生成合成 OFD 容器样本（A1 2026-09-23）：GB/T 33190 骨架 zip + 内嵌发票 XML。

用途：为 OFD 解包（app/ofd.py）提供可验收的语料样本。
构造口径（诚实声明）：
- 内嵌发票 XML = 语料库官方公开票样（official-gd-special.xml，已脱敏）——票面数据真实官方样例；
- OFD 容器骨架 = GB/T 33190-2016 最小结构（OFD.xml 入口描述 + Doc_0/Document.xml 版式占位 +
  发票.xml 内嵌数据）；真实数电票 OFD 内部布局（内嵌 XML 命名/位置/多重数据文件）待真实 OFD 核验；
- 输出 tests/corpus/ofd/ofd-container-gd-sample.ofd（zip 容器）。

用法：python3 scripts/generate_ofd_sample.py [--output 路径] [--verify]
--verify：解包自检（提取内嵌 XML 后走 parse_document 断言字段）。
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
SRC_XML = ROOT / "tests" / "corpus" / "official-gd-special.xml"
DEFAULT_OUT = ROOT / "tests" / "corpus" / "ofd" / "ofd-container-gd-sample.ofd"

OFD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OFD xmlns="http://www.ofdspec.org/2016" Version="1.0">
  <DocBody>
    <DocInfo>
      <DocID>OFD-INVOICE-SAMPLE-20260923</DocID>
      <Title>数电票合成容器（官方票样封装）</Title>
    </DocInfo>
    <DocRoot>Doc_0/Document.xml</DocRoot>
  </DocBody>
</OFD>
"""

DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Document xmlns:ofd="http://www.ofdspec.org/2016" DocType="Invoice">
  <ofd:Pages>
    <ofd:Page ID="P1" BaseLoc="Pages/Page_1/Content.xml"/>
  </ofd:Pages>
</ofd:Document>
"""


def build_ofd(invoice_xml: bytes) -> bytes:
    """构造 GB/T 33190 骨架 + 内嵌发票 XML 的 OFD zip（确定性：固定条目时间戳，逐字节可复现）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in (
            ("OFD.xml", OFD_XML),
            ("Doc_0/Document.xml", DOCUMENT_XML),
            ("发票.xml", invoice_xml),  # 内嵌数电票 XML（命名模拟常见形态）
        ):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, content)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description="生成合成 OFD 容器样本")
    ap.add_argument("--output", default=str(DEFAULT_OUT))
    ap.add_argument("--verify", action="store_true", help="生成后自检（解包+解析）")
    args = ap.parse_args()

    src = SRC_XML.read_bytes()
    ofd_bytes = build_ofd(src)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ofd_bytes)
    print(f"OFD 样本已生成：{out}（{len(ofd_bytes)} bytes）")

    if args.verify:
        from app.ofd import extract_invoice_xml
        from app.parser import parse_document

        inner = extract_invoice_xml(ofd_bytes)
        assert inner == src, "内嵌 XML 与源票样不一致"
        inv = parse_document(ofd_bytes)
        assert inv.invoice_no == "23440000000000100001", f"票号不符：{inv.invoice_no}"
        assert str(inv.total) == "2.12", f"合计不符：{inv.total}"
        print("自检通过：解包→解析→字段断言 OK")


if __name__ == "__main__":
    main()
