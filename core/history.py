"""
Persistence module: saves and loads download history using SQLite.
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass


DB_PATH = os.path.join(os.path.expanduser("~"), ".sdm", "history.db")


@dataclass
class HistoryEntry:
    id: int
    url: str
    filename: str
    dest_path: str
    total_size: int
    status: str
    start_time: Optional[str]
    end_time: Optional[str]
    num_threads: int
    error: Optional[str]


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
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL,
                filename    TEXT,
                dest_path   TEXT,
                total_size  INTEGER DEFAULT 0,
                status      TEXT DEFAULT 'pending',
                start_time  TEXT,
                end_time    TEXT,
                num_threads INTEGER DEFAULT 4,
                error       TEXT
            )
        """)
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


def save_download(task) -> int:
    """Insert or update a download record. Returns row id."""
    start = datetime.fromtimestamp(task.start_time).isoformat() if task.start_time else None
    end = datetime.fromtimestamp(task.end_time).isoformat() if task.end_time else None

    with _get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO downloads (url, filename, dest_path, total_size, status, start_time, end_time, num_threads, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.url,
            task.filename,
            task.dest_path,
            task.total_size,
            task.status.value,
            start,
            end,
            task.num_threads,
            task.error,
        ))
        conn.commit()
        return cur.lastrowid


def update_download(row_id: int, task):
    start = datetime.fromtimestamp(task.start_time).isoformat() if task.start_time else None
    end = datetime.fromtimestamp(task.end_time).isoformat() if task.end_time else None

    with _get_conn() as conn:
        conn.execute("""
            UPDATE downloads
            SET total_size=?, status=?, start_time=?, end_time=?, error=?
            WHERE id=?
        """, (task.total_size, task.status.value, start, end, task.error, row_id))
        conn.commit()


def get_history(limit: int = 100) -> List[HistoryEntry]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [HistoryEntry(**dict(r)) for r in rows]


def delete_entry(row_id: int):
    with _get_conn() as conn:
        conn.execute("DELETE FROM downloads WHERE id=?", (row_id,))
        conn.commit()


def clear_history():
    with _get_conn() as conn:
        conn.execute("DELETE FROM downloads")
        conn.commit()


def save_queue_entry(entry: QueueEntry):
    with _get_conn() as conn:
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
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM download_queue ORDER BY queued_at ASC"
        ).fetchall()
    return [QueueEntry(**dict(row)) for row in rows]


def delete_queue_entry(entry_id: str):
    with _get_conn() as conn:
        conn.execute("DELETE FROM download_queue WHERE id=?", (entry_id,))
        conn.commit()


def update_queue_order(entries: List[tuple[str, float]]):
    with _get_conn() as conn:
        conn.executemany(
            "UPDATE download_queue SET queued_at=? WHERE id=?",
            [(queued_at, entry_id) for entry_id, queued_at in entries],
        )
        conn.commit()


init_db()
