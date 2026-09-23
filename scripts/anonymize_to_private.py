#!/usr/bin/env python3
"""anonymize_to_private.py —— 真实发票 → 脱敏可重跑副本（评审 #10 采纳红线微调）。

流程：解析真实 PDF → 字段 → 假发票号/假公司/假税号（金额/日期保留）→ reportlab 合成
脱敏版 PDF → 存 ~/invoice-private/（受控保留，可重跑）→ 标注 JSON 存 benchmark/private
（.gitignore 永不入库）。原始文件由调用方删除（红线）。

用法：python3 scripts/anonymize_to_private.py <真实票目录> <发票号映射JSON>
"""
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.parser import parse_document  # noqa: E402


def fake_no(seed: str, year: str) -> str:
    random.seed(seed)
    # 20 位数电票号：年度2 + 区划2 + 渠道1 + 顺序15
    return f"{year[-2:]}{random.choice('11')}{random.choice('33')}{random.choice('0')}" + \
        "".join(random.choices("0123456789", k=15))


def fake_company(seed: str, role: str) -> str:
    random.seed(seed + role)
    return f"示例{random.choice(['华','宇','盛','恒','瑞'])}{role}有限公司"


def fake_taxid(seed: str) -> str:
    random.seed(seed)
    return f"91{random.choice('1234567890')}{random.choice('0123456789')}" + \
        "".join(random.choices("0123456789ABCDEFGHJKLMNPQRTUWXY", k=10))


def render_invoice_pdf(path: Path, fields: dict) -> None:
    """合成脱敏版数电票 PDF（fitz china-s 中文字体，标签+值结构可被解析器识别并勾稽）。"""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 pt
    y = 780
    page.insert_text((60, y), "电子发票（增值税电子发票）", fontname="china-s", fontsize=14); y -= 26
    page.insert_text((60, y), f"发票号码：{fields['invoice_no']}", fontname="china-s", fontsize=11)
    page.insert_text((300, y), f"开票日期：{fields['issue_date']}", fontname="china-s", fontsize=11); y -= 34
    page.insert_text((60, y), "购买方信息", fontname="china-s", fontsize=11); y -= 18
    page.insert_text((60, y), f"名称：{fields['buyer_name']}", fontname="china-s", fontsize=11)
    page.insert_text((300, y), f"统一社会信用代码/纳税人识别号：{fields['buyer_taxid']}", fontname="china-s", fontsize=11); y -= 30
    page.insert_text((60, y), "销售方信息", fontname="china-s", fontsize=11); y -= 18
    page.insert_text((60, y), f"名称：{fields['seller_name']}", fontname="china-s", fontsize=11)
    page.insert_text((300, y), f"统一社会信用代码/纳税人识别号：{fields['seller_taxid']}", fontname="china-s", fontsize=11); y -= 38
    page.insert_text((60, y), "项目名称            金额            税率/征收率            税额", fontname="china-s", fontsize=11); y -= 20
    for it in fields.get("items", [])[:4]:
        # 整行单次插入（含空格分隔列），避免多段插入文本流粘连（如 金额+0% 粘成 xx140%）
        page.insert_text((60, y), f"{it.get('name', '示例项目')}  {it.get('amount', '')}  {it.get('rate', '')}  {it.get('tax', '')}", fontname="china-s", fontsize=11)
        y -= 20
    y -= 24
    page.insert_text((60, y), f"合计金额：{fields['amount']}    合计税额：{fields['tax']}", fontname="china-s", fontsize=11); y -= 22
    page.insert_text((60, y), f"价税合计(小写)：¥{fields['total']}", fontname="china-s", fontsize=12)
    doc.save(str(path))
    doc.close()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python3 scripts/anonymize_to_private.py <真实票目录>")
        return 1
    src = Path(sys.argv[1])
    priv_dir = Path.home() / "invoice-private"
    priv_dir.mkdir(parents=True, exist_ok=True)
    out_json = ROOT / "benchmark" / "private" / "real_private_index.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for p in sorted(src.glob("*.pdf")):
        try:
            inv = parse_document(p.read_bytes())
        except Exception as e:
            print(f"  ! 解析失败 {p.name}: {type(e).__name__} {str(e)[:60]}")
            continue
        seed = p.name
        fields = {
            "file": p.name,
            "invoice_no": fake_no(seed, inv.issue_date[:4] if inv.issue_date else "2026"),
            "issue_date": inv.issue_date,
            "buyer_name": fake_company(seed, "买方"),
            "buyer_taxid": fake_taxid(seed + "b"),
            "seller_name": fake_company(seed, "销方"),
            "seller_taxid": fake_taxid(seed + "s"),
            "amount": f"{inv.amount:.2f}" if inv.amount else "0.00",
            "tax": f"{inv.tax:.2f}" if inv.tax else "0.00",
            "total": f"{inv.total:.2f}" if inv.total else "0.00",
            "items": [{"name": "示例商品", "amount": f"{inv.amount:.2f}",
                       "rate": "0%", "tax": "0.00"}],
        }
        target = priv_dir / f"anon_{inv.invoice_no}.pdf"
        render_invoice_pdf(target, fields)
        # 再解析副本验证可重跑
        try:
            chk = parse_document(target.read_bytes())
            ok = chk.total > 0 and chk.amount + chk.tax == chk.total
        except Exception as e:
            ok = False
            print(f"  ! 副本不可解析 {target.name}: {e}")
        records.append({
            "src_file": p.name, "copy": target.name, "copy_ok": ok,
            "truth": {k: v for k, v in fields.items() if k != "items"},
        })
        print(f"  ✓ {p.name} → {target.name}（勾稽验证 {ok}）")
    out_json.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n共 {len(records)} 张副本；标注索引：{out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
