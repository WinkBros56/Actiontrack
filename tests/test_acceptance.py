"""SPEC section 8 acceptance criteria, run against the 36 sample notes."""

import json
import unittest
from datetime import date

from helpers import Fixture, d, make_people_with_emails, FIXTURES  # noqa: F401
from actiontrack import store
from actiontrack.ingest import ingest_folder
from actiontrack.people import Roster
from helpers import PEOPLE


class AcceptanceBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def assertItem(self, item, *, owner=None, due="__skip__", status=None, today="2026-11-24"):
        if owner is not None:
            self.assertEqual(item.owner_display, owner)
        if due != "__skip__":
            self.assertEqual(item.due, d(due) if due else None)
        if status is not None:
            self.assertEqual(item.status(d(today)), status)


class TestIngest(AcceptanceBase):
    def test_ac1_skips_and_counts(self):  # AC1
        r = self.fx.report
        self.assertEqual((r.examined, r.parsed, len(r.skipped)), (35, 31, 4))
        skipped = dict(r.skipped)
        self.assertEqual(set(skipped), {"2026-09-09-notes-final-v2.md", "2026-10-03-sales-pipeline.md",
                                        "scratch.md", "untitled.md"})
        self.assertIn("empty", skipped["2026-09-09-notes-final-v2.md"])
        self.assertIn("sometime last week", skipped["2026-10-03-sales-pipeline.md"])
        self.assertIn("binary", skipped["scratch.md"])
        self.assertIn("no meeting date", skipped["untitled.md"])
        self.assertNotIn("2026-09-19-offsite-draft.txt", " ".join(n for n, _ in r.skipped))

    def test_ac2_reingest_is_idempotent(self):  # AC2
        before = [(i.id, i.owner_display, i.task, i.due, i.notes_status) for i in self.fx.items()]
        again = ingest_folder(self.fx.conn, FIXTURES, self.fx.roster)
        after = [(i.id, i.owner_display, i.task, i.due, i.notes_status) for i in self.fx.items()]
        self.assertEqual(before, after)
        self.assertEqual(again.items_new, 0)
        self.assertEqual(again.unchanged, 31)

    def test_ids_are_deterministic_on_fresh_db(self):
        other = Fixture()
        try:
            a = [(i.id, i.task) for i in self.fx.items()]
            b = [(i.id, i.task) for i in other.items()]
            self.assertEqual(a, b)
        finally:
            other.close()


class TestTrapsAndFormats(AcceptanceBase):
    def test_ac3_blockquote_not_an_item(self):  # AC3
        refunds = self.fx.find("refund affected customers")
        self.assertEqual(len(refunds), 1)
        self.assertItem(refunds[0], owner="Karan Joshi", due=None)
        self.assertEqual(refunds[0].mention_count, 1)

    def test_ac4_code_fence_and_html_comment_ignored(self):  # AC4
        self.assertEqual(self.fx.find("remediate finding"), [])
        self.assertEqual(self.fx.find("hardware tokens"), [])
        self.assertEqual(len(self.fx.find("", source="2026-10-09-security-review")), 4)

    def test_ac5_offsite_formats(self):  # AC5
        src = "2026-09-30-q4-offsite"
        self.assertItem(self.fx.one("Q4 sales targets", source=src), owner="Meera Iyer", due="2026-10-03")
        self.assertItem(self.fx.one("validate targets", source=src), owner="Karan Joshi", due="2026-10-05")  # nested
        self.assertItem(self.fx.one("customer survey", source=src), owner="Lena Fischer", due="2026-10-15")  # *
        self.assertItem(self.fx.one("on-call rotation", source=src), owner="Sam Okafor", due="2026-10-15")  # +
        self.assertItem(self.fx.one("brand kit", source=src), owner="Anita Desai", due="2026-10-09")  # table
        self.assertItem(self.fx.one("offsite summary", source=src), owner="Karan Joshi", due="2026-10-01")  # ACTION:
        self.assertEqual(len(self.fx.find("", source=src)), 9)

    def test_ac6_uppercase_x_is_done(self):  # AC6
        self.assertItem(self.fx.one("approve the Q4 cloud spend"), owner="Karan Joshi", status="done")


class TestDates(AcceptanceBase):
    def test_ac7_resolution_samples(self):  # AC7
        self.assertItem(self.fx.one("revised quote to Acme"), due="2026-09-11")  # latest mention wins (09-08 by Friday)
        self.assertItem(self.fx.one("audit colour contrast"), due="2026-09-21")
        self.assertItem(self.fx.one("test plan for invoice rounding"), due="2026-09-11")
        self.assertItem(self.fx.one("write the postmortem"), due="2026-09-22")
        self.assertItem(self.fx.one("start SSO spike"), owner="Rahul Verma", due="2026-10-13")
        self.assertItem(self.fx.one("collect 5 customer quotes"), due="2026-10-02")
        self.assertItem(self.fx.one("welcome-email copy"), due="2026-09-25")
        self.assertItem(self.fx.one("on-call rotation"), due="2026-10-15")
        self.assertItem(self.fx.one("freeze invoice edits"), due="2026-10-09")
        self.assertItem(self.fx.one("schedule cutover"), due="2026-10-10")
        self.assertItem(self.fx.one("Q4 goals"), due="2026-10-07")

    def test_first_day_resolutions_from_kickoff(self):
        self.assertItem(self.fx.one("share first wireframes"), due="2026-09-03")
        self.assertItem(self.fx.one("open the repo access request"), due="2026-09-03")
        self.assertItem(self.fx.one("pull Acme's usage report"), due="2026-09-11")


class TestOwners(AcceptanceBase):
    def test_ac9_resolved_priya_and_rahul(self):  # AC9
        self.assertItem(self.fx.one("draft the test plan"), owner="Priya Shah")
        self.assertItem(self.fx.one("review the schema mapping"), owner="Rahul Verma")
        self.assertItem(self.fx.one("revised quote to Acme"), owner="Priya Nair")
        self.assertItem(self.fx.one("run the invoice rounding tests"), owner="Priya Shah")  # "Priya S."
        self.assertItem(self.fx.one("notify customers about the maintenance"), owner="Priya Nair")  # "Priya N."
        self.assertItem(self.fx.one("patch the outdated express"), owner="Rahul Verma")
        self.assertItem(self.fx.one("fix the pagination bug"), owner="Rahul Mehta")

    def test_ac10_ambiguous_owners(self):  # AC10
        for task, owner in [("sign off the cutover checklist", "Priya (?)"), ("own the hiring plan", "Priya (?)"),
                            ("architecture overview", "Rahul (?)"), ("review Anita's PR", "Rahul (?)"),
                            ("rebase the feature branch", "Rahul (?)"), ("go/no-go summary", "Priya (?)")]:
            item = self.fx.one(task)
            self.assertEqual((item.owner_kind, item.owner_display), ("ambiguous", owner), task)
            self.assertEqual(len(item.owner_candidates), 2)
        self.assertEqual(len(self.fx.report.ambiguous), 6)

    def test_ac11_unique_name_without_attendees(self):  # AC11
        self.assertItem(self.fx.one("rotate the staging credentials"), owner="Sam Okafor")

    def test_ac12_unassigned_items(self):  # AC12
        for task in ("update the CRM notes", "design-system changelog", "decide on the launch date",
                     "review the prototype before", "Q4 goals"):
            self.assertEqual(self.fx.one(task).owner_kind, "unassigned", task)
        self.assertTrue(self.fx.one("review the prototype before").group)  # Everyone
        self.assertTrue(self.fx.one("Q4 goals").group)
        self.assertFalse(self.fx.one("update the CRM notes").group)  # Someone
        self.assertEqual(self.fx.report.unassigned, 5)

    def test_ac13_multiple_owners_and_helpers(self):  # AC13
        api = self.fx.find("agree on the component API")
        self.assertEqual(sorted(i.owner_display for i in api), ["Anita Desai", "Rahul Mehta"])
        self.assertTrue(all(i.due == d("2026-09-23") for i in api))
        self.assertEqual(len({i.id for i in api}), 2)
        fb = self.fx.one("collect user feedback")
        self.assertItem(fb, owner="Lena Fischer", due="2026-09-25")
        self.assertIn("(with Priya)", fb.task)
        self.assertEqual(self.fx.find("collect user feedback", owner="Priya Nair"), [])


class TestDuplicates(AcceptanceBase):
    def test_ac14_revised_quote(self):  # AC14
        item = self.fx.one("revised quote to Acme")
        self.assertItem(item, owner="Priya Nair", status="done")
        self.assertEqual(item.mention_count, 3)

    def test_ac15_budget_and_staging(self):  # AC15
        self.assertItem(self.fx.one("confirm cloud budget"), status="done")
        self.assertItem(self.fx.one("staging environment"), status="done")

    def test_ac16_pagination_due_from_later_mention(self):  # AC16
        self.assertItem(self.fx.one("pagination bug"), due="2026-09-23", status="overdue")

    def test_ac17_updated_vs_first_wireframes(self):  # AC17
        updated = self.fx.one("share updated wireframes")
        self.assertEqual(updated.mention_count, 2)
        self.assertEqual(updated.due, d("2026-09-24"))
        self.assertEqual(self.fx.one("share first wireframes").mention_count, 1)
        self.assertTrue(any("first wireframes" in p and "updated wireframes" in p
                            for p in self.fx.report.possible_duplicates))

    def test_ac18_customer_quotes(self):  # AC18
        self.assertEqual(self.fx.one("collect 5 customer quotes").mention_count, 2)
        self.assertEqual(self.fx.one("publish the customer quotes").mention_count, 1)

    def test_ac19_freeze_and_notify(self):  # AC19
        freeze = self.fx.one("freeze invoice edits")
        self.assertItem(freeze, due="2026-10-09", status="overdue")
        self.assertEqual(freeze.mention_count, 2)
        self.assertItem(self.fx.one("notify customers about the maintenance"), status="done")

    def test_ac20_due_kept_when_latest_has_none(self):  # AC20 (assumption A1)
        item = self.fx.one("review the schema mapping")
        self.assertEqual(item.mention_count, 2)
        self.assertItem(item, due="2026-09-07")

    def test_later_unchecked_mention_reopens(self):
        # Rahul Verma 'estimate SSO work' is [ ] in both notes: stays open (overdue).
        self.assertItem(self.fx.one("estimate SSO work"), status="overdue")


class TestStatusAndFilters(AcceptanceBase):
    def test_ac21_overdue_boundary(self):  # AC21
        on_due = self.fx.one("send Acme the revised pricing sheet")  # due 2026-10-01
        self.assertEqual(on_due.status(d("2026-10-01")), "open")
        self.assertEqual(on_due.status(d("2026-10-02")), "overdue")
        code, out, _ = self.fx.cli("list", "--status", "overdue", today="2026-10-01")
        self.assertEqual(code, 0)
        self.assertNotIn("send the revised quote to Acme", out)  # done items never overdue
        self.assertIn("fix the pagination bug", out)

    def test_ac22_due_before_is_strict_and_excludes_no_due(self):  # AC22
        code, out, _ = self.fx.cli("list", "--due-before", "2026-10-01", "--json")
        rows = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(rows and all(r["due"] is not None and r["due"] < "2026-10-01" for r in rows))
        self.assertNotIn("2026-10-01", {r["due"] for r in rows})

    def test_ac23_owner_filter(self):  # AC23
        code, out, err = self.fx.cli("list", "--owner", "Priya")
        self.assertEqual(code, 2)
        self.assertIn("Priya Nair", err)
        self.assertIn("Priya Shah", err)
        for q in ("Priya Nair", "Priya N.", "priya n"):
            code, out, _ = self.fx.cli("list", "--owner", q, "--json")
            rows = json.loads(out)
            self.assertEqual(code, 0, q)
            self.assertTrue(rows and {r["owner"] for r in rows} == {"Priya Nair"}, q)
        code, out, _ = self.fx.cli("list", "--owner", "unassigned", "--json")
        self.assertEqual(len(json.loads(out)), 5)
        code, out, _ = self.fx.cli("list", "--owner", "ambiguous", "--json")
        self.assertEqual(len(json.loads(out)), 6)
        code, _, err = self.fx.cli("list", "--owner", "Nobody Atall")
        self.assertEqual(code, 2)

    def test_bad_arguments_exit_2(self):
        self.assertEqual(self.fx.cli("list", "--due-before", "tomorrow")[0], 2)
        self.assertEqual(self.fx.cli("list", "--status", "late")[0], 2)
        self.assertEqual(self.fx.cli("list", today="yesterday")[0], 2)

    def test_default_sort_due_ascending_no_due_last(self):
        _, out, _ = self.fx.cli("list", "--json")
        dues = [r["due"] for r in json.loads(out)]
        dated = [x for x in dues if x]
        self.assertEqual(dated, sorted(dated))
        self.assertEqual(dues[len(dated):], [None] * (len(dues) - len(dated)))


class TestDone(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_ac24_done_is_sticky_across_reingest(self):  # AC24
        item = self.fx.one("set up uptime monitoring")
        code, out, _ = self.fx.cli("done", str(item.id))
        self.assertEqual(code, 0)
        self.assertEqual(store.get_item(self.fx.conn, item.id).status(d("2026-11-24")), "done")
        code, out, _ = self.fx.cli("done", str(item.id))
        self.assertEqual(code, 0)
        self.assertIn("already done", out)
        ingest_folder(self.fx.conn, FIXTURES, self.fx.roster)
        again = self.fx.one("set up uptime monitoring")
        self.assertEqual(again.id, item.id)
        self.assertEqual(again.status(d("2026-11-24")), "done")

    def test_unknown_id_exits_1(self):
        code, _, err = self.fx.cli("done", "99999")
        self.assertEqual(code, 1)
        self.assertIn("99999", err)

    def test_later_unchecked_note_reopens_manual_done(self):
        item = self.fx.one("set up uptime monitoring")  # last mentioned 2026-10-06
        self.fx.cli("done", str(item.id), today="2026-10-01")  # manual done *before* the last mention
        self.assertEqual(store.get_item(self.fx.conn, item.id).status(d("2026-11-24")), "open")


class TestFollowup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fx = Fixture(make_people_with_emails(Path(cls.tmp.name)))

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()
        cls.tmp.cleanup()

    def test_ac25_single_owner_dry_run(self):  # AC25
        before = self.fx.conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
        code, out, err = self.fx.cli("followup", "Karan Joshi", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("To: karan.joshi@example.test", out)
        self.assertIn("Hi Karan,", out)
        self.assertLess(out.index("Overdue ("), out.index("No due date ("))  # nothing is merely "open" by 2026-11-24
        self.assertIn("[#", out)
        self.assertIn("days overdue", out)
        self.assertNotIn("Priya", out)
        self.assertEqual(self.fx.conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0], before)  # dry run logs nothing
        self.assertIn("Nothing was sent", err)

    def test_ac25_all_owners_json_payloads(self):
        before = self.fx.conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0]
        code, out, err = self.fx.cli("followup", "--all")
        self.assertEqual(code, 0, err)
        drafts = json.loads(out)["drafts"]
        owners = [x["owner"] for x in drafts]
        self.assertEqual(len(owners), len(set(owners)))
        self.assertEqual(set(owners), {"Anita Desai", "Karan Joshi", "Lena Fischer", "Meera Iyer", "Priya Nair",
                                       "Priya Shah", "Rahul Mehta", "Rahul Verma", "Sam Okafor"})
        for x in drafts:
            cd = x["create_draft"]
            self.assertEqual(set(cd), {"to", "subject", "body", "includeSignature"})
            self.assertTrue(cd["includeSignature"])
            self.assertEqual(len(cd["to"]), 1)
            self.assertNotIn("**", cd["body"])
        # ambiguous & unassigned items are never in anyone's email
        ambiguous_ids = {i.id for i in self.fx.items() if i.owner_kind in ("ambiguous", "unassigned")}
        sent_ids = {n for x in drafts for n in x["item_ids"]}
        self.assertFalse(ambiguous_ids & sent_ids)
        # done items are never in an email
        done_ids = {i.id for i in self.fx.items() if i.status(date(2026, 11, 24)) == "done"}
        self.assertFalse(done_ids & sent_ids)
        self.assertIn("were not emailed", err)
        self.assertEqual(self.fx.conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0] - before, len(drafts))

    def test_second_run_same_day_warns_but_does_not_block(self):
        self.fx.cli("followup", "Sam Okafor")
        code, out, err = self.fx.cli("followup", "Sam Okafor")
        self.assertEqual(code, 0)
        self.assertIn("already created", err)
        self.assertEqual(len(json.loads(out)["drafts"]), 1)

    def test_ambiguous_owner_arg_exits_2(self):
        self.assertEqual(self.fx.cli("followup", "Priya", "--dry-run")[0], 2)

    def test_needs_owner_or_all(self):
        self.assertEqual(self.fx.cli("followup")[0], 2)
        self.assertEqual(self.fx.cli("followup", "Sam", "--all")[0], 2)

    def test_email_sections_overdue_then_open_then_no_due(self):
        code, out, _ = self.fx.cli("followup", "Sam Okafor", "--dry-run", today="2026-10-12")
        self.assertEqual(code, 0)
        self.assertLess(out.index("Overdue ("), out.index("Open ("))
        self.assertLess(out.index("Open ("), out.index("No due date ("))
        self.assertIn("Subject: Action items follow-up:", out)


class TestFollowupBlankEmail(unittest.TestCase):
    def test_ac26_blank_email_gives_empty_to_and_warning(self):  # AC26
        fx = Fixture()  # roster with blank emails
        try:
            code, out, err = fx.cli("followup", "Sam Okafor")
            self.assertEqual(code, 0)
            draft = json.loads(out)["drafts"][0]
            self.assertEqual(draft["create_draft"]["to"], [])
            self.assertTrue(draft["warnings"])
            self.assertIn("no valid email", err)
        finally:
            fx.close()


class TestRosterChangeFixesAmbiguity(unittest.TestCase):
    def test_alias_added_then_reingest_keeps_item_id(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "notes"
            folder.mkdir()
            (folder / "a.md").write_text(
                "# Meeting: X\nDate: 2026-09-01\nAttendees: Priya Nair, Priya Shah\n\n- [ ] Priya to ship it by Friday\n",
                encoding="utf-8")
            conn = store.connect(Path(tmp) / "t.db")
            first = ingest_folder(conn, folder, Roster.load(PEOPLE))
            self.assertEqual(len(first.ambiguous), 1)
            before = store.get_items(conn)[0]
            self.assertEqual(before.owner_kind, "ambiguous")
            # user edits the note to disambiguate, then re-ingests
            (folder / "a.md").write_text(
                "# Meeting: X\nDate: 2026-09-01\nAttendees: Priya Nair, Priya Shah\n\n- [ ] Priya S. to ship it by Friday\n",
                encoding="utf-8")
            ingest_folder(conn, folder, Roster.load(PEOPLE))
            items = store.get_items(conn)
            self.assertEqual(len(items), 1)
            self.assertEqual((items[0].id, items[0].owner_display), (before.id, "Priya Shah"))
            conn.close()


if __name__ == "__main__":
    unittest.main()
