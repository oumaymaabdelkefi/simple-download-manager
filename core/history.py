"""
Persistence module: saves and loads download history using SQLite.
"""

import sqlite3
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass


DB_PATH = os.path.join(os.path.expanduser("~"), ".sdm", "history.db")
FALLBACK_DB_PATH = os.path.join(tempfile.gettempdir(), "sdm-history.db")
_DB_LOCK = threading.RLock()


@dataclass
class HistoryEntry:
    id: int
    url: str
    filename: str
    dest_path: str
    total_size: int
    downloaded_bytes: int
    status: str
    start_time: Optional[str]
    end_time: Optional[str]
    num_threads: int
    error: Optional[str]
    segments_json: Optional[str]


@dataclass
class QueueEntry:
    id: str
    url: str
    dest_dir: str
    filename: Optional[str]
    num_threads: int
    max_retries: int
    bandwidth_limit: Optional[int]
    scheduled_at: Optional[float]
    queued_at: float


def _get_conn() -> sqlite3.Connection:
    last_error = None
    for path in (DB_PATH, FALLBACK_DB_PATH):
        conn = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            conn = sqlite3.connect(path, timeout=30)
            conn.row_factory = sqlite3.Row
            _init_schema(conn)
            return conn
        except (OSError, sqlite3.Error) as exc:
            if conn is not None:
                conn.close()
            last_error = exc
    raise last_error or sqlite3.OperationalError("unable to open database file")


@contextmanager
def _connect():
    with _DB_LOCK:
        with _get_conn() as conn:
            yield conn


def init_db():
    with _connect() as conn:
        _init_schema(conn)


def _init_schema(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL,
            filename    TEXT,
            dest_path   TEXT,
            total_size  INTEGER DEFAULT 0,
            downloaded_bytes INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'pending',
            start_time  TEXT,
            end_time    TEXT,
            num_threads INTEGER DEFAULT 4,
            error       TEXT,
            segments_json TEXT
        )
    """)
    _ensure_column(conn, "downloads", "downloaded_bytes", "INTEGER DEFAULT 0")
    _ensure_column(conn, "downloads", "segments_json", "TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS download_queue (
            id              TEXT PRIMARY KEY,
            url             TEXT NOT NULL,
            dest_dir        TEXT NOT NULL,
            filename        TEXT,
            num_threads     INTEGER DEFAULT 4,
            max_retries     INTEGER DEFAULT 3,
            bandwidth_limit INTEGER,
            scheduled_at    REAL,
            queued_at       REAL NOT NULL
        )
    """)
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _segments_json(task) -> str:
    return json.dumps([
        {
            "index": segment.index,
            "start": segment.start,
            "end": segment.end,
            "downloaded": segment.downloaded,
            "status": segment.status.value,
            "retries": segment.retries,
        }
        for segment in getattr(task, "segments", [])
    ])


def save_download(task) -> int:
    """Insert or update a download record. Returns row id."""
    start = datetime.fromtimestamp(task.start_time).isoformat() if task.start_time else None
    end = datetime.fromtimestamp(task.end_time).isoformat() if task.end_time else None

    with _connect() as conn:
        cur = conn.execute("""
            INSERT INTO downloads (url, filename, dest_path, total_size, downloaded_bytes, status, start_time, end_time, num_threads, error, segments_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.url,
            task.filename,
            task.dest_path,
            task.total_size,
            task.downloaded_bytes,
            task.status.value,
            start,
            end,
            task.num_threads,
            task.error,
            _segments_json(task),
        ))
        conn.commit()
        return cur.lastrowid


def update_download(row_id: int, task):
    start = datetime.fromtimestamp(task.start_time).isoformat() if task.start_time else None
    end = datetime.fromtimestamp(task.end_time).isoformat() if task.end_time else None

    with _connect() as conn:
        conn.execute("""
            UPDATE downloads
            SET total_size=?, downloaded_bytes=?, status=?, start_time=?, end_time=?, error=?, segments_json=?
            WHERE id=?
        """, (task.total_size, task.downloaded_bytes, task.status.value, start, end, task.error, _segments_json(task), row_id))
        conn.commit()


def get_history(limit: int = 100) -> List[HistoryEntry]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [HistoryEntry(**dict(r)) for r in rows]


def delete_entry(row_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM downloads WHERE id=?", (row_id,))
        conn.commit()


def clear_history():
    with _connect() as conn:
        conn.execute("DELETE FROM downloads")
        conn.commit()


def save_queue_entry(entry: QueueEntry):
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO download_queue
            (id, url, dest_dir, filename, num_threads, max_retries, bandwidth_limit, scheduled_at, queued_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.id,
            entry.url,
            entry.dest_dir,
            entry.filename,
            entry.num_threads,
            entry.max_retries,
            entry.bandwidth_limit,
            entry.scheduled_at,
            entry.queued_at,
        ))
        conn.commit()


def get_queue_entries() -> List[QueueEntry]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM download_queue ORDER BY queued_at ASC"
        ).fetchall()
    return [QueueEntry(**dict(row)) for row in rows]


def delete_queue_entry(entry_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM download_queue WHERE id=?", (entry_id,))
        conn.commit()


def update_queue_order(entries: List[tuple[str, float]]):
    with _connect() as conn:
        conn.executemany(
            "UPDATE download_queue SET queued_at=? WHERE id=?",
            [(queued_at, entry_id) for entry_id, queued_at in entries],
        )
        conn.commit()


init_db()
