"""audit.py 审计存储测试（T-014 前置③ / T-056）。"""
import json
import tempfile
import unittest
from pathlib import Path

from app.audit import AuditStore


class TestAuditStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "audit.db"
        self.store = AuditStore(self.db)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _ev(self):
        return self.store.log("file_uploaded", {"file_fp": "abc123", "size": 100, "name": "报销.pdf"})

    def test_append_only_and_hash_chain(self):
        e1 = self._ev()
        e2 = self.store.log("parsed", {"file_fp": "abc123", "ok": True, "n_findings": 3})
        e3 = self._ev()
        # 链头 genesis、prev_hash 正确衔接
        self.assertEqual(e1["prev_hash"], "GENESIS")
        self.assertEqual(e2["prev_hash"], e1["event_hash"])
        self.assertEqual(e3["prev_hash"], e2["event_hash"])
        self.assertEqual(self.store.count(), 3)
        ok, bad = self.store.verify_chain()
        self.assertTrue(ok)
        self.assertEqual(bad, [])

    def test_tamper_detected(self):
        e1 = self._ev()
        e2 = self.store.log("parsed", {"file_fp": "abc123", "ok": True, "n_findings": 1})
        # 篡改 e1 的 payload（改 size），e1 的 hash 不再匹配
        import sqlite3
        conn = sqlite3.connect(str(self.db))
        conn.execute(
            "UPDATE audit_events SET payload = ? WHERE id = ?",
            (json.dumps({"file_fp": "abc123", "size": 999, "name": "x.pdf"}, ensure_ascii=False),
             e1["event_id"]),
        )
        conn.commit()
        conn.close()
        ok, bad = self.store.verify_chain()
        self.assertFalse(ok)
        self.assertIn(1, bad)  # 第一行损坏

    def test_payload_redaction_by_caller(self):
        # 审计只存指纹不存明文 PII（调用方职责：payload 脱敏）
        e = self.store.log("finding_reviewed",
                           {"finding_id": "F-001", "decision": "reject", "note": "税额勾稽不符（截断）"})
        self.assertIn("F-001", json.dumps(e["payload"]))
        self.assertNotIn("发票号码明文", json.dumps(e["payload"]))

    def test_unknown_event_type_rejected(self):
        with self.assertRaises(ValueError):
            self.store.log("drop_table", {})

    def test_list_events_paging_and_filter(self):
        for i in range(5):
            self.store.log("exported", {"kind": "csv", "n_rows": i})
        self.store.log("data_deleted", {"ref_fp": "zzz", "ref_kind": "invoice"})
        all_ev = self.store.list_events(limit=10)
        self.assertEqual(len(all_ev), 6)
        # 倒序：最新在前
        self.assertEqual(all_ev[0]["event_type"], "data_deleted")
        filt = self.store.list_events(limit=10, event_type="exported")
        self.assertEqual(len(filt), 5)
        page = self.store.list_events(limit=2, offset=4)
        self.assertEqual(len(page), 2)

    def test_verify_empty_chain(self):
        ok, bad = self.store.verify_chain()
        self.assertTrue(ok)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
