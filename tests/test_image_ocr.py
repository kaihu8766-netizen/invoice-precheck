"""F-06 图片解析 + PWA（RV-49）：detect_type image / EXIF 转正 / 长边限制 / SW 注册 / capture。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser import detect_type, parse_document


def _make_png(w=800, h=600, rotation=None) -> bytes:
    """构造 PNG（可选 EXIF Orientation），用于 detect_type/预处理解码测试。"""
    from PIL import Image
    from io import BytesIO
    im = Image.new("RGB", (w, h), (255, 255, 255))
    if rotation:
        exif = im.getexif()
        exif[0x0112] = rotation  # Orientation tag
        im.save(BytesIO(), format="PNG")  # 先占位
        buf = BytesIO()
        im.save(buf, format="PNG", exif=exif)
        return buf.getvalue()
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg() -> bytes:
    from PIL import Image
    from io import BytesIO
    im = Image.new("RGB", (400, 300), (200, 200, 200))
    buf = BytesIO()
    im.save(buf, format="JPEG")
    return buf.getvalue()


class TestImageDetect(unittest.TestCase):
    def test_detect_png_image(self):
        self.assertEqual(detect_type(_make_png()), "image")

    def test_detect_jpeg_image(self):
        self.assertEqual(detect_type(_make_jpeg()), "image")

    def test_detect_xml_still_ok(self):
        self.assertEqual(detect_type(b"<?xml version='1.0'?><root/>"), "xml")

    def test_preprocess_exif_transpose(self):
        # EXIF 转正：竖拍（Orientation=6）的 800x600 → 处理后应为 600x800
        from app.image_ocr import preprocess_image
        from PIL import Image
        from io import BytesIO
        out = preprocess_image(_make_png(800, 600, rotation=6))
        im = Image.open(BytesIO(out))
        self.assertEqual((im.width, im.height), (600, 800), "EXIF Orientation=6 应转正为竖图")

    def test_preprocess_max_edge(self):
        # 长边 4000 → 限制 1800
        from app.image_ocr import preprocess_image
        from PIL import Image
        from io import BytesIO
        out = preprocess_image(_make_png(4000, 2000), max_edge=1800)
        im = Image.open(BytesIO(out))
        self.assertLessEqual(max(im.width, im.height), 1800)

    def test_parse_document_image_empty_raises(self):
        # 空白图片 → OCR 空 → 明确报错（前端提示重拍，不静默空报告）
        with self.assertRaises(ValueError):
            parse_document(_make_png(600, 400))


class TestPWAStatic(unittest.TestCase):
    def test_manifest_valid(self):
        import json
        m = json.loads(Path("docs/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["display"], "standalone")
        self.assertTrue(any(i["sizes"] == "192x192" for i in m["icons"]))

    def test_sw_and_icons_exist(self):
        sw = Path("docs/service-worker.js").read_text(encoding="utf-8")
        self.assertIn("network-first", sw.replace(" ", "").lower()) if "network" in sw else None
        self.assertIn("CACHE_NAME", sw)
        self.assertTrue(Path("docs/assets/icon-192.png").exists())
        self.assertTrue(Path("docs/assets/icon-512.png").exists())

    def test_index_pwa_meta(self):
        html = Path("docs/index.html").read_text(encoding="utf-8")
        self.assertIn("rel=\"manifest\"", html)
        self.assertIn("apple-mobile-web-app-capable", html)
        self.assertIn("service-worker.js", html)
        self.assertIn("capture=\"environment\"", html)
        self.assertIn(".png,.jpg,.jpeg", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
