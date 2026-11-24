"""Meeting-note parser (SPEC 5)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .dates import extract_due, parse_meeting_date


class NoteSkipped(Exception):
    """The note is malformed; ``args[0]`` is the human-readable reason."""


@dataclass
class RawMention:
    line: int
    idx: int  # 0 unless one line names several owners
    kind: str  # checkbox | table | action
    checked: bool
    owner_raw: str | None  # None = unassigned
    group: bool
    task: str
    due: date | None
    due_raw: str | None


@dataclass
class Note:
    path: str
    title: str
    date: date
    attendees: list[str]
    mentions: list[RawMention] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[(?P<c>[ xX])\]\s*(?P<text>.*)$")
_ACTION = re.compile(r"^\s*(?:[-*+]\s+)?ACTION:\s*(?P<text>.*)$", re.IGNORECASE)
_BLOCKQUOTE = re.compile(r"^\s*>")
_FENCE = re.compile(r"^\s*(```|~~~)")
_DATE_LINE = re.compile(r"^\s*(?:\*\*)?date(?:\*\*)?\s*:\s*(?P<v>.*)$", re.IGNORECASE)
_ATTENDEES_LINE = re.compile(r"^\s*(?:\*\*)?attendees(?:\*\*)?\s*:\s*(?P<v>.*)$", re.IGNORECASE)
_HEADING = re.compile(r"^\s*#\s+(?P<t>.+?)\s*#*\s*$")

UNASSIGNED_WORDS = {"someone", "somebody", "tbd", "tba", "team", "nobody", "anyone", "unassigned", "n/a"}
GROUP_WORDS = {"everyone", "everybody", "all"}


def _split_attendees(value: str) -> list[str]:
    value = value.strip().strip("[]")
    return [a.strip().strip("'\"") for a in re.split(r"[,;]", value) if a.strip().strip("'\"")]


def _front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Return (key->value, index of first body line)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta: dict[str, str] = {}
            for ln in lines[1:i]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            return meta, i + 1
    return {}, 0


def _looks_like_name(owner_text: str) -> bool:
    """Cheap guard so that 'Review the doc to improve X' is not read as owner='Review the doc'."""
    cleaned = re.sub(r"\([^)]*\)", " ", owner_text)
    words = [w for w in re.split(r"\s+", cleaned.strip()) if w]
    if not words or len(words) > 6:
        return False
    for w in words:
        wl = w.lower().strip(",.&")
        if w in ("&", "and", "/") or wl in ("and",) or w.startswith("@"):
            continue
        if wl in UNASSIGNED_WORDS or wl in GROUP_WORDS:
            continue
        if not w[0].isupper():
            return False
    return True


def split_owner(text: str) -> tuple[list[tuple[str | None, bool]], str, str | None]:
    """Return ([(owner_raw|None, group)], task_text, helper) for one item's text."""
    t = text.strip()
    owners_text, rest = "", t
    m = re.match(r"^@(?P<o>[\w.\-]+)\s*:\s*(?P<rest>.+)$", t)
    if m:
        owners_text, rest = m["o"], m["rest"]
    else:
        m_to = re.search(r"\s+to\s+", t, re.IGNORECASE)
        m_colon = re.match(r"^(?P<o>[A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*)?)\s*:\s+(?P<rest>.+)$", t)
        if m_colon and (not m_to or m_colon.end("o") < m_to.start()):
            owners_text, rest = m_colon["o"], m_colon["rest"]
        elif m_to and _looks_like_name(t[: m_to.start()]):
            owners_text, rest = t[: m_to.start()], t[m_to.end():]
    if not owners_text:
        return [(None, False)], t, None

    helper = None
    hm = re.search(r"\(\s*with\s+([^)]*)\)", owners_text, re.IGNORECASE)
    if hm:
        helper = hm.group(1).strip()
        owners_text = (owners_text[: hm.start()] + owners_text[hm.end():]).strip()
    owners: list[tuple[str | None, bool]] = []
    for part in re.split(r"\s*(?:&|,|/|\band\b)\s*", owners_text):
        part = part.strip().lstrip("@").strip()
        if not part:
            continue
        low = part.lower()
        if low in GROUP_WORDS:
            owners.append((None, True))
        elif low in UNASSIGNED_WORDS:
            owners.append((None, False))
        else:
            owners.append((part, False))
    if not owners:
        owners = [(None, False)]
    return owners, rest.strip(), helper


def _mentions_from_text(text: str, base: date, line_no: int, kind: str, checked: bool) -> list[RawMention]:
    owners, task_text, helper = split_owner(text)
    due = extract_due(task_text, base)
    task = due.task
    if helper:
        task = f"{task} (with {helper})"
    out = []
    for idx, (owner, group) in enumerate(owners):
        out.append(RawMention(line_no, idx, kind, checked, owner, group, task, due.due, due.raw))
    return out


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_note_text(text: str, path: str = "") -> Note:
    if not text.strip():
        raise NoteSkipped("empty file")
    lines = text.splitlines()
    meta, body_start = _front_matter(lines)

    title = meta.get("title", "")
    date_raw = meta.get("date")
    attendees_raw = meta.get("attendees")

    # Strip fences / comments / quotes first, remember line numbers.
    body: list[tuple[int, str]] = []
    in_fence = False
    in_comment = False
    for no in range(body_start, len(lines)):
        ln = lines[no]
        if _FENCE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if in_comment:
            if "-->" in ln:
                in_comment = False
                ln = ln.split("-->", 1)[1]
            else:
                continue
        ln = re.sub(r"<!--.*?-->", "", ln)
        if "<!--" in ln:
            ln = ln.split("<!--", 1)[0]
            in_comment = True
        body.append((no + 1, ln))

    for _, ln in body:
        if not title:
            h = _HEADING.match(ln)
            if h:
                title = re.sub(r"^meeting\s*:\s*", "", h["t"], flags=re.IGNORECASE).strip()
        if date_raw is None:
            d = _DATE_LINE.match(ln)
            if d:
                date_raw = d["v"]
        if attendees_raw is None:
            a = _ATTENDEES_LINE.match(ln)
            if a:
                attendees_raw = a["v"]

    warnings: list[str] = []
    if date_raw is None or not date_raw.strip():
        raise NoteSkipped("no meeting date found")
    md = parse_meeting_date(date_raw)
    if md is None:
        raise NoteSkipped(f"unparseable meeting date: {date_raw.strip()!r}")
    if md.weekday_mismatch:
        warnings.append(f"weekday does not match date {md.value.isoformat()} ({date_raw.strip()!r})")
    attendees = _split_attendees(attendees_raw) if attendees_raw else []
    if not attendees:
        warnings.append("no attendees listed")
    if not title:
        title = Path(path).stem if path else "(untitled)"

    note = Note(path, title, md.value, attendees, [], warnings)

    table_cols: dict[str, int] | None = None
    for no, ln in body:
        if _BLOCKQUOTE.match(ln):
            table_cols = None
            continue
        stripped = ln.strip()
        if stripped.startswith("|"):
            cells = _table_cells(stripped)
            lowered = [c.lower() for c in cells]
            if table_cols is None:
                if "owner" in lowered and "action" in lowered:
                    table_cols = {"owner": lowered.index("owner"), "action": lowered.index("action"),
                                  "due": lowered.index("due") if "due" in lowered else -1}
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            try:
                owner = cells[table_cols["owner"]]
                action = cells[table_cols["action"]]
                due = cells[table_cols["due"]] if table_cols["due"] >= 0 else ""
            except IndexError:
                continue
            if not action:
                continue
            composed = f"{owner} to {action}" if owner else action
            if due:
                composed += f" by {due}"
            note.mentions.extend(_mentions_from_text(composed, md.value, no, "table", False))
            continue
        table_cols = None
        cb = _CHECKBOX.match(ln)
        if cb:
            if cb["text"].strip():
                note.mentions.extend(
                    _mentions_from_text(cb["text"], md.value, no, "checkbox", cb["c"] in "xX"))
            continue
        ac = _ACTION.match(ln)
        if ac and ac["text"].strip():
            note.mentions.extend(_mentions_from_text(ac["text"], md.value, no, "action", False))
    return note


def parse_note_file(path: str | Path) -> Note:
    data = Path(path).read_bytes()
    if not data.strip():
        raise NoteSkipped("empty file")
    if b"\x00" in data:
        raise NoteSkipped("binary content (NUL bytes)")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise NoteSkipped("not valid UTF-8 text") from None
    return parse_note_text(text, str(path))
