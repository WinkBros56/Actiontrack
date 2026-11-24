"""Roster and owner identity resolution (SPEC 3, 5.5)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def norm(s: str) -> str:
    """Case-fold, drop periods, collapse whitespace."""
    return " ".join(s.casefold().replace(".", " ").split())


@dataclass(frozen=True)
class Person:
    name: str
    email: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def first(self) -> str:
        return norm(self.name).split()[0] if self.name.strip() else ""

    @property
    def surname(self) -> str:
        return " ".join(norm(self.name).split()[1:])

    @property
    def first_display(self) -> str:
        return self.name.split()[0] if self.name.split() else self.name

    @property
    def has_valid_email(self) -> bool:
        return bool(_EMAIL.match(self.email.strip()))

    def matches_text(self, text: str) -> bool:
        """True if ``text`` is this person's full name or one of their aliases."""
        t = norm(text)
        return t == norm(self.name) or t in {norm(a) for a in self.aliases}


@dataclass
class Resolution:
    kind: str  # 'person' | 'ambiguous' | 'unknown'
    name: str  # canonical name, '<First> (?)' for ambiguous, raw text for unknown
    candidates: tuple[str, ...] = ()
    person: Person | None = None


@dataclass
class Roster:
    people: list[Person] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None) -> "Roster":
        if path is None or not Path(path).is_file():
            return cls([], [f"roster file not found ({path}); owners resolved from notes only"] if path else [])
        warnings: list[str] = []
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            entries = data["people"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return cls([], [f"roster file {path} unreadable: {exc}"])
        people: list[Person] = []
        for e in entries:
            if not isinstance(e, dict) or not str(e.get("name", "")).strip():
                warnings.append(f"roster entry skipped (no name): {e!r}")
                continue
            email = str(e.get("email", "") or "").strip()
            p = Person(str(e["name"]).strip(), email, tuple(str(a) for a in e.get("aliases", []) or []))
            if email and not p.has_valid_email:
                warnings.append(f"roster: invalid email for {p.name!r}: {email!r} (treated as missing)")
            people.append(p)
        return cls(people, warnings)

    def by_name(self, name: str) -> Person | None:
        for p in self.people:
            if norm(p.name) == norm(name):
                return p
        return None

    # -- lookups ----------------------------------------------------------------------
    def _first_name_matches(self, first: str) -> list[Person]:
        return [p for p in self.people if p.first == first]

    @staticmethod
    def _rest_matches(p: Person, rest: str) -> bool:
        if not rest:
            return True
        if len(rest) == 1:  # initial
            return p.surname.startswith(rest)
        return p.surname == rest

    def find(self, query: str) -> tuple[str, list[Person]]:
        """Lookup for CLI filters. Returns ('one'|'many'|'none', people)."""
        q = norm(query)
        if not q:
            return "none", []
        exact = [p for p in self.people if p.matches_text(query)]
        if len(exact) == 1:
            return "one", exact
        toks = q.split()
        cands = self._first_name_matches(toks[0])
        if len(toks) > 1:
            cands = [p for p in cands if self._rest_matches(p, " ".join(toks[1:]))]
        if len(cands) == 1:
            return "one", cands
        if len(cands) > 1:
            return "many", cands
        return "none", []


def resolve_owner(raw: str, roster: Roster, attendees: list[str] | tuple[str, ...] = ()) -> Resolution:
    """SPEC 5.5: alias/surname/initial, then unique roster first name, then attendees, else ambiguous."""
    raw = raw.strip()
    q = norm(raw)
    if not q:
        return Resolution("unknown", raw)
    toks = q.split()

    # (1) alias / full name / initial / surname
    exact = [p for p in roster.people if p.matches_text(raw)]
    if len(exact) == 1:
        return Resolution("person", exact[0].name, person=exact[0])
    cands = roster._first_name_matches(toks[0])
    if len(toks) > 1:
        narrowed = [p for p in cands if roster._rest_matches(p, " ".join(toks[1:]))]
        if len(narrowed) == 1:
            return Resolution("person", narrowed[0].name, person=narrowed[0])
        if len(narrowed) > 1:
            return Resolution("ambiguous", f"{raw} (?)", tuple(p.name for p in narrowed))
        # no roster person fits: fall through to attendee-derived names
        cands = []
    # (2) first name unique in the roster
    elif len(cands) == 1:
        return Resolution("person", cands[0].name, person=cands[0])

    # (3) several roster people share the first name -> use attendees
    if len(cands) > 1:
        present = [p for p in cands if any(p.matches_text(a) for a in attendees)]
        if len(present) == 1:
            return Resolution("person", present[0].name, person=present[0])
        pool = present or cands
        return Resolution("ambiguous", f"{raw} (?)", tuple(p.name for p in pool))

    # (4) not in the roster at all: try the attendee list
    att = [a for a in attendees if norm(a).split() and norm(a).split()[0] == toks[0]
           and (len(toks) == 1 or Roster._rest_matches(Person(a), " ".join(toks[1:])))]
    att = list(dict.fromkeys(att))
    if len(att) == 1:
        return Resolution("person", att[0], person=Person(att[0]))
    if len(att) > 1:
        return Resolution("ambiguous", f"{raw} (?)", tuple(att))
    return Resolution("unknown", raw)
