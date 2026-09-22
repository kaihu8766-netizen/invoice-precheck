"""P0-5 manifest schema 校验（DeepSeek 评审 required_for_v1；CI 用）。

校验 tests/corpus/manifest.json 的结构完整性与数据一致性：
1. 元结构：corpus_version / governance（m1_status / coverage_matrix / required_for_v1）存在；
2. 样本条目：必填字段齐全（file/sha256/province/issuing_system/version/invoice_type/scenario/
   dialect/features/source_tier/authority/license/redistribution/verification）；
3. 合成样本强制标注：synthetic=true → source_tier=synthetic_schema_based + construction_basis；
4. XBRL 归档样本：format=xbrl_instance → verification=official_sample；
4b. OFD 容器样本：format=ofd_container → synthetic=true + source_tier=synthetic_schema_based
    + construction_basis（A1 合成容器：内嵌官方票样，容器骨架为构造）；
5. SHA256：禁止 AUTO 占位，且与磁盘文件匹配；
6. 集合一致性：manifest 文件清单 == 目录实际 XML 文件（含 synthetic/ 子目录）；
7. 文件名唯一性。

用法：python3 scripts/validate_manifest.py [--corpus tests/corpus]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "tests" / "corpus"

REQUIRED_SAMPLE_FIELDS = (
    "file", "sha256", "province", "issuing_system", "version", "invoice_type",
    "scenario", "dialect", "features", "source", "masking",
    "source_tier", "authority", "license", "redistribution", "verification",
)
ALLOWED_FORMATS = ("invoice_xml", "xbrl_instance", "ofd_container")


def validate_manifest(corpus_dir: Path) -> list[str]:
    """返回问题清单（空=通过）。"""
    errors: list[str] = []
    mf_path = corpus_dir / "manifest.json"
    if not mf_path.exists():
        return [f"manifest.json 缺失：{mf_path}"]

    try:
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"manifest.json 不是合法 JSON：{e}"]

    if not isinstance(manifest.get("corpus_version"), str) or not manifest["corpus_version"].startswith("v"):
        errors.append("corpus_version 缺失或格式错误（应为 vX.Y）")

    g = manifest.get("governance", {})
    for key in ("m1_status", "coverage_matrix", "required_for_v1"):
        if key not in g:
            errors.append(f"governance.{key} 缺失")

    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        return errors + ["samples 缺失或为空"]

    seen: set[str] = set()
    for s in samples:
        if not isinstance(s, dict):
            errors.append("samples 中存在非对象条目")
            continue
        fname = s.get("file")
        if not isinstance(fname, str) or not fname:
            errors.append("samples 条目缺 file")
            continue
        if fname in seen:
            errors.append(f"file 重复：{fname}")
        seen.add(fname)
        for k in REQUIRED_SAMPLE_FIELDS:
            if k not in s:
                errors.append(f"{fname}: 缺必填字段 {k}")
        fmt = s.get("format", "invoice_xml")
        if fmt not in ALLOWED_FORMATS:
            errors.append(f"{fname}: format 非法 {fmt!r}（允许 {ALLOWED_FORMATS}）")
        if s.get("synthetic"):
            if s.get("source_tier") != "synthetic_schema_based":
                errors.append(f"{fname}: 合成样本 source_tier 必须为 synthetic_schema_based")
            if not s.get("construction_basis"):
                errors.append(f"{fname}: 合成样本缺 construction_basis")
        if fmt == "xbrl_instance" and s.get("verification") != "official_sample":
            errors.append(f"{fname}: XBRL 归档样本 verification 应为 official_sample")
        sha = s.get("sha256")
        if sha == "AUTO":
            errors.append(f"{fname}: sha256 为 AUTO 占位（禁止入库）")
        elif not (isinstance(sha, str) and len(sha) == 64):
            errors.append(f"{fname}: sha256 格式非法")
        else:
            fp = corpus_dir / fname
            if not fp.exists():
                errors.append(f"{fname}: 文件不存在")
            else:
                actual = hashlib.sha256(fp.read_bytes()).hexdigest()
                if actual != sha:
                    errors.append(f"{fname}: SHA256 与磁盘不一致（文件被改动？）")

    # 集合一致性：manifest 清单 == 磁盘文件（XML + OFD 容器，递归含 synthetic/）
    on_disk = sorted(
        str(p.relative_to(corpus_dir)).replace("\\", "/")
        for p in list(corpus_dir.rglob("*.xml")) + list(corpus_dir.rglob("*.ofd"))
    )
    in_manifest = sorted(seen)
    if in_manifest != on_disk:
        errors.append(f"文件集合不一致：manifest 缺 {set(on_disk) - set(in_manifest)}，"
                      f"磁盘多余 {set(in_manifest) - set(on_disk)}")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="manifest schema 校验")
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    args = ap.parse_args()
    errors = validate_manifest(args.corpus)
    if errors:
        print("校验失败：")
        for e in errors:
            print("  - " + e)
        return 1
    print("OK：manifest.json 结构与数据一致性校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
