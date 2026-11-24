import unittest

from helpers import PEOPLE  # noqa: F401
from actiontrack.people import Roster, resolve_owner

NAIR_SHAH = ["Priya Nair", "Priya Shah"]
ROSTER = Roster.load(PEOPLE)


class ResolveOwner(unittest.TestCase):
    def test_alias_and_initial(self):
        self.assertEqual(resolve_owner("Priya N.", ROSTER, NAIR_SHAH).name, "Priya Nair")
        self.assertEqual(resolve_owner("Priya S.", ROSTER, NAIR_SHAH).name, "Priya Shah")
        self.assertEqual(resolve_owner("priya shah", ROSTER, []).name, "Priya Shah")

    def test_unique_roster_first_name_needs_no_attendees(self):  # AC11
        r = resolve_owner("Sam", ROSTER, [])
        self.assertEqual((r.kind, r.name), ("person", "Sam Okafor"))
        self.assertEqual(resolve_owner("anita", ROSTER, []).name, "Anita Desai")

    def test_shared_first_name_uses_attendees(self):
        self.assertEqual(resolve_owner("Priya", ROSTER, ["Priya Nair", "Sam Okafor"]).name, "Priya Nair")
        self.assertEqual(resolve_owner("Rahul", ROSTER, ["Rahul Verma", "Anita Desai"]).name, "Rahul Verma")

    def test_both_present_is_ambiguous(self):
        r = resolve_owner("Priya", ROSTER, ["Priya Nair", "Priya Shah"])
        self.assertEqual((r.kind, r.name, r.candidates), ("ambiguous", "Priya (?)", ("Priya Nair", "Priya Shah")))

    def test_no_attendees_is_ambiguous_for_shared_first_name(self):
        self.assertEqual(resolve_owner("Rahul", ROSTER, []).kind, "ambiguous")

    def test_unknown_name_kept_as_is(self):
        r = resolve_owner("Zed", ROSTER, [])
        self.assertEqual((r.kind, r.name), ("unknown", "Zed"))

    def test_attendee_derived_when_roster_is_empty(self):
        empty = Roster([])
        self.assertEqual(resolve_owner("Sam", empty, ["Sam Okafor", "Anita Desai"]).name, "Sam Okafor")
        self.assertEqual(resolve_owner("Priya", empty, ["Priya Nair", "Priya Shah"]).kind, "ambiguous")


class RosterFind(unittest.TestCase):
    def test_find(self):
        self.assertEqual(ROSTER.find("Priya N.")[0], "one")
        self.assertEqual(ROSTER.find("Priya Nair")[0], "one")
        self.assertEqual(ROSTER.find("Priya")[0], "many")
        self.assertEqual(ROSTER.find("Nobody Here")[0], "none")
        self.assertEqual(ROSTER.find("karan")[1][0].name, "Karan Joshi")


if __name__ == "__main__":
    unittest.main()
