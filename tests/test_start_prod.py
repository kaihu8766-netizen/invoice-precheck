"""start_prod.py ensure_env 强制语义测试（RV-20261001-370 修复）。"""
import tempfile
import unittest
from pathlib import Path

import scripts.start_prod as sp


class TestEnsureEnv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        sp.SCRIPTS = Path(self._tmp.name)
        sp.ENV_FILE = Path(self._tmp.name) / ".env"

    def tearDown(self):
        self._tmp.cleanup()

    def test_generates_prod_defaults(self):
        env = sp.ensure_env()
        self.assertEqual(env["INVOICE_ENV"], "prod")
        self.assertEqual(env["INVOICE_LLM_ENABLED"], "0")
        self.assertEqual(env["INVOICE_LLM_SEND_AMOUNT"], "0")
        self.assertGreaterEqual(len(env["INVOICE_API_KEY"]), 16)
        self.assertTrue(sp.ENV_FILE.exists())

    def test_forces_prod_over_stale_dev(self):
        # RV-370：陈旧 .env 含 dev → 必须强制重置为 prod（静默 dev 语义是缺陷）
        sp.ENV_FILE.write_text("INVOICE_ENV=dev\nINVOICE_API_KEY=correct-horse-battery-staple-long\n",
                               encoding="utf-8")
        env = sp.ensure_env()
        self.assertEqual(env["INVOICE_ENV"], "prod")

    def test_forces_llm_off_over_stale_on(self):
        # RV-370：陈旧 LLM=1 → 强制 0（涉税数据不外发；要开必须事后显式改）
        sp.ENV_FILE.write_text("INVOICE_ENV=prod\nINVOICE_LLM_ENABLED=1\n"
                               "INVOICE_LLM_SEND_AMOUNT=1\nINVOICE_API_KEY=correct-horse-battery-ok\n",
                               encoding="utf-8")
        env = sp.ensure_env()
        self.assertEqual(env["INVOICE_LLM_ENABLED"], "0")
        self.assertEqual(env["INVOICE_LLM_SEND_AMOUNT"], "0")

    def test_keeps_valid_key(self):
        sp.ENV_FILE.write_text("INVOICE_ENV=prod\nINVOICE_API_KEY=keep-this-key-value-16chars\n",
                               encoding="utf-8")
        env = sp.ensure_env()
        self.assertEqual(env["INVOICE_API_KEY"], "keep-this-key-value-16chars")


if __name__ == "__main__":
    unittest.main()
