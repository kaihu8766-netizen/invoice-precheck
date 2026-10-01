"""审计 API 测试（T-014 前置③：review-decision / events / verify / 删除权）。"""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

# 隔离审计 DB（避免污染真实 data/audit.db）
import app.main as m

_tmp = tempfile.TemporaryDirectory()
m.AUDIT_DB = Path(_tmp.name) / "audit.db"
m.AUDIT = m.AuditStore(m.AUDIT_DB)

from app.main import app

_client = TestClient(app)
_KEY = "dev-invoice-precheck-key"


class TestAuditApi(unittest.TestCase):
    def setUp(self):
        # 每次测试重建独立审计库（删旧文件防事件累积）
        db = Path(_tmp.name) / "audit.db"
        if db.exists():
            db.unlink()
        m.AUDIT = m.AuditStore(db)

    def _auth(self, **kw):
        kw.setdefault("headers", {"X-API-Key": _KEY})
        return kw

    def test_review_decision_writes_chain(self):
        r = _client.post("/api/audit/review-decision",
                         json={"finding_id": "F-001", "decision": "reject", "note": "税额勾稽不符"},
                         headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["event_hash"])
        v = _client.get("/api/audit/verify", headers={"X-API-Key": _KEY})
        self.assertTrue(v.json()["ok"])
        ev = _client.get("/api/audit/events", headers={"X-API-Key": _KEY}).json()
        self.assertEqual(ev["total"], 1)
        self.assertEqual(ev["events"][0]["event_type"], "finding_reviewed")

    def test_decision_validation(self):
        r = _client.post("/api/audit/review-decision",
                         json={"finding_id": "F-2", "decision": "approve"},
                         headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 400)

    def test_note_truncated(self):
        r = _client.post("/api/audit/review-decision",
                         json={"finding_id": "F-3", "decision": "pass", "note": "x" * 500},
                         headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        ev = _client.get("/api/audit/events", headers={"X-API-Key": _KEY}).json()["events"][0]
        self.assertLessEqual(len(ev["payload"]["note"]), 200)

    def test_events_require_auth(self):
        r = _client.get("/api/audit/events")
        self.assertEqual(r.status_code, 401)

    def test_delete_right_leaves_audit(self):
        r = _client.delete("/api/data/abc123", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        ev = _client.get("/api/audit/events", headers={"X-API-Key": _KEY}).json()["events"][0]
        self.assertEqual(ev["event_type"], "data_deleted")
        self.assertEqual(ev["payload"]["ref_fp"], "abc123")

    def test_parse_audits_failure(self):
        # 坏文件 → parsed ok=False 事件
        r = _client.post("/parse", files={"file": ("bad.pdf", b"\x25\x50\x44\x46 garbage", "application/pdf")},
                         headers={"X-API-Key": _KEY})
        self.assertIn(r.status_code, (200, 400, 422))
        evs = _client.get("/api/audit/events", headers={"X-API-Key": _KEY}).json()["events"]
        types = {e["event_type"] for e in evs}
        self.assertIn("parsed", types)

    def test_export_audited(self):
        r = _client.post("/api/audit/export", json={"kind": "csv", "n_rows": 12},
                         headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        evs = _client.get("/api/audit/events", headers={"X-API-Key": _KEY}).json()["events"]
        self.assertEqual(evs[0]["event_type"], "exported")
        self.assertEqual(evs[0]["payload"]["n_rows"], 12)

    def test_verify_returns_tail_hash(self):
        _client.post("/api/audit/review-decision", json={"finding_id": "F-9", "decision": "pass", "note": ""},
                     headers={"X-API-Key": _KEY})
        v = _client.get("/api/audit/verify", headers={"X-API-Key": _KEY}).json()
        self.assertTrue(v["ok"])
        self.assertNotEqual(v["tail_hash"], "GENESIS")
        self.assertFalse(v["tail_is_genesis"])
        self.assertGreaterEqual(v["total"], 1)

    def test_config_change_audited(self):
        # mock save_config 防污染真实 data/config.json；恢复内存配置
        import unittest.mock as mock
        import app.main as m
        orig = m._APP_CONFIG
        try:
            with mock.patch("app.main.save_config"):
                r = _client.put("/api/config",
                                json={"company_name": "测试企业", "tax_id": "91330000MA1ABCDEFG"},
                                headers={"X-API-Key": _KEY})
        finally:
            m._APP_CONFIG = orig
        self.assertEqual(r.status_code, 200)
        evs = _client.get("/api/audit/events", headers={"X-API-Key": _KEY}).json()["events"]
        self.assertEqual(evs[0]["event_type"], "config_changed")
        self.assertEqual(evs[0]["payload"]["rules_changed"], False)


if __name__ == "__main__":
    unittest.main()
