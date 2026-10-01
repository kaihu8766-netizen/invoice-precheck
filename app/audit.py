"""企业级审计日志（T-014 前置③ / T-056 后端部分，RV-20261001-367）。

设计：
- SQLite 本地存储（A 案部署：数据不出机）；append-only：无 UPDATE/DELETE 入口，只 INSERT
- 哈希链防篡改：event_hash = sha256(prev_hash + ts + event_type + payload_json)，
  任一历史事件被改 → 其后所有 event_hash 校验失败（可检测篡改，无需可信第三方）
- 事件 payload 脱敏：不含明文 PII（票号/税号/手机/公司名/金额不进 payload；
  需要定位的信息用 event_id / 文件指纹 sha256 前 16 位）
- 删除权（PII）：DELETE 数据 → 文件本体删除 + 审计事件 data_deleted 保留（含被删对象指纹），
  "删除但审计不可销毁"——合规可溯源

事件类型（event_type）：
- file_uploaded  上传原始文件（payload: file_fp 指纹, size, name 脱敏）
- parsed         解析完成（payload: file_fp, ok, n_findings）
- finding_reviewed  复核决定落服务端（payload: finding_id, decision, note 截断；T-056 核心）
- config_changed 配置变更（payload: fields 清单, rules_hash）
- exported       导出（payload: kind, n_rows）
- data_deleted   数据删除权行使（payload: ref_fp, ref_kind）

校验接口：
- verify_chain() -> list[bad_index]：重算全部 event_hash，返回不匹配的事件索引（审计自检用）
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger("invoice-precheck.audit")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,        -- event_id (uuid4 hex)
    ts          TEXT NOT NULL,           -- ISO8601 UTC
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,           -- JSON 字符串（脱敏）
    prev_hash   TEXT NOT NULL,
    event_hash  TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type);
"""


class AuditStore:
    """append-only 审计存储（进程内单例；多进程部署需迁移 PostgreSQL，当前 A 案单进程足够）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI 异步端点可在多线程执行：check_same_thread=False + 写锁（append-only 单写者足够）
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- 内部 ----
    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT event_hash FROM audit_events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else "GENESIS"  # 链头

    def _insert(self, event_type: str, payload: dict) -> dict:
        with self._lock:
            prev = self._last_hash()
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            event_hash = hashlib.sha256(f"{prev}|{ts}|{event_type}|{payload_json}".encode()).hexdigest()
            event_id = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO audit_events (id, ts, event_type, payload, prev_hash, event_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, ts, event_type, payload_json, prev, event_hash),
            )
            self._conn.commit()
            return {"event_id": event_id, "ts": ts, "event_type": event_type,
                    "payload": payload, "prev_hash": prev, "event_hash": event_hash}

    # ---- 事件写入（唯一入口；payload 调用方负责脱敏）----
    def log(self, event_type: str, payload: dict) -> dict:
        if event_type not in {"file_uploaded", "parsed", "finding_reviewed",
                              "config_changed", "exported", "data_deleted"}:
            raise ValueError(f"未知审计事件类型: {event_type}")
        return self._insert(event_type, payload)

    # ---- 查询（只读）----
    def list_events(self, limit: int = 100, offset: int = 0,
                    event_type: str | None = None) -> list[dict]:
        if event_type:
            rows = self._conn.execute(
                "SELECT id, ts, event_type, payload, prev_hash, event_hash FROM audit_events "
                "WHERE event_type = ? ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (event_type, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, ts, event_type, payload, prev_hash, event_hash FROM audit_events "
                "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [{"event_id": r[0], "ts": r[1], "event_type": r[2],
                 "payload": json.loads(r[3]), "prev_hash": r[4], "event_hash": r[5]}
                for r in rows]

    def count(self, event_type: str | None = None) -> int:
        if event_type:
            return self._conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = ?", (event_type,)
            ).fetchone()[0]
        return self._conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    # ---- 完整性自检（审计自检/隔日任务用）----
    def verify_chain(self) -> tuple[bool, list[int]]:
        """重算全链 event_hash。返回 (是否完好, 损坏事件索引列表)。"""
        rows = self._conn.execute(
            "SELECT rowid, id, ts, event_type, payload, prev_hash, event_hash "
            "FROM audit_events ORDER BY rowid"
        ).fetchall()
        prev = "GENESIS"
        bad: list[int] = []
        for rowid, _eid, ts, etype, payload, p_hash, e_hash in rows:
            expected = hashlib.sha256(
                f"{prev}|{ts}|{etype}|{payload}".encode()
            ).hexdigest()
            if expected != e_hash or p_hash != prev:
                bad.append(rowid)
            prev = e_hash
        return (not bad, bad)

    def close(self):
        self._conn.close()
