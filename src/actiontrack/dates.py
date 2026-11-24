"""Date parsing and relative-date resolution (SPEC 5.1, 5.3, 5.4)."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
WD = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WEEKDAYS = {w: i for i, w in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}


def _mon(word: str) -> int:
    return _MONTHS[word.lower()[:3]]


def _wd(word: str) -> int:
    return _WEEKDAYS[word.lower()[:3]]


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# Patterns used for resolving an expression (named groups) -------------------------------
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DM = re.compile(rf"(?:{WD}\.?,?\s+)?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{MONTH})\.?(?:,?\s+(?P<y>\d{{4}}))?")
_MD = re.compile(rf"(?:{WD}\.?,?\s+)?(?P<m>{MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{{4}}))?")
_EOW = re.compile(r"end\s+of\s+(?:the\s+)?next\s+week")
_EOM = re.compile(r"end\s+of\s+(?:the\s+)?month")
_MID = re.compile(rf"mid[-\s]+(?P<m>{MONTH})")
_NEXT_WD = re.compile(rf"next\s+(?P<w>{WD})")
_EOD_TOM = re.compile(r"eod\s+tomorrow")
_WD_ONLY = re.compile(rf"(?P<w>{WD})")

# Unnamed pattern used for *finding* an expression inside a sentence ---------------------
_EXPR = "|".join(
    [
        r"\d{4}-\d{2}-\d{2}",
        rf"(?:{WD}\.?,?\s+)?\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\.?(?:,?\s+\d{{4}})?",
        rf"(?:{WD}\.?,?\s+)?{MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?",
        r"end\s+of\s+(?:the\s+)?next\s+week",
        r"end\s+of\s+(?:the\s+)?month",
        rf"mid[-\s]+{MONTH}",
        rf"next\s+{WD}",
        r"eod\s+tomorrow",
        r"eod",
        r"tomorrow",
        r"today",
        WD,
    ]
)
_PREP_EXPR = re.compile(
    rf"(?<![\w-])(?:by|due|on|from|for|until|before)\s+(?:the\s+)?(?P<expr>{_EXPR})(?![\w])", re.IGNORECASE
)
_BARE_TOMORROW = re.compile(r"(?<![\w-])(?:eod\s+)?tomorrow[\s.!]*$", re.IGNORECASE)


def next_weekday(base: date, wd: int) -> date:
    """Next occurrence of weekday ``wd`` strictly after ``base`` (same weekday -> +7)."""
    return base + timedelta(days=(wd - base.weekday()) % 7 or 7)


def resolve_expr(expr: str, base: date) -> date | None:
    """Resolve one date expression relative to ``base`` (the meeting date)."""
    e = " ".join(expr.strip().lower().split())
    m = _ISO.fullmatch(e)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    for rx in (_DM, _MD):
        m = rx.fullmatch(e)
        if m:
            return _safe_date(int(m["y"]) if m["y"] else base.year, _mon(m["m"]), int(m["d"]))
    if _EOW.fullmatch(e):
        next_monday = base - timedelta(days=base.weekday()) + timedelta(days=7)
        return next_monday + timedelta(days=4)
    if _EOM.fullmatch(e):
        return date(base.year, base.month, calendar.monthrange(base.year, base.month)[1])
    m = _MID.fullmatch(e)
    if m:
        return date(base.year, _mon(m["m"]), 15)
    m = _NEXT_WD.fullmatch(e)
    if m:
        next_monday = base - timedelta(days=base.weekday()) + timedelta(days=7)
        return next_monday + timedelta(days=_wd(m["w"]))
    if _EOD_TOM.fullmatch(e) or e == "tomorrow":
        return base + timedelta(days=1)
    if e in ("eod", "today"):
        return base
    m = _WD_ONLY.fullmatch(e)
    if m:
        return next_weekday(base, _wd(m["w"]))
    return None


@dataclass(frozen=True)
class DueResult:
    task: str  # task text with the due phrase removed
    due: date | None
    raw: str | None  # the matched phrase, e.g. "by Friday"


def _clean(task: str) -> str:
    task = " ".join(task.split())
    return task.strip(" \t-—–,;:").strip()


def extract_due(text: str, base: date) -> DueResult:
    """Find the first preposition-anchored date expression and remove it from the task."""
    for m in _PREP_EXPR.finditer(text):
        due = resolve_expr(m["expr"], base)
        if due is not None:
            task = _clean(text[: m.start()] + " " + text[m.end():])
            return DueResult(task, due, m.group(0))
    m = _BARE_TOMORROW.search(text)
    if m:
        due = resolve_expr(m.group(0).strip(" .!"), base)
        return DueResult(_clean(text[: m.start()]), due, m.group(0).strip())
    return DueResult(_clean(text), None, None)


# Meeting dates ---------------------------------------------------------------------------
_LEAD_WD = re.compile(rf"^(?P<w>{WD})\.?,?\s+(?P<rest>.+)$", re.IGNORECASE)
_MEET_DM = re.compile(rf"(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{MONTH})\.?,?\s+(?P<y>\d{{4}})", re.IGNORECASE)
_MEET_MD = re.compile(rf"(?P<m>{MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})", re.IGNORECASE)


@dataclass(frozen=True)
class MeetingDate:
    value: date
    weekday_mismatch: bool = False


def parse_meeting_date(text: str) -> MeetingDate | None:
    """Accepts ISO, 'D Month YYYY', 'Month D, YYYY', each with an optional leading weekday."""
    s = text.strip().strip("*_`'\"").strip()
    if not s:
        return None
    weekday = None
    m = _LEAD_WD.match(s)
    if m:
        weekday = _wd(m["w"])
        s = m["rest"].strip()
    value = None
    iso = _ISO.fullmatch(s)
    if iso:
        value = _safe_date(int(iso[1]), int(iso[2]), int(iso[3]))
    else:
        for rx in (_MEET_DM, _MEET_MD):
            mm = rx.fullmatch(s)
            if mm:
                value = _safe_date(int(mm["y"]), _mon(mm["m"]), int(mm["d"]))
                break
    if value is None:
        return None
    return MeetingDate(value, weekday is not None and weekday != value.weekday())
