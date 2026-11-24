import unittest

from helpers import d  # noqa: F401  (also sets sys.path)
from actiontrack.dates import extract_due, parse_meeting_date, resolve_expr


class ResolveExpr(unittest.TestCase):
    def check(self, expr, base, expected):
        self.assertEqual(resolve_expr(expr, d(base)), d(expected) if expected else None, f"{expr!r} from {base}")

    def test_weekday_is_strictly_after_meeting(self):
        self.check("Friday", "2026-09-01", "2026-09-04")  # Tue -> Fri
        self.check("Mon", "2026-09-18", "2026-09-21")  # Fri -> Mon
        self.check("Wednesday", "2026-09-22", "2026-09-23")

    def test_same_weekday_means_next_week(self):  # AC8
        self.check("Friday", "2026-09-18", "2026-09-25")
        self.check("Monday", "2026-09-14", "2026-09-21")

    def test_next_weekday_is_following_calendar_week(self):
        self.check("next Tuesday", "2026-10-05", "2026-10-13")  # Mon -> Tue of next week
        self.check("next Monday", "2026-10-07", "2026-10-12")

    def test_relative_days(self):
        self.check("tomorrow", "2026-09-02", "2026-09-03")
        self.check("EOD tomorrow", "2026-09-21", "2026-09-22")
        self.check("EOD", "2026-10-08", "2026-10-08")

    def test_end_of_next_week_and_month(self):
        self.check("end of next week", "2026-09-03", "2026-09-11")
        self.check("end of month", "2026-09-14", "2026-09-30")
        self.check("end of the month", "2026-02-10", "2026-02-28")

    def test_mid_month(self):
        self.check("mid-October", "2026-09-30", "2026-10-15")

    def test_explicit_dates_default_to_meeting_year(self):
        self.check("10 Sep", "2026-09-03", "2026-09-10")
        self.check("Oct 3rd", "2026-09-30", "2026-10-03")
        self.check("2 Oct", "2026-09-24", "2026-10-02")
        self.check("15 October", "2026-09-30", "2026-10-15")
        self.check("2026-10-09", "2026-09-30", "2026-10-09")

    def test_explicit_date_wins_over_weekday_word(self):
        self.check("Wed 16 Sep", "2026-09-14", "2026-09-16")
        self.check("Saturday 10 Oct", "2026-09-29", "2026-10-10")

    def test_invalid_dates_do_not_crash(self):
        self.check("31 Feb", "2026-09-01", None)


class ExtractDue(unittest.TestCase):
    def test_by_phrase_removed_from_task(self):
        r = extract_due("send revised quote to Acme — by Friday", d("2026-09-08"))
        self.assertEqual((r.task, r.due, r.raw), ("send revised quote to Acme", d("2026-09-11"), "by Friday"))

    def test_other_prepositions(self):
        self.assertEqual(extract_due("freeze invoice edits from 9 Oct", d("2026-09-29")).due, d("2026-10-09"))
        self.assertEqual(extract_due("schedule cutover for Saturday 10 Oct", d("2026-09-29")).due, d("2026-10-10"))
        self.assertEqual(extract_due("demo the portal to Acme on 2026-10-14", d("2026-10-06")).due, d("2026-10-14"))
        self.assertEqual(extract_due("run the survey, results due 15 October", d("2026-09-30")).due, d("2026-10-15"))

    def test_no_false_positives(self):
        for text in ("share the VPN guide", "collect 5 customer quotes for the landing page",
                     "cut cloud costs by 10%", "snapshot the production DB before cutover",
                     "review the prototype before the next review", "set up uptime monitoring"):
            self.assertIsNone(extract_due(text, d("2026-10-01")).due, text)

    def test_bare_trailing_tomorrow(self):
        r = extract_due("circulate the offsite summary tomorrow", d("2026-09-30"))
        self.assertEqual((r.task, r.due), ("circulate the offsite summary", d("2026-10-01")))

    def test_first_preposition_anchored_date_wins(self):
        r = extract_due("run the dry-run migration on staging by 2026-09-22", d("2026-09-17"))
        self.assertEqual((r.task, r.due), ("run the dry-run migration on staging", d("2026-09-22")))


class MeetingDate(unittest.TestCase):
    def test_formats(self):
        for text in ("2026-09-09", "9 September 2026", "Wednesday, 9 September 2026", "September 9, 2026"):
            self.assertEqual(parse_meeting_date(text).value, d("2026-09-09"), text)

    def test_rejected(self):
        for text in ("sometime last week", "09/10/2026", "", "2026-13-40", "next Monday"):
            self.assertIsNone(parse_meeting_date(text), text)

    def test_weekday_mismatch_is_flagged_not_rejected(self):
        r = parse_meeting_date("Tuesday, 14 September 2026")  # actually a Monday
        self.assertTrue(r.weekday_mismatch)
        self.assertFalse(parse_meeting_date("Monday, 14 September 2026").weekday_mismatch)


if __name__ == "__main__":
    unittest.main()
