"""Ingest: parse a folder of notes, then rebuild merged items from all stored mentions (SPEC 4.1, 6)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .dedupe import MERGE_THRESHOLD, NEAR_MISS_THRESHOLD, norm_task, similarity, tokens
from .parser import NoteSkipped, parse_note_file
from .people import Resolution, Roster, norm, resolve_owner


@dataclass
class IngestReport:
    folder: str
    examined: int = 0
    parsed: int = 0
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    merges: list[str] = field(default_factory=list)
    possible_duplicates: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    items_total: int = 0
    items_new: int = 0
    unassigned: int = 0
    mentions: int = 0

    def render(self) -> str:
        out = [f"Ingest {self.folder}",
               f"Files: {self.examined} .md examined, {self.parsed} parsed ({self.unchanged} unchanged), "
               f"{len(self.skipped)} skipped"]
        if self.skipped:
            out.append("Skipped (malformed):")
            out += [f"  - {n}: {r}" for n, r in self.skipped]
        out.append(f"Items: {self.items_total} total, {self.items_new} new "
                   f"({self.mentions} action lines read, {self.unassigned} unassigned)")
        for title, rows in (("Merged duplicates", self.merges), ("Possible duplicates (not merged)",
                            self.possible_duplicates), ("Ambiguous owners (never guessed; excluded from followup)",
                            self.ambiguous), ("Warnings", self.warnings)):
            if rows:
                out.append(f"{title}: {len(rows)}")
                out += [f"  - {r}" for r in rows]
        return "\n".join(out)


@dataclass
class _Cluster:
    owner_key: str
    res: Resolution | None  # None = unassigned
    mentions: list[dict] = field(default_factory=list)
    variants: list[frozenset] = field(default_factory=list)


def _owner_key(res: Resolution | None) -> str:
    if res is None:
        return "unassigned"
    if res.kind == "person":
        return "person:" + norm(res.name)
    if res.kind == "ambiguous":
        return "ambiguous:" + norm(res.name) + ":" + "|".join(sorted(norm(c) for c in res.candidates))
    return "unknown:" + norm(res.name)


def _list_notes(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".md"),
                  key=lambda p: p.name.lower())


def ingest_folder(conn: sqlite3.Connection, folder: str | Path, roster: Roster) -> IngestReport:
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"not a folder: {folder}")
    report = IngestReport(str(folder))
    report.warnings += roster.warnings
    unknown_attendees: set[str] = set()

    for path in _list_notes(folder):
        report.examined += 1
        key = str(path.resolve())
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        prev = conn.execute("SELECT sha256, status FROM files WHERE path = ?", (key,)).fetchone()
        try:
            note = parse_note_file(path)
        except NoteSkipped as exc:
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - a bad note must never crash the run
            reason = f"parse error: {type(exc).__name__}: {exc}"
        else:
            reason = ""
        if reason:
            report.skipped.append((path.name, reason))
            conn.execute("DELETE FROM mentions WHERE file_path = ?", (key,))
            conn.execute("INSERT OR REPLACE INTO files(path, name, sha256, status, reason) VALUES (?,?,?,?,?)",
                         (key, path.name, sha, "skipped", reason))
            continue

        report.parsed += 1
        if prev and prev["sha256"] == sha and prev["status"] == "parsed":
            report.unchanged += 1
        conn.execute("DELETE FROM mentions WHERE file_path = ?", (key,))
        conn.execute(
            "INSERT OR REPLACE INTO files(path, name, sha256, status, reason, meeting_date, title, attendees) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (key, path.name, sha, "parsed", None, note.date.isoformat(), note.title, json.dumps(note.attendees)))
        for m in note.mentions:
            conn.execute(
                "INSERT INTO mentions(file_path, line_no, idx, kind, checked, owner_raw, group_flag, task, due, due_raw) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (key, m.line, m.idx, m.kind, int(m.checked), m.owner_raw, int(m.group), m.task,
                 m.due.isoformat() if m.due else None, m.due_raw))
        for w in note.warnings:
            report.warnings.append(f"{path.name}: {w}")
        for a in note.attendees:
            if roster.people and not any(p.matches_text(a) for p in roster.people):
                unknown_attendees.add(a)
    if unknown_attendees:
        report.warnings.append("attendees not in roster: " + ", ".join(sorted(unknown_attendees)))
    conn.commit()

    _rebuild_items(conn, roster, report)
    return report


def _rebuild_items(conn: sqlite3.Connection, roster: Roster, report: IngestReport) -> None:
    rows = conn.execute(
        "SELECT m.*, f.name AS fname, f.title, f.meeting_date, f.attendees FROM mentions m "
        "JOIN files f ON f.path = m.file_path WHERE f.status = 'parsed' "
        "ORDER BY f.meeting_date, lower(f.name), m.line_no, m.idx").fetchall()
    report.mentions = len(rows)

    clusters: list[_Cluster] = []
    by_owner: dict[str, list[_Cluster]] = {}
    for r in rows:
        toks = tokens(r["task"])
        if not toks:
            report.warnings.append(f"{r['fname']}:{r['line_no']}: empty task, ignored")
            continue
        res = None
        if r["owner_raw"]:
            res = resolve_owner(r["owner_raw"], roster, json.loads(r["attendees"] or "[]"))
            if res.kind == "unknown" and roster.people:
                msg = f"owner {res.name!r} is not in the roster"
                if msg not in report.warnings:
                    report.warnings.append(msg)
        key = _owner_key(res)
        mention = dict(r)
        mention["toks"] = toks
        best, best_sim = None, 0.0
        for c in by_owner.get(key, []):
            s = max(similarity(toks, v) for v in c.variants)
            if s > best_sim:
                best, best_sim = c, s
        if best is not None and best_sim >= MERGE_THRESHOLD:
            best.mentions.append(mention)
            if toks not in best.variants:
                best.variants.append(toks)
        else:
            c = _Cluster(key, res, [mention], [toks])
            clusters.append(c)
            by_owner.setdefault(key, []).append(c)

    # Persisted identity: keep ids stable across re-ingests (SPEC 6.3).
    existing = {(k["owner_key"], k["norm_task"]): k["item_id"] for k in conn.execute("SELECT * FROM item_keys")}
    by_task: dict[str, list[tuple[int, str]]] = {}
    for k in conn.execute("SELECT k.norm_task, k.item_id, i.owner_kind FROM item_keys k JOIN items i ON i.id = k.item_id"):
        by_task.setdefault(k["norm_task"], []).append((k["item_id"], k["owner_kind"]))
    old_items = {r["id"]: r for r in conn.execute("SELECT * FROM items")}

    claimed: set[int] = set()
    new_ids = 0
    new_keys: list[tuple[str, str, int]] = []
    for c in clusters:
        cand = sorted({existing[(c.owner_key, norm_task(v))] for v in c.variants
                       if (c.owner_key, norm_task(v)) in existing} - claimed)
        if not cand:  # an unresolved owner may have just been resolved: match on task text alone
            cand = sorted({iid for v in c.variants for iid, kind in by_task.get(norm_task(v), [])
                           if kind != "person" and iid not in claimed})[:1]
        values = _cluster_values(c)
        item_id = cand[0] if cand else None
        manual = [old_items[i] for i in cand if i in old_items and old_items[i]["manual_done_at"]]
        manual_at = manual[0]["manual_done_at"] if manual else None
        manual_day = manual[0]["manual_done_date"] if manual else None
        cols = ("owner_kind", "owner_name", "owner_candidates", "group_flag", "task", "due", "due_raw",
                "notes_status", "latest_mention_date", "latest_source", "latest_title", "sources",
                "mention_count", "manual_done_at", "manual_done_date")
        vals = values + (manual_at, manual_day)
        if item_id is None:
            cur = conn.execute(f"INSERT INTO items({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            item_id = cur.lastrowid
            new_ids += 1
        else:
            conn.execute(f"UPDATE items SET {', '.join(c_ + '=?' for c_ in cols)} WHERE id = ?", vals + (item_id,))
        claimed.add(item_id)
        for v in c.variants:
            new_keys.append((c.owner_key, norm_task(v), item_id))
        _report_cluster(c, values, report)

    stale = [i for i in old_items if i not in claimed]
    conn.executemany("DELETE FROM items WHERE id = ?", [(i,) for i in stale])
    conn.execute("DELETE FROM item_keys")
    conn.executemany("INSERT OR IGNORE INTO item_keys(owner_key, norm_task, item_id) VALUES (?,?,?)", new_keys)
    conn.commit()

    report.items_total = len(clusters)
    report.items_new = new_ids
    report.unassigned = sum(1 for c in clusters if c.res is None)
    _near_misses(clusters, report)


def _cluster_values(c: _Cluster) -> tuple:
    ms = c.mentions  # already in (meeting_date, file, line) order
    latest = ms[-1]
    max_date = max(m["meeting_date"] for m in ms)
    same_day = [m for m in ms if m["meeting_date"] == max_date]
    notes_status = "done" if any(m["checked"] for m in same_day) else "open"  # tie: [x] wins
    due = due_raw = None
    for m in reversed(ms):  # A1: latest non-null due date
        if m["due"]:
            due, due_raw = m["due"], m["due_raw"]
            break
    sources: list[dict] = []
    for m in ms:
        entry = {"file": m["fname"], "date": m["meeting_date"]}
        if entry not in sources:
            sources.append(entry)
    res = c.res
    kind = "unassigned" if res is None else res.kind
    name = None if res is None else res.name
    cands = [] if res is None else list(res.candidates)
    return (kind, name, json.dumps(cands), int(any(m["group_flag"] for m in ms)), latest["task"], due, due_raw,
            notes_status, max_date, latest["fname"], latest["title"], json.dumps(sources), len(ms))


def _label(c: _Cluster) -> str:
    return "(unassigned)" if c.res is None else c.res.name


def _report_cluster(c: _Cluster, values: tuple, report: IngestReport) -> None:
    files = ", ".join(s["file"] for s in json.loads(values[11]))
    if len(c.mentions) > 1:
        report.merges.append(f"{_label(c)}: {values[4]!r} <- {len(c.mentions)} mentions ({files})")
    if c.res is not None and c.res.kind == "ambiguous":
        report.ambiguous.append(f"{c.res.name} could be {' / '.join(c.res.candidates)}: {values[4]!r} "
                                f"({c.mentions[-1]['fname']})")


def _near_misses(clusters: list[_Cluster], report: IngestReport) -> None:
    for i, a in enumerate(clusters):
        for b in clusters[i + 1:]:
            if a.owner_key != b.owner_key:
                continue
            s = max(similarity(x, y) for x in a.variants for y in b.variants)
            if NEAR_MISS_THRESHOLD <= s < MERGE_THRESHOLD:
                report.possible_duplicates.append(
                    f"{_label(a)}: {a.mentions[-1]['task']!r} ~ {b.mentions[-1]['task']!r} (similarity {s:.2f})")
