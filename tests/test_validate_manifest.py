"""P0-5 manifest schema 校验回归：正常通过 + 篡改场景拦截。"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validate_manifest import validate_manifest

CORPUS = Path(__file__).resolve().parents[1] / "tests" / "corpus"


class TestValidateManifest(unittest.TestCase):
    def test_current_passes(self):
        errors = validate_manifest(CORPUS)
        self.assertEqual(errors, [], "当前语料库应通过校验：\n" + "\n".join(errors))

    def test_auto_sha_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
            manifest["samples"][0]["sha256"] = "AUTO"
            (td / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            # 复制首个样本文件
            (td / manifest["samples"][0]["file"]).write_bytes(
                (CORPUS / manifest["samples"][0]["file"]).read_bytes())
            errors = validate_manifest(td)
            self.assertTrue(any("AUTO" in e for e in errors), errors)

    def test_synthetic_marking_required(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
            synth = next(s for s in manifest["samples"] if s.get("synthetic"))
            del synth["construction_basis"]
            (td / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            for s in manifest["samples"]:
                src = CORPUS / s["file"]
                if src.exists():
                    (td / s["file"]).parent.mkdir(parents=True, exist_ok=True)
                    (td / s["file"]).write_bytes(src.read_bytes())
            errors = validate_manifest(td)
            self.assertTrue(any("construction_basis" in e for e in errors), errors)

    def test_sha_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
            (td / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            # 只复制一个样本并篡改内容
            f0 = manifest["samples"][0]["file"]
            (td / f0).parent.mkdir(parents=True, exist_ok=True)
            (td / f0).write_bytes(b"<tampered/>")
            errors = validate_manifest(td)
            self.assertTrue(any("SHA256 与磁盘不一致" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
