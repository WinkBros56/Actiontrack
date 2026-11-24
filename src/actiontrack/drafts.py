"""Follow-up email composition (SPEC 7). Plain text only; no Markdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .people import Person
from .store import Item


@dataclass
class Draft:
    owner: str
    to: list[str]
    subject: str
    body: str
    include_signature: bool = True
    item_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def create_draft_args(self) -> dict:
        """Arguments for the Gmail connector's create_draft tool."""
        return {"to": self.to, "subject": self.subject, "body": self.body,
                "includeSignature": self.include_signature}


def _line(item: Item, today: date) -> str:
    parts = [f"[#{item.id}] {item.task}"]
    if item.due is not None:
        text = f"due {item.due.isoformat()}"
        if item.due < today:
            n = (today - item.due).days
            text += f" ({n} day{'s' if n != 1 else ''} overdue)"
        parts.append(text)
    src = item.latest_title or item.latest_source or "meeting notes"
    parts.append(f"from {src}, {item.latest_mention_date.isoformat()}")
    return "  " + " — ".join(parts)


def _sorted(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda i: (i.due or date.max, i.id))


def build_draft(owner: str, person: Person | None, items: list[Item], today: date) -> Draft:
    """One email for one owner covering their open and overdue items (overdue first)."""
    overdue = _sorted([i for i in items if i.due is not None and i.due < today])
    open_ = _sorted([i for i in items if i.due is not None and i.due >= today])
    no_due = _sorted([i for i in items if i.due is None])
    first = person.first_display if person else owner.split()[0]

    sections = []
    for title, group in (("Overdue", overdue), ("Open", open_), ("No due date", no_due)):
        if group:
            sections.append(f"{title} ({len(group)})\n" + "\n".join(_line(i, today) for i in group))
    body = (f"Hi {first},\n\nHere are your outstanding action items from recent meetings.\n\n"
            + "\n\n".join(sections)
            + "\n\nPlease reply or tell me when any of these are done or need a new date.\n\nThanks")
    warnings: list[str] = []
    to: list[str] = []
    if person is not None and person.has_valid_email:
        to = [person.email.strip()]
    else:
        warnings.append(f"no valid email on file for {owner}; draft has an empty To")
    total = len(overdue) + len(open_) + len(no_due)
    subject = f"Action items follow-up: {total} open ({len(overdue)} overdue)"
    return Draft(owner, to, subject, body, True, [i.id for i in overdue + open_ + no_due], warnings)


def render_text(d: Draft) -> str:
    return f"To: {', '.join(d.to) or '(none)'}\nSubject: {d.subject}\n\n{d.body}"
