"""actiontrack command line (SPEC 4)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from . import store
from .drafts import build_draft, render_text
from .ingest import ingest_folder
from .people import Person, Roster, norm
from .sinks import JsonSink, McpSink, SinkError
from .store import Item


class CliError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _parse_iso(value: str, what: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CliError(f"{what}: expected YYYY-MM-DD, got {value!r}", 2) from None


def _today(args: argparse.Namespace) -> date:
    raw = args.today or os.environ.get("ACTIONTRACK_TODAY")
    return _parse_iso(raw, "--today") if raw else date.today()


def _now_iso(today: date) -> str:
    return datetime.now().replace(microsecond=0).isoformat()


_OPEN: list = []


def _open(args: argparse.Namespace):
    """Open the database; main() closes every connection when the command ends (matters on Windows)."""
    conn = store.connect(_db_path(args))
    _OPEN.append(conn)
    return conn


def _db_path(args: argparse.Namespace) -> str:
    return args.db or os.environ.get("ACTIONTRACK_DB") or "actiontrack.db"


def _owner_matches(query: str, roster: Roster, items: list[Item]):
    """Return a predicate for --owner / followup <owner> (SPEC 4.2)."""
    q = query.strip()
    if q.lower() == "unassigned":
        return lambda i: i.owner_kind == "unassigned"
    if q.lower() == "ambiguous":
        return lambda i: i.owner_kind == "ambiguous"
    kind, people = roster.find(q)
    if kind == "many":
        raise CliError(f"owner {q!r} is ambiguous; candidates: {', '.join(p.name for p in people)}", 2)
    if kind == "one":
        target = norm(people[0].name)
        return lambda i: i.owner_kind in ("person", "unknown") and norm(i.owner_name or "") == target
    names = {i.owner_name for i in items if i.owner_kind in ("person", "unknown") and i.owner_name}
    exact = [n for n in names if norm(n) == norm(q)]
    first = [n for n in names if norm(n).split()[0] == norm(q).split()[0]] if norm(q) else []
    pick = exact or (first if len(first) == 1 else [])
    if len(first) > 1 and not exact:
        raise CliError(f"owner {q!r} is ambiguous; candidates: {', '.join(sorted(first))}", 2)
    if not pick:
        raise CliError(f"unknown owner {q!r}", 2)
    return lambda i: i.owner_kind in ("person", "unknown") and i.owner_name == pick[0]


def cmd_ingest(args: argparse.Namespace) -> int:
    roster = Roster.load(args.people)
    conn = _open(args)
    try:
        report = ingest_folder(conn, args.folder, roster)
    except NotADirectoryError as exc:
        raise CliError(str(exc), 1) from None
    print(report.render())
    return 0


def _row(i: Item, today: date) -> dict:
    return {"id": i.id, "status": i.status(today), "owner": i.owner_display, "owner_kind": i.owner_kind,
            "group": i.group, "task": i.task, "due": i.due.isoformat() if i.due else None, "due_raw": i.due_raw,
            "source": i.latest_source, "title": i.latest_title,
            "meeting_date": i.latest_mention_date.isoformat(), "sources": i.sources}


def cmd_list(args: argparse.Namespace) -> int:
    today = _today(args)
    roster = Roster.load(args.people)
    conn = _open(args)
    items = store.get_items(conn)
    if args.owner:
        pred = _owner_matches(args.owner, roster, items)
        items = [i for i in items if pred(i)]
    if args.status:
        items = [i for i in items if i.status(today) == args.status]
    if args.due_before:
        cutoff = _parse_iso(args.due_before, "--due-before")
        items = [i for i in items if i.due is not None and i.due < cutoff]
    items.sort(key=lambda i: (i.due is None, i.due or date.max, i.id))
    rows = [_row(i, today) for i in items]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No items." if conn.execute("SELECT 1 FROM items LIMIT 1").fetchone()
              else "No items. Run `actiontrack ingest <folder>` first.")
        return 0
    cols = [("id", "ID"), ("status", "STATUS"), ("owner", "OWNER"), ("task", "TASK"), ("due", "DUE"), ("source", "SOURCE")]
    table = [{k: ("-" if r[k] is None else str(r[k])) for k, _ in cols} for r in rows]
    widths = {k: max(len(h), *(len(t[k]) for t in table)) for k, h in cols}
    print("  ".join(h.ljust(widths[k]) for k, h in cols).rstrip())
    print("  ".join("-" * widths[k] for k, _ in cols))
    for t in table:
        print("  ".join(t[k].ljust(widths[k]) for k, _ in cols).rstrip())
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    today = _today(args)
    conn = _open(args)
    item = store.get_item(conn, args.id)
    if item is None:
        raise CliError(f"no item with id {args.id}", 1)
    if item.is_done():
        print(f"#{item.id} is already done: {item.task}")
        return 0
    store.mark_done(conn, item.id, _now_iso(today), today)
    print(f"Marked #{item.id} done: {item.owner_display} - {item.task}")
    return 0


def cmd_followup(args: argparse.Namespace) -> int:
    if bool(args.all) == bool(args.owner):
        raise CliError("give exactly one of <owner> or --all", 2)
    today = _today(args)
    roster = Roster.load(args.people)
    conn = _open(args)
    all_items = store.get_items(conn)
    live = [i for i in all_items if i.status(today) != "done"]

    skipped_unassigned = [i for i in live if i.owner_kind == "unassigned"]
    skipped_ambiguous = [i for i in live if i.owner_kind == "ambiguous"]
    if args.all:
        names = sorted({i.owner_name for i in live if i.owner_kind in ("person", "unknown") and i.owner_name})
    else:
        pred = _owner_matches(args.owner, roster, all_items)
        if not any(pred(i) for i in all_items if i.owner_kind in ("person", "unknown")):
            kind, people = roster.find(args.owner)
            names = [people[0].name] if kind == "one" else []
        else:
            names = sorted({i.owner_name for i in all_items
                            if i.owner_kind in ("person", "unknown") and i.owner_name and pred(i)})
        if not names:
            raise CliError(f"unknown owner {args.owner!r}", 2)

    sink = None
    if not args.dry_run:
        try:
            sink = McpSink.from_config(args.config) if args.sink == "mcp" else JsonSink()
        except SinkError as exc:
            raise CliError(str(exc), 1) from None

    made = 0
    try:
        for name in names:
            mine = [i for i in live if i.owner_kind in ("person", "unknown") and i.owner_name == name]
            if not mine:
                print(f"{name}: nothing open; skipped.", file=sys.stderr)
                continue
            person = roster.by_name(name) or Person(name)
            draft = build_draft(name, person, mine, today)
            for w in draft.warnings:
                print(f"warning: {w}", file=sys.stderr)
            if store.followed_up_on(conn, name, today):
                print(f"warning: a follow-up for {name} was already created on {today.isoformat()}", file=sys.stderr)
            if args.dry_run:
                print(render_text(draft))
                print("-" * 60)
                made += 1
                continue
            ref = sink.create(draft)
            store.log_followup(conn, name, _now_iso(today), today, draft.item_ids, sink.name, ref)
            made += 1
            if sink.name == "mcp":
                print(f"Draft created for {name}: {ref}", file=sys.stderr)
    except SinkError as exc:
        raise CliError(str(exc), 1) from None
    finally:
        if sink is not None:
            sink.close()

    if isinstance(sink, JsonSink):
        text = json.dumps(sink.payload(), indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"Wrote {made} draft payload(s) to {args.out}", file=sys.stderr)
        else:
            print(text)
    verb = "previewed" if args.dry_run else "prepared"
    print(f"{made} follow-up draft(s) {verb}. Nothing was sent.", file=sys.stderr)
    if skipped_unassigned:
        print(f"note: {len(skipped_unassigned)} open item(s) have no owner and were not emailed "
              f"(ids {', '.join('#' + str(i.id) for i in skipped_unassigned)}).", file=sys.stderr)
    if skipped_ambiguous:
        print(f"note: {len(skipped_ambiguous)} open item(s) have an ambiguous owner and were not emailed "
              f"(ids {', '.join('#' + str(i.id) for i in skipped_ambiguous)}).", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    def add_globals(p: argparse.ArgumentParser, suppress: bool) -> None:
        d = argparse.SUPPRESS if suppress else None
        p.add_argument("--db", default=d, help="SQLite file (default ./actiontrack.db or $ACTIONTRACK_DB)")
        p.add_argument("--people", default=argparse.SUPPRESS if suppress else "people.json",
                       help="roster file (default ./people.json)")
        p.add_argument("--today", default=d, metavar="YYYY-MM-DD",
                       help="override today's date (also $ACTIONTRACK_TODAY)")

    parser = argparse.ArgumentParser(prog="actiontrack", description="Track meeting action items; draft follow-ups.")
    add_globals(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="parse every .md note in a folder")
    add_globals(p, True)
    p.add_argument("folder")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("list", help="show action items")
    add_globals(p, True)
    p.add_argument("--owner")
    p.add_argument("--status", choices=["open", "done", "overdue"])
    p.add_argument("--due-before", metavar="YYYY-MM-DD")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("done", help="mark an item done")
    add_globals(p, True)
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_done)

    p = sub.add_parser("followup", help="draft follow-up emails (never sends)")
    add_globals(p, True)
    p.add_argument("owner", nargs="?")
    p.add_argument("--all", action="store_true", help="one draft per owner with open/overdue items")
    p.add_argument("--dry-run", action="store_true", help="print drafts; create nothing")
    p.add_argument("--sink", choices=["json", "mcp"], default="json")
    p.add_argument("--out", help="write json drafts to this file instead of stdout")
    p.add_argument("--config", default="actiontrack.config.json", help="MCP sink config file")
    p.set_defaults(func=cmd_followup)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    finally:
        while _OPEN:
            _OPEN.pop().close()


if __name__ == "__main__":
    sys.exit(main())

