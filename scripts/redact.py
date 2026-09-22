"""P0-3 脱敏流水线脚本化（DeepSeek 评审 required_for_v1）。

2026-09-23 建立：把"官方实例脱敏 / 社区票样二次脱敏"从一次性手工操作升级为可复现流水线。

设计约束（沿用语料库脱敏纪律）：
- **税号/统一社会信用代码打码**：18 位代码保留前 4 后 2（结构不破坏，可识别一致性）；
- **发票号码替换**：20 位票号可替换（--invoice-no-map 或自动打码），防票号关联真实主体；
- **签名/证书清理**：SignatureValue 清空、X509Certificate 整节点删除、可整体剥离签名子树（--strip-signature）；
- **名称替换**：--replace "旧名=新名" 可多次，或 --replace-file 读映射表（每行 旧名=新名）；
- **电话/账号**：手机号（1[3-9]\d{9}）自动打码；银行账号等由 --replace 覆盖；
- **幂等**：对已脱敏输入重复执行不破坏结构（打码后不再命中原模式）。

用法：
    python3 scripts/redact.py --input path/to/raw.xml --out path/to/redacted.xml \\
        --replace "中山市奕博照明有限公司=示例照明（广东）有限公司" --strip-signature
    python3 scripts/redact.py --input raw.xml --out out.xml --invoice-no-map "22442000000921291350=23440000000000100011"
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

# 18 位统一社会信用代码 / 税号（大写字母+数字；校验位随意，保守打码）
_TAXID_RE = re.compile(r"\b[0-9A-Z]{18}\b")
# 20 位纯数字（数电票号）
_INVNO_RE = re.compile(r"\b\d{20}\b")
# 大陆手机号
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 签名相关节点
_SIGNATURE_VALUE_RE = re.compile(r"(<SignatureValue[^>]*>).*?(</SignatureValue>)", re.S)
_X509_RE = re.compile(r"<X509Certificate[^>]*>.*?</X509Certificate>", re.S)
_SIGNATURE_BLOCK_RE = re.compile(r"<Signature\b[^>]*>.*?</Signature>", re.S)


def _mask_taxid(m: re.Match) -> str:
    s = m.group(0)
    return s[:4] + "****" + s[-2:]


def _mask_mobile(m: re.Match) -> str:
    s = m.group(0)
    return s[:3] + "****" + s[-4:]


def _mask_invno(m: re.Match, map_: dict[str, str]) -> str:
    s = m.group(0)
    # 幂等：映射后的新票号（map 值）不再次打码；映射表命中则替换
    if s in map_.values():
        return s
    return map_.get(s, s[:3] + "****" + s[-4:])


def redact(text: str, replacements: dict[str, str] | None = None,
           invoice_no_map: dict[str, str] | None = None,
           mask_taxids: bool = True, mask_mobiles: bool = True,
           strip_signature: bool = False) -> str:
    """执行脱敏；返回脱敏后的 XML 文本。"""
    out = text
    if mask_taxids:
        out = _TAXID_RE.sub(_mask_taxid, out)
    if mask_mobiles:
        out = _MOBILE_RE.sub(_mask_mobile, out)
    if invoice_no_map is not None:
        out = _INVNO_RE.sub(lambda m: _mask_invno(m, invoice_no_map), out)
    for k, v in (replacements or {}).items():
        out = out.replace(k, v)
    if strip_signature:
        out = _SIGNATURE_BLOCK_RE.sub("<!-- signature stripped by redact.py -->", out)
    else:
        out = _SIGNATURE_VALUE_RE.sub(lambda m: m.group(1) + "<!-- signature value cleared -->" + m.group(2), out)
        out = _X509_RE.sub("", out)
    return out


def _parse_map(items: list[str] | None, file_path: Path | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if items:
        for it in items:
            k, _, v = it.partition("=")
            if not k or not _:
                raise SystemExit(f"非法替换映射（需 旧名=新名）：{it}")
            mapping[k] = v
    if file_path:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            if k and _:
                mapping[k.strip()] = v.strip()
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser(description="数电票 XML 脱敏流水线")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--replace", action="append", default=None, help="名称替换 旧=新（可多次）")
    ap.add_argument("--replace-file", type=Path, default=None, help="替换映射文件（每行 旧=新）")
    ap.add_argument("--invoice-no-map", action="append", default=None, help="票号替换 旧=新（可多次）")
    ap.add_argument("--no-mask-taxids", action="store_true", help="不自动打码 18 位税号")
    ap.add_argument("--no-mask-mobiles", action="store_true", help="不自动打码手机号")
    ap.add_argument("--strip-signature", action="store_true", help="整体剥离签名子树（默认只清 SignatureValue/X509）")
    ap.add_argument("--print-hint", action="store_true", help="打印建议的 manifest 条目草稿（sha256 已算）")
    args = ap.parse_args()

    raw = args.input.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    replacements = _parse_map(args.replace, args.replace_file)
    inv_map = _parse_map(args.invoice_no_map, None)

    out = redact(text, replacements=replacements, invoice_no_map=inv_map,
                 mask_taxids=not args.no_mask_taxids,
                 mask_mobiles=not args.no_mask_mobiles,
                 strip_signature=args.strip_signature)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(out, encoding="utf-8")
    sha = hashlib.sha256(out.encode("utf-8")).hexdigest()
    print(f"已脱敏：{args.input} → {args.out}（{len(out)} 字节，SHA256={sha[:16]}）")

    if args.print_hint:
        print("建议 manifest 条目：")
        print(f'  "file": "{args.out.name}", "sha256": "{sha}",')
        print('  "source_tier": "<official_public_sample|public_blog_secondary_redacted|community_public_secondary_redacted>",')
    return 0


if __name__ == "__main__":
    sys.exit(main())
