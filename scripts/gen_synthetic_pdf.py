"""合成数电票 PDF（合规虚构数据，可进 Git；数据红线：不得混入真实数据）。

实现：PyMuPDF 插入 CJK 字体生成文本型 PDF（reportlab 不支持 Noto CFF 轮廓，弃用）。
用法：python scripts/gen_synthetic_pdf.py [输出目录] [数量]
"""
import sys
from pathlib import Path

import fitz

CJK_FONT = "/usr/share/fonts/truetype/arphic-gbsn00lp/gbsn00lp.ttf"


def gen(path: Path, no: str, seller: str, amount: float, date: str):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_font(fontname="noto", fontfile=CJK_FONT)
    y = 100
    rows = [
        ("电子发票（数电票·合成样例）", 16),
        ("", 0),
        (f"发票号码: {no}", 12),
        (f"开票日期: {date}", 12),
        (f"购买方名称: 示例采购有限公司", 12),
        (f"购买方税号: 91310000MA1FKEXAMPLE1", 12),
        (f"销售方名称: {seller}", 12),
        (f"销售方税号: 91320000MA1FKEXAMPLE2", 12),
        (f"金额: {amount:.2f}", 12),
        (f"税额: {round(amount*0.13, 2):.2f}", 12),
        (f"价税合计（小写）: ￥{round(amount, 2) + round(amount*0.13, 2):.2f}", 12),
        ("备注: 合成测试数据，主体虚构，仅用于解析验证", 12),
    ]
    for text, size in rows:
        if text:
            page.insert_text((72, y), text, fontname="noto", fontsize=size)
            y += 26 if size == 12 else 36
    doc.subset_fonts()  # 字体子集化（只嵌用到的字形，控制文件体积）
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmark/synthetic")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    out.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        no = f"253000000000{90000000+i}"
        gen(out / f"synthetic_{i+1}.pdf", no, f"示例服务有限公司{i+1}", 100.0 + i*50.5, f"2026-08-{i+1:02d}")
    print(f"生成 {n} 张到 {out}")


if __name__ == "__main__":
    main()
