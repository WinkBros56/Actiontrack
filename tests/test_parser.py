import tempfile
import unittest
from pathlib import Path

from helpers import d
from actiontrack import store
from actiontrack.ingest import ingest_folder
from actiontrack.parser import NoteSkipped, parse_note_file, parse_note_text, split_owner
from actiontrack.people import Roster

HEAD = "# Meeting: T\nDate: 2026-09-01\nAttendees: Priya Nair, Sam Okafor\n\n"


def mentions(body: str, head: str = HEAD):
    return parse_note_text(head + body).mentions


class LineShapes(unittest.TestCase):
    def test_no_space_after_checkbox(self):  # the brief's own example: "- [ ]Priya to send ..."
        m = mentions("- [ ]Priya to send the revised quote by Friday\n")
        self.assertEqual((m[0].owner_raw, m[0].task, m[0].due), ("Priya", "send the revised quote", d("2026-09-04")))

    def test_bullets_boxes_and_nesting(self):
        m = mentions("- [ ] A to one\n* [x] B to two\n+ [X] C to three\n  - [ ] D to four\n    * [ ] E to five\n")
        self.assertEqual([x.owner_raw for x in m], list("ABCDE"))
        self.assertEqual([x.checked for x in m], [False, True, True, False, False])

    def test_fences_comments_quotes_ignored(self):
        body = ("```\n- [ ] Fenced to ignore\n```\n<!-- - [ ] Inline to ignore -->\n"
                "<!--\n- [ ] Multi-line to ignore\n-->\n> - [ ] Quoted to ignore\n- [ ] Real to keep\n")
        self.assertEqual([x.owner_raw for x in mentions(body)], ["Real"])

    def test_table_with_reordered_columns_and_missing_due(self):
        body = "| Due | Action | Owner |\n|---|---|---|\n| 2026-10-09 | Refresh the kit | Anita |\n|  | Plan party | Sam |\n"
        m = mentions(body)
        self.assertEqual([(x.owner_raw, x.task, x.due) for x in m],
                         [("Anita", "Refresh the kit", d("2026-10-09")), ("Sam", "Plan party", None)])

    def test_action_line(self):
        m = mentions("ACTION: Karan to circulate the summary tomorrow\n")
        self.assertEqual((m[0].kind, m[0].owner_raw, m[0].due), ("action", "Karan", d("2026-09-02")))

    def test_checkbox_anywhere_not_only_action_items_section(self):
        self.assertEqual(len(mentions("## Discussion\ntext\n- [ ] Sam to do it\n## Parking lot\n- [ ] Priya to too\n")), 2)

    def test_empty_checkbox_ignored_and_plain_bullets_ignored(self):
        self.assertEqual(mentions("- [ ]\n- not a checkbox to ignore\n- [ ] \n"), [])

    def test_crlf_files(self):
        note = parse_note_text(("# Meeting: T\r\nDate: 2026-09-01\r\n\r\n- [ ] Sam to do it by Friday\r\n"))
        self.assertEqual(note.mentions[0].task, "do it")

    def test_front_matter(self):
        note = parse_note_text("---\ntitle: Team standup\ndate: 2026-09-16\nattendees: [Anita Desai, Lena Fischer]\n---\n\n- [ ] Lena to draft it\n")
        self.assertEqual((note.title, note.date, note.attendees), ("Team standup", d("2026-09-16"), ["Anita Desai", "Lena Fischer"]))


class OwnerSplitting(unittest.TestCase):
    def test_shapes(self):
        cases = {
            "Priya N. to send the quote": ([("Priya N.", False)], "send the quote", None),
            "@anita: audit contrast": ([("anita", False)], "audit contrast", None),
            "Anita & Rahul to agree on the API": ([("Anita", False), ("Rahul", False)], "agree on the API", None),
            "Lena (with Priya) to collect feedback": ([("Lena", False)], "collect feedback", "Priya"),
            "Everyone to submit goals": ([(None, True)], "submit goals", None),
            "Someone to update the CRM": ([(None, False)], "update the CRM", None),
            "TBD to write the changelog": ([(None, False)], "write the changelog", None),
            "Team to decide on the date": ([(None, False)], "decide on the date", None),
        }
        for text, expected in cases.items():
            self.assertEqual(split_owner(text), expected, text)

    def test_text_without_owner_is_unassigned_not_misparsed(self):
        owners, task, _ = split_owner("Review the doc to improve clarity")
        self.assertEqual((owners, task), ([(None, False)], "Review the doc to improve clarity"))
        self.assertEqual(split_owner("Refund customers")[0], [(None, False)])


class MalformedFiles(unittest.TestCase):
    def skipped(self, data: bytes) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.md"
            p.write_bytes(data)
            with self.assertRaises(NoteSkipped) as cm:
                parse_note_file(p)
            return str(cm.exception)

    def test_reasons(self):
        self.assertIn("empty", self.skipped(b""))
        self.assertIn("empty", self.skipped(b"  \n\n"))
        self.assertIn("binary", self.skipped(b"\x89PNG\r\n\x1a\n\x00\x00"))
        self.assertIn("UTF-8", self.skipped(b"# T\nDate: 2026-09-01\n\xff\xfe\xfa\n"))
        self.assertIn("no meeting date", self.skipped(b"- [ ] Sam to chase the vendor\n"))
        self.assertIn("unparseable", self.skipped(b"# T\nDate: 09/10/2026\n- [ ] Sam to x\n"))
        self.assertIn("no meeting date", self.skipped(b"---\ntitle: x\n---\n- [ ] Sam to x\n"))

    def test_bad_note_never_aborts_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "n"
            folder.mkdir()
            (folder / "bad.md").write_bytes(b"\xff\xfe\x00\x00")
            (folder / "dir.md").mkdir()  # a directory named like a note
            (folder / "good.MD").write_text(HEAD + "- [ ] Sam to ship it by Friday\n", encoding="utf-8")
            (folder / "sub").mkdir()
            (folder / "sub" / "nested.md").write_text(HEAD + "- [ ] Sam to not be read\n", encoding="utf-8")
            conn = store.connect(Path(tmp) / "t.db")
            try:
                report = ingest_folder(conn, folder, Roster([]))
                self.assertEqual((report.examined, report.parsed, len(report.skipped)), (2, 1, 1))
                self.assertEqual([i.task for i in store.get_items(conn)], ["ship it"])  # .MD read, subfolder not
            finally:
                conn.close()

    def test_missing_folder_is_error_exit_1(self):
        from helpers import Fixture
        fx = Fixture()
        try:
            code, _, err = fx.cli("ingest", str(fx.dir / "does-not-exist"))
            self.assertEqual(code, 1)
            self.assertIn("not a folder", err)
        finally:
            fx.close()


if __name__ == "__main__":
    unittest.main()
