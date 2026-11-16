"""SQLite storage. One file (data/clipper.db) holds campaigns, videos and clips."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    title TEXT, brand TEXT, category TEXT, type TEXT,
    platforms TEXT,                 -- JSON list
    rate_per_1k REAL,               -- dollars, from the board
    budget_total REAL, budget_available REAL,
    requires_application INTEGER,
    description TEXT,
    launched_at_ms INTEGER,
    detail TEXT,                    -- JSON from the campaign page (payoutModel etc.)
    matched_keywords TEXT,          -- JSON list
    first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE, video_id TEXT, title TEXT, duration REAL,
    campaign TEXT,                  -- campaigns/<slug>.yaml
    path TEXT,
    status TEXT DEFAULT 'new',      -- new, downloaded, transcribed, clipped, failed
    error TEXT,
    meta TEXT,                      -- JSON
    created_at REAL
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    campaign TEXT,
    start REAL, end REAL,
    score REAL,
    hook TEXT, reason TEXT,
    caption TEXT, pinned_comment TEXT,
    path TEXT,
    status TEXT DEFAULT 'pending',  -- pending, approved, rejected, sent, posted
    rule_report TEXT,               -- JSON list of {rule, ok, detail}
    signals TEXT,                   -- JSON: which signals voted for this clip
    spoiler INTEGER DEFAULT 0,
    created_at REAL, approved_at REAL, submit_by REAL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_settings().db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> float:
    return time.time()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    return json.loads(text) if text else default
