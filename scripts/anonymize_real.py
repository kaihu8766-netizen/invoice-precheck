"""真实发票脱敏（数据红线合规：替换式脱敏，原文件不进出仓库）。

用法：python scripts/anonymize_real.py [真实票目录] [输出JSON]
输出：脱敏映射 JSON（虚构发票号/企业名/税号 ↔ 真实结构字段），金额保留（规则验证用）。
     PDF 原文件不复制、不进 Git；脱敏 JSON 存 benchmark/private（.gitignore 永不入库）。
"""
import json
import random
import sys
from pathlib import Path

from app.pdf import parse_pdf

_COMPANY_SUFFIX = ["有限公司", "科技股份有限公司", "自动化有限公司", "物流有限公司", "模具工业有限公司"]
_PREFIX = ["示例", "演示", "测试"]


def fake_taxid(seed: str) -> str:
    random.seed(seed)
    # 91 + 6位区划 + 9位编码 + 校验位（18位，字母数字）
    body = "".join(random.choices("0123456789ABCDEFGHJKLMNPQRTUWXY", k=10))
    return f"91{random.choice('1234567890')}{random.choice('0123456789')}{body}"


def fake_no(seed: str) -> str:
    random.seed(seed)
    return "".join(random.choices("0123456789", k=20))


def fake_company(seed: str) -> str:
    random.seed(seed)
    return f"{random.choice(_PREFIX)}{random.choice(_COMPANY_SUFFIX)}"


def main():
    src_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("benchmark/private/real_invoices_anonymized.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(src_dir.glob("**/*.pdf")):
        try:
            inv = parse_pdf(p.read_bytes())
        except Exception:
            continue
        if not inv.invoice_no:
            continue
        rows.append({
            "source": p.parent.name[:40],          # 来源目录（供应商+金额，不落文件名全量）
            "invoice_no": inv.invoice_no,          # 真实号（本地脱敏映射保留）
            "issue_date": inv.issue_date,
            "amount": str(inv.amount), "tax": str(inv.tax), "total": str(inv.total),
            "buyer_name": inv.buyer_name, "seller_name": inv.seller_name,
            "buyer_taxid": inv.buyer_taxid, "seller_taxid": inv.seller_taxid,
            # 脱敏映射（替换式）：虚构值 ↔ 真实结构
            "anon": {
                "invoice_no": fake_no(inv.invoice_no),
                "buyer_name": fake_company(inv.invoice_no + "b"),
                "seller_name": fake_company(inv.invoice_no + "s"),
                "buyer_taxid": fake_taxid(inv.invoice_no + "bt"),
                "seller_taxid": fake_taxid(inv.invoice_no + "st"),
            },
        })
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"脱敏映射 {len(rows)} 条 → {out_path}")
    for r in rows[:3]:
        a = r["anon"]
        print(f"  {r['invoice_no'][:8]}... → {a['invoice_no']} | {r['seller_name'][:6]} → {a['seller_name']} | total={r['total']}")


if __name__ == "__main__":
    main()
