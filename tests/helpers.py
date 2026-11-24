from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from actiontrack import store  # noqa: E402
from actiontrack.cli import main  # noqa: E402
from actiontrack.ingest import ingest_folder  # noqa: E402
from actiontrack.people import Roster  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "notes"
PEOPLE = ROOT / "people.json"


def make_people_with_emails(dir_: Path) -> Path:
    data = json.loads(PEOPLE.read_text(encoding="utf-8"))
    for p in data["people"]:
        p["email"] = p["name"].lower().replace(" ", ".") + "@example.test"
    out = dir_ / "people_emails.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


class Fixture:
    """A temp database with the 36 sample notes ingested."""

    def __init__(self, people: Path = PEOPLE) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        self.people = people
        self.conn = store.connect(self.db)
        self.roster = Roster.load(people)
        self.report = ingest_folder(self.conn, FIXTURES, self.roster)

    def items(self) -> list[store.Item]:
        return store.get_items(self.conn)

    def find(self, task_part: str, owner: str | None = None, source: str | None = None) -> list[store.Item]:
        out = []
        for i in self.items():
            if task_part.lower() not in i.task.lower():
                continue
            if owner is not None and i.owner_display != owner:
                continue
            if source is not None and not any(source in s["file"] for s in i.sources):
                continue
            out.append(i)
        return out

    def one(self, task_part: str, owner: str | None = None, source: str | None = None) -> store.Item:
        found = self.find(task_part, owner, source)
        assert len(found) == 1, f"expected exactly one item for {task_part!r}/{owner!r}, got {[(i.id, i.owner_display, i.task) for i in found]}"
        return found[0]

    def cli(self, *argv: str, today: str = "2026-11-24") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        full = ["--db", str(self.db), "--people", str(self.people), "--today", today, *argv]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(full)
            except SystemExit as exc:  # argparse usage errors
                code = int(exc.code)
        return code, out.getvalue(), err.getvalue()

    def close(self) -> None:
        self.conn.close()
        self.tmp.cleanup()


def d(s: str) -> date:
    return date.fromisoformat(s)
