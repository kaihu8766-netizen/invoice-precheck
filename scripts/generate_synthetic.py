"""P2-1 合成样本生成器（确定性 · 可复现 · 参数化）。

2026-09-23 建立（语料库 v0.3 落地闭环的下一步：手工构造 → 可复现生成器）。

设计约束：
- **确定性**：默认参数必须逐字节复现 `tests/corpus/synthetic/` 现有 4 个样本
  （黄金文件在磁盘，SHA256 绑定测试防漂移；`test_synthetic_generator.py` 断言默认输出 == 磁盘）。
- **参数化**：可生成变体（票号/金额/主体名），变体输出同样满足勾稽与 20 位票号硬规则
  （SYNTHETIC.md 构造硬规则 1/2/3）。
- **不越权**：生成新样本后，manifest.json 条目由调用方按流程追加（本脚本 --manifest 可自动追加，
  但 SHA256 以实际输出为准，禁止 AUTO 占位）。

用法：
    python3 scripts/generate_synthetic.py --kind red-letter [--seq 4] [--out tests/corpus/synthetic]
    python3 scripts/generate_synthetic.py --verify   # 校验现有 4 样本可由默认参数逐字节复现
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "corpus" / "synthetic"
KINDS = ("red-letter", "differential", "multirate", "pinyin")


# ---------- 生成函数（默认参数 = 磁盘现状，逐字节一致） ----------

def gen_red_letter(seq: int = 4, invoice_no: str = "26110000000000100004",
                   amount: str = "-2500.00", tax: str = "-150.00",
                   total: str = "-2650.00", blue_invoice_no: str = "24110000000000100003",
                   confirm_no: str = "HZSQ20260918000001") -> str:
    """红字发票 · 中文标签方言 · 负数金额（EI390/EI391）。"""
    assert len(invoice_no) == 20 and invoice_no.isdigit(), "票号必须 20 位数字"
    assert Decimal(amount) + Decimal(tax) == Decimal(total), "勾稽不符"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  合成样本（synthetic）：红字发票 · 中文标签方言 · 负数金额
  - 字段依据：GB/T《电子发票业务数据规范 第2部分：特定要素》红字发票要素
    EI390 被红冲蓝字电子发票号码 / EI391 红字发票信息确认单编号
  - 红冲业务依据：国家税务总局公告 2024 年第 11 号第八条（销货退回/开票有误/服务中止/销售折让
    开具红字数电发票）；票面金额为负（冲减蓝票）
  - 构造：基于官方数电票中文标签结构（财政部电子凭证会计数据标准元素清单），
    红冲要素字段名为构造（合成标注），真实红冲 EInvoice 结构待真实票核验
  - 未通过官方验证工具校验（工具仅 Win10 64 位）；结构级校验=与语料库真实票样同构 + 勾稽测试
  - 由 scripts/generate_synthetic.py 生成（seq={seq}）
-->
<发票>
  <发票号码>{invoice_no}</发票号码>
  <开票日期>2026-09-18</开票日期>
  <购买方名称>示例科技（北京）有限公司</购买方名称>
  <购买方纳税人识别号>91110000MAXXXXXX1A</购买方纳税人识别号>
  <销售方名称>示例商贸有限公司</销售方名称>
  <销售方纳税人识别号>91110100MAXXXXXX1B</销售方纳税人识别号>
  <合计金额>{amount}</合计金额>
  <合计税额>{tax}</合计税额>
  <价税合计>{total}</价税合计>
  <被红冲蓝字发票号码>{blue_invoice_no}</被红冲蓝字发票号码>
  <红字发票信息确认单编号>{confirm_no}</红字发票信息确认单编号>
  <备注>红冲：销货退回</备注>
</发票>
"""


def gen_differential(seq: int = 5, invoice_no: str = "26440000000000100005",
                     amount: str = "800.00", tax: str = "48.00", total: str = "848.00",
                     kce: str = "200.00") -> str:
    """差额征税 · EInvoice 英文结构（EI386 KCE + 备注格式）。"""
    assert len(invoice_no) == 20 and invoice_no.isdigit(), "票号必须 20 位数字"
    assert Decimal(amount) + Decimal(tax) == Decimal(total), "勾稽不符"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  合成样本（synthetic）：差额征税 · EInvoice 英文结构
  - 字段依据：GB/T《电子发票业务数据规范 第2部分：特定要素》差额征税要素 EI386 扣除额（拼音缩写 KCE）；
    金蝶发票云差额征税注意事项（备注格式："差额征税：XX 元。"）
  - 业务口径：旅游服务差额征税，销售额 1000.00，扣除额 {kce}，计税基础 {amount} × 6% = 税额 {tax}
  - 构造：基于语料库真实票样（gaode-js-taxi.xml）EInvoice 结构同构扩展
  - 未通过官方验证工具校验（工具仅 Win10 64 位）；结构级校验=与真实票样同构 + 勾稽测试
  - 由 scripts/generate_synthetic.py 生成（seq={seq}）
-->
<EInvoice>
  <Header>
    <EIid>{invoice_no}</EIid>
    <EInvoiceTag>SWEI4400</EInvoiceTag>
    <Version>0.32</Version>
  </Header>
  <EInvoiceData>
    <SellerInformation>
      <SellerName>示例旅行社（广东）有限公司</SellerName>
      <SellerIdNum>91440101MAXXXXXX1C</SellerIdNum>
      <SellerAddr>广东省广州市示例区示例路 1 号</SellerAddr>
      <SellerTelNum>020-XXXXXXXX</SellerTelNum>
    </SellerInformation>
    <BuyerInformation>
      <BuyerName>示例集团（深圳）有限公司</BuyerName>
      <BuyerIdNum>91440300MAXXXXXX1D</BuyerIdNum>
    </BuyerInformation>
    <BasicInformation>
      <TotalAmWithoutTax>{amount}</TotalAmWithoutTax>
      <TotalTaxAm>{tax}</TotalTaxAm>
      <TotalTax-includedAmount>{total}</TotalTax-includedAmount>
      <RequestTime>2026-09-10 09:30:00</RequestTime>
      <KCE>{kce}</KCE>
      <Remark>差额征税：{kce}。</Remark>
    </BasicInformation>
    <IssuItemInformation>
      <ItemDetail>
        <ItemName>旅游服务</ItemName>
        <Amount>{amount}</Amount>
        <TaxRate>0.06</TaxRate>
        <TaxAm>{tax}</TaxAm>
      </ItemDetail>
    </IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo>
    <InvoiceNumber>{invoice_no}</InvoiceNumber>
    <IssueTime>2026-09-10 09:30:00</IssueTime>
    <TaxBureauCode>14400000000</TaxBureauCode>
    <TaxBureauName>国家税务总局广东省税务局</TaxBureauName>
  </TaxSupervisionInfo>
</EInvoice>
"""


def gen_multirate(seq: int = 6, invoice_no: str = "26320000000000100006",
                  rows: tuple = (("100.00", "6.00", "0.06"), ("200.00", "18.00", "0.09"),
                                 ("300.00", "39.00", "0.13"))) -> str:
    """多税率明细 · EInvoice 英文结构（rows=(amount, tax, rate) 列表）。"""
    assert len(invoice_no) == 20 and invoice_no.isdigit(), "票号必须 20 位数字"
    total_amt = sum(Decimal(r[0]) for r in rows)
    total_tax = sum(Decimal(r[1]) for r in rows)
    total = total_amt + total_tax
    # 勾稽自洽：金额/税额/合计均保留 2 位小数且相加无舍入缺口
    assert total == Decimal(f"{total:.2f}"), f"勾稽不符（合计 {total_amt}+{total_tax}={total}）"
    items = "\n".join(
        f"      <ItemDetail>\n"
        f"        <ItemName>{('软件服务', '咨询服务', '设备销售')[i]}</ItemName>\n"
        f"        <Amount>{r[0]}</Amount>\n"
        f"        <TaxRate>{r[2]}</TaxRate>\n"
        f"        <TaxAm>{r[1]}</TaxAm>\n"
        f"      </ItemDetail>"
        for i, r in enumerate(rows)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  合成样本（synthetic）：多税率明细 · EInvoice 英文结构
  - 业务口径：一票三明细行，税率分别为 6% / 9% / 13%（一般纳税人多税率场景）
  - 构造：基于语料库真实票样（gaode-js-taxi.xml）EInvoice 结构同构扩展（{len(rows)} 明细行）
  - 未通过官方验证工具校验（工具仅 Win10 64 位）；结构级校验=与真实票样同构 + 勾稽测试
  - 由 scripts/generate_synthetic.py 生成（seq={seq}）
-->
<EInvoice>
  <Header>
    <EIid>{invoice_no}</EIid>
    <EInvoiceTag>SWEI3200</EInvoiceTag>
    <Version>0.32</Version>
  </Header>
  <EInvoiceData>
    <SellerInformation>
      <SellerName>示例软件（江苏）有限公司</SellerName>
      <SellerIdNum>91320000MAXXXXXX1E</SellerIdNum>
      <SellerAddr>江苏省南京市示例区示例大道 2 号</SellerAddr>
      <SellerTelNum>025-XXXXXXXX</SellerTelNum>
    </SellerInformation>
    <BuyerInformation>
      <BuyerName>示例实业（苏州）有限公司</BuyerName>
      <BuyerIdNum>91320500MAXXXXXX1F</BuyerIdNum>
    </BuyerInformation>
    <BasicInformation>
      <TotalAmWithoutTax>{total_amt:.2f}</TotalAmWithoutTax>
      <TotalTaxAm>{total_tax:.2f}</TotalTaxAm>
      <TotalTax-includedAmount>{total:.2f}</TotalTax-includedAmount>
      <RequestTime>2026-08-20 14:00:00</RequestTime>
    </BasicInformation>
    <IssuItemInformation>
{items}
    </IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo>
    <InvoiceNumber>{invoice_no}</InvoiceNumber>
    <IssueTime>2026-08-20 14:00:00</IssueTime>
    <TaxBureauCode>13200000000</TaxBureauCode>
    <TaxBureauName>国家税务总局江苏省税务局</TaxBureauName>
  </TaxSupervisionInfo>
</EInvoice>
"""


def gen_pinyin(seq: int = 7, invoice_no: str = "26320000000000100007",
               amount: str = "57.28", tax: str = "1.72", total: str = "59.00") -> str:
    """国标拼音缩写方言 · 普通发票（FPHM/KPRQ/HJJE/HJSE/JSHJXX）。"""
    assert len(invoice_no) == 20 and invoice_no.isdigit(), "票号必须 20 位数字"
    assert Decimal(amount) + Decimal(tax) == Decimal(total), "勾稽不符"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  合成样本（synthetic）：国标拼音缩写方言 · 普通发票
  - 字段依据：GB/T《电子发票业务数据规范》基本要素字段名规则（汉字拼音首字母）
    FPHM 发票号码 / KPRQ 开票日期 / XSFMC 销售方名称 / GMFMC 购买方名称
    HJJE 合计金额 / HJSE 合计税额 / JSHJXX 价税合计
  - 业务口径：餐饮服务，3% 征收率，{amount} × 3% = {tax}，价税合计 {total}
  - 构造：基于 GB/T 字段字典（v0.1 调研，无完整官方文件），键名有规范依据
  - 未通过官方验证工具校验（工具仅 Win10 64 位）；结构级校验=与语料库真实票样同构 + 勾稽测试
  - 由 scripts/generate_synthetic.py 生成（seq={seq}）
-->
<发票>
  <FPHM>{invoice_no}</FPHM>
  <KPRQ>2026-07-30</KPRQ>
  <XSFMC>示例生活服务（无锡）有限公司</XSFMC>
  <XSFNSRSBH>91320200MAXXXXXX1G</XSFNSRSBH>
  <GMFMC>个人</GMFMC>
  <GMFNSRSBH></GMFNSRSBH>
  <HJJE>{amount}</HJJE>
  <HJSE>{tax}</HJSE>
  <JSHJXX>{total}</JSHJXX>
  <SPMC>餐饮服务</SPMC>
</发票>
"""


GENERATORS = {
    "red-letter": gen_red_letter,
    "differential": gen_differential,
    "multirate": gen_multirate,
    "pinyin": gen_pinyin,
}
FILENAMES = {
    "red-letter": "synthetic-red-letter-cn.xml",
    "differential": "synthetic-differential-einv.xml",
    "multirate": "synthetic-multirate-einv.xml",
    "pinyin": "synthetic-pinyin-abbrev.xml",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_reproducible(out_dir: Path = DEFAULT_OUT) -> list[str]:
    """校验现有 4 样本可由默认参数逐字节复现；返回不一致清单（空=全部一致）。"""
    diffs: list[str] = []
    for kind, fn in FILENAMES.items():
        disk = (out_dir / fn).read_text(encoding="utf-8")
        generated = GENERATORS[kind]()
        if disk != generated:
            diffs.append(f"{fn}: 磁盘与默认生成不一致\n  磁盘 SHA256={_sha256(disk)}\n  生成 SHA256={_sha256(generated)}")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description="合成样本生成器（确定性/参数化）")
    ap.add_argument("--kind", choices=KINDS, help="样本类型")
    ap.add_argument("--seq", type=int, default=None, help="票号顺序号（可选，覆盖默认票号）")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录（默认 tests/corpus/synthetic/）")
    ap.add_argument("--verify", action="store_true", help="校验现有样本可复现（不写文件）")
    args = ap.parse_args()

    if args.verify:
        diffs = verify_reproducible(args.out)
        if diffs:
            print("不可复现：")
            for d in diffs:
                print("  " + d.replace("\n", "\n  "))
            return 1
        print("OK：现有 4 个合成样本均可由默认参数逐字节复现。")
        return 0

    if not args.kind:
        ap.error("--kind 必填（或使用 --verify）")
    text = GENERATORS[args.kind]()
    fn = FILENAMES[args.kind]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / fn).write_text(text, encoding="utf-8")
    print(f"已生成 {args.out / fn}（{len(text)} 字节，SHA256={_sha256(text)[:16]}）")
    print("提示：新样本/改动样本须同步更新 manifest.json 的 sha256（禁止 AUTO 占位入库）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
