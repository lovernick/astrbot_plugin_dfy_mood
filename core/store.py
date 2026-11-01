"""SQLite 持久化：心情状态与情绪事件日志。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

try:
    from .state import MoodState
except ImportError:  # 兼容以顶层模块方式加载
    from state import MoodState


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mood_state (
    session_key TEXT PRIMARY KEY,
    valence REAL NOT NULL,
    arousal REAL NOT NULL,
    energy REAL NOT NULL,
    intimacy REAL NOT NULL,
    jealousy REAL NOT NULL DEFAULT 0,
    label TEXT NOT NULL DEFAULT '平静',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    last_interact_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mood_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    delta TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mood_event_session
    ON mood_event (session_key, ts DESC);

CREATE TABLE IF NOT EXISTS mood_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    ts REAL NOT NULL,
    valence REAL NOT NULL,
    arousal REAL NOT NULL,
    energy REAL NOT NULL,
    intimacy REAL NOT NULL,
    jealousy REAL NOT NULL,
    label TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_mood_snapshot_session
    ON mood_snapshot (session_key, ts);
"""


class MoodStore:
    """线程安全的简单存储，数据量很小，直接读写 SQLite。"""

    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._snapshot_writes = 0
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, session_key: str) -> MoodState:
        """取出状态；不存在时创建一份基线状态并落库。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT valence, arousal, energy, intimacy, jealousy, label,"
                " enabled, updated_at, last_interact_at"
                " FROM mood_state WHERE session_key = ?",
                (session_key,),
            ).fetchone()

        if row is not None:
            return MoodState(
                session_key=session_key,
                valence=row[0],
                arousal=row[1],
                energy=row[2],
                intimacy=row[3],
                jealousy=row[4],
                label=row[5],
                enabled=bool(row[6]),
                updated_at=row[7],
                last_interact_at=row[8],
            )

        state = MoodState(session_key=session_key)
        self.save(state)
        return state

    def save(self, state: MoodState) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO mood_state"
                " (session_key, valence, arousal, energy, intimacy, jealousy,"
                "  label, enabled, updated_at, last_interact_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    state.session_key,
                    state.valence,
                    state.arousal,
                    state.energy,
                    state.intimacy,
                    state.jealousy,
                    state.label,
                    int(state.enabled),
                    state.updated_at,
                    state.last_interact_at,
                ),
            )
            self._conn.commit()

    def log_event(self, session_key: str, event, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mood_event (session_key, ts, kind, reason, delta)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    session_key,
                    ts,
                    event.kind,
                    event.reason,
                    json.dumps(event.delta, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def recent_events(self, session_key: str, limit: int = 5) -> list[tuple]:
        """返回 [(ts, kind, reason, delta_text), ...]，按时间倒序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, reason, delta FROM mood_event"
                " WHERE session_key = ? ORDER BY ts DESC LIMIT ?",
                (session_key, max(1, limit)),
            ).fetchall()
        return rows

    # ------------------------------------------------------------ 界面快照查询

    def record_snapshot(
        self, state: MoodState, ts: float, trigger: str = "", retention_days: int = 90
    ) -> None:
        """写入一条时序快照，供 WebUI 画曲线；顺带按保留期清理旧数据。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO mood_snapshot"
                " (session_key, ts, valence, arousal, energy, intimacy, jealousy,"
                "  label, trigger)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    state.session_key,
                    ts,
                    state.valence,
                    state.arousal,
                    state.energy,
                    state.intimacy,
                    state.jealousy,
                    state.label,
                    trigger or "",
                ),
            )
            self._snapshot_writes += 1
            if self._snapshot_writes % 50 == 0 and retention_days > 0:
                cutoff = ts - retention_days * 86400
                self._conn.execute(
                    "DELETE FROM mood_snapshot WHERE ts < ?", (cutoff,)
                )
            self._conn.commit()

    def history(
        self, session_key: str, since: float, limit: int = 5000
    ) -> list[tuple]:
        """返回 [(ts, v, a, e, i, j, label), ...]，按时间升序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, valence, arousal, energy, intimacy, jealousy, label"
                " FROM mood_snapshot WHERE session_key = ? AND ts >= ?"
                " ORDER BY ts ASC LIMIT ?",
                (session_key, since, max(1, limit)),
            ).fetchall()
        return rows

    def sessions(self, since: float) -> list[dict]:
        """所有会话的当前状态 + 时间窗内的消息数。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.session_key, s.valence, s.arousal, s.energy, s.intimacy,"
                " s.jealousy, s.label, s.enabled, s.updated_at, s.last_interact_at,"
                " (SELECT COUNT(*) FROM mood_snapshot p"
                "   WHERE p.session_key = s.session_key AND p.ts >= ?)"
                " FROM mood_state s ORDER BY s.last_interact_at DESC",
                (since,),
            ).fetchall()
            reasons = self._conn.execute(
                "SELECT session_key, reason FROM mood_event"
                " WHERE id IN (SELECT MAX(id) FROM mood_event GROUP BY session_key)"
            ).fetchall()
        reason_map = {row[0]: row[1] for row in reasons}
        return [
            {
                "session_key": row[0],
                "valence": row[1],
                "arousal": row[2],
                "energy": row[3],
                "intimacy": row[4],
                "jealousy": row[5],
                "label": row[6],
                "enabled": bool(row[7]),
                "updated_at": row[8],
                "last_interact_at": row[9],
                "recent_messages": int(row[10]),
                "last_reason": reason_map.get(row[0], ""),
            }
            for row in rows
        ]

    def events(
        self, session_key: str, since: float, limit: int = 200
    ) -> list[tuple]:
        """返回 [(ts, kind, reason, delta_text), ...]，按时间倒序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, reason, delta FROM mood_event"
                " WHERE session_key = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
                (session_key, since, max(1, limit)),
            ).fetchall()
        return rows

    def stats(self, session_key: str, since: float) -> dict:
        """事件类型分布、按小时分布与心情标签占比。"""
        with self._lock:
            kinds = self._conn.execute(
                "SELECT kind, COUNT(*) FROM mood_event"
                " WHERE session_key = ? AND ts >= ? GROUP BY kind",
                (session_key, since),
            ).fetchall()
            hours = self._conn.execute(
                "SELECT CAST(strftime('%H', datetime(ts, 'unixepoch', '+8 hours'))"
                " AS INTEGER), COUNT(*) FROM mood_event"
                " WHERE session_key = ? AND ts >= ? GROUP BY 1",
                (session_key, since),
            ).fetchall()
            labels = self._conn.execute(
                "SELECT label, COUNT(*) FROM mood_snapshot"
                " WHERE session_key = ? AND ts >= ? GROUP BY label",
                (session_key, since),
            ).fetchall()
        return {
            "kinds": [{"kind": k, "count": int(c)} for k, c in kinds],
            "hours": [{"hour": int(h), "count": int(c)} for h, c in hours],
            "labels": [{"label": name, "count": int(c)} for name, c in labels],
        }

    def prune(self, older_than: float) -> int:
        """清理指定时间之前的快照与事件，返回删除行数。"""
        with self._lock:
            deleted = 0
            for table in ("mood_snapshot", "mood_event"):
                cursor = self._conn.execute(
                    f"DELETE FROM {table} WHERE ts < ?", (older_than,)
                )
                deleted += cursor.rowcount or 0
            self._conn.commit()
        return deleted

    def close(self) -> None:
        with self._lock:
            self._conn.close()
