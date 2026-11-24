"""SQLite persistence and the Item read model (SPEC 6)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
    reason TEXT, meeting_date TEXT, title TEXT, attendees TEXT
);
CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL REFERENCES files(path),
    line_no INTEGER NOT NULL, idx INTEGER NOT NULL, kind TEXT NOT NULL, checked INTEGER NOT NULL,
    owner_raw TEXT, group_flag INTEGER NOT NULL, task TEXT NOT NULL, due TEXT, due_raw TEXT
);
CREATE INDEX IF NOT EXISTS mentions_file ON mentions(file_path);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_kind TEXT NOT NULL, owner_name TEXT, owner_candidates TEXT,
    group_flag INTEGER NOT NULL, task TEXT NOT NULL, due TEXT, due_raw TEXT, notes_status TEXT NOT NULL,
    latest_mention_date TEXT NOT NULL, latest_source TEXT, latest_title TEXT, sources TEXT,
    mention_count INTEGER NOT NULL, manual_done_at TEXT, manual_done_date TEXT
);
CREATE TABLE IF NOT EXISTS item_keys (
    owner_key TEXT NOT NULL, norm_task TEXT NOT NULL, item_id INTEGER NOT NULL,
    PRIMARY KEY (owner_key, norm_task)
);
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, created_at TEXT NOT NULL, day TEXT NOT NULL,
    item_ids TEXT NOT NULL, sink TEXT NOT NULL, draft_ref TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass
class Item:
    id: int
    owner_kind: str  # person | ambiguous | unknown | unassigned
    owner_name: str | None
    owner_candidates: list[str]
    group: bool
    task: str
    due: date | None
    due_raw: str | None
    notes_status: str  # 'open' | 'done' (as written in notes)
    latest_mention_date: date
    latest_source: str | None
    latest_title: str | None
    sources: list[dict] = field(default_factory=list)
    mention_count: int = 1
    manual_done_at: str | None = None
    manual_done_date: date | None = None

    @property
    def owner_display(self) -> str:
        if self.owner_kind == "unassigned":
            return "(unassigned)"
        return self.owner_name or "(unassigned)"

    def is_done(self) -> bool:
        """Notes status, unless a manual `done` is newer than every note mention (SPEC 6.3)."""
        if self.manual_done_date is not None and self.latest_mention_date <= self.manual_done_date:
            return True
        return self.notes_status == "done"

    def status(self, today: date) -> str:
        if self.is_done():
            return "done"
        if self.due is not None and self.due < today:
            return "overdue"
        return "open"


def _item_from_row(r: sqlite3.Row) -> Item:
    return Item(
        id=r["id"], owner_kind=r["owner_kind"], owner_name=r["owner_name"],
        owner_candidates=json.loads(r["owner_candidates"] or "[]"), group=bool(r["group_flag"]),
        task=r["task"], due=_d(r["due"]), due_raw=r["due_raw"], notes_status=r["notes_status"],
        latest_mention_date=date.fromisoformat(r["latest_mention_date"]), latest_source=r["latest_source"],
        latest_title=r["latest_title"], sources=json.loads(r["sources"] or "[]"),
        mention_count=r["mention_count"], manual_done_at=r["manual_done_at"],
        manual_done_date=_d(r["manual_done_date"]),
    )


def get_items(conn: sqlite3.Connection) -> list[Item]:
    return [_item_from_row(r) for r in conn.execute("SELECT * FROM items ORDER BY id")]


def get_item(conn: sqlite3.Connection, item_id: int) -> Item | None:
    r = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _item_from_row(r) if r else None


def mark_done(conn: sqlite3.Connection, item_id: int, now_iso: str, day: date) -> None:
    conn.execute("UPDATE items SET manual_done_at = ?, manual_done_date = ? WHERE id = ?",
                 (now_iso, day.isoformat(), item_id))
    conn.commit()


def log_followup(conn: sqlite3.Connection, owner: str, now_iso: str, day: date,
                 item_ids: list[int], sink: str, draft_ref: str | None) -> None:
    conn.execute(
        "INSERT INTO followups(owner, created_at, day, item_ids, sink, draft_ref) VALUES (?,?,?,?,?,?)",
        (owner, now_iso, day.isoformat(), json.dumps(item_ids), sink, draft_ref))
    conn.commit()


def followed_up_on(conn: sqlite3.Connection, owner: str, day: date) -> bool:
    r = conn.execute("SELECT 1 FROM followups WHERE owner = ? AND day = ? LIMIT 1",
                     (owner, day.isoformat())).fetchone()
    return r is not None
