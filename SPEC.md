# actiontrack — Specification (v1)

Status: **APPROVED and implemented (v0.1.0).** Deviations discovered while building are listed in section 12.
Author: Coditas engineering. Method: spec-driven; every rule below came from a grilling round and is testable.

## 1. Purpose

Meeting notes pile up as markdown. Action items get lost and follow-ups are late. `actiontrack` is a small CLI that
parses notes, tracks action items, and drafts (never sends) follow-up emails per owner.

## 2. Stack and layout

- Python 3.14, **standard library only** at runtime (`argparse`, `sqlite3`, `datetime`, `json`, `re`, `difflib`).
- Dev dependency: `pytest`.
- Console script `actiontrack` via `pyproject.toml`; also `python -m actiontrack`.
- Repo layout:
  ```
  actiontrack/
    SPEC.md  README.md  pyproject.toml  people.json
    src/actiontrack/  cli.py parser.py dates.py people.py dedupe.py store.py report.py email.py sinks.py
    tests/            unit tests + tests/fixtures/notes/ (verbatim copy of the 36 sample files)
  ```
- Storage: SQLite file, default `./actiontrack.db`; override with `--db` or `ACTIONTRACK_DB`.
- Global flags: `--db`, `--people <file>` (default `./people.json`), `--today YYYY-MM-DD` (env `ACTIONTRACK_TODAY`).
- Exit codes: `0` success (including runs that skipped bad notes), `1` runtime error (missing folder, unknown id, DB error),
  `2` usage error (bad date, bad `--status`, ambiguous `--owner`).

## 3. Roster (`people.json`)

Source of truth for identity and email. Seeded with the 9 people seen in the notes; **emails left blank for the user to fill**.
We never invent email addresses.

```json
{ "people": [
  {"name": "Priya Nair", "email": "", "aliases": ["Priya N."]},
  {"name": "Priya Shah", "email": "", "aliases": ["Priya S."]}
] }
```

An owner with a blank or invalid email still gets a draft, with an empty `To`, flagged in output.

## 4. Commands

### 4.1 `actiontrack ingest <folder>`
Parses every `*.md` (case-insensitive) **directly in** the folder (no recursion). Non-`.md` files are ignored silently.
Idempotent; prints a summary: files parsed, files skipped (file + reason), warnings, merges performed, possible duplicates,
ambiguous owners. One bad file never aborts the run.

### 4.2 `actiontrack list [--owner X] [--status open|done|overdue] [--due-before YYYY-MM-DD] [--json]`
- Default: **all** items, sorted by due date ascending (no-due last), then id.
- Columns: id, status, owner, task, due, source (latest source file).
- Filters combine with AND.
- `--owner`: case-insensitive match on roster name, alias, or unique first name. An ambiguous `--owner Priya` exits 2
  and lists the candidates. `--owner unassigned` lists owner-less items.
- `--due-before`: strictly before the given ISO date; items with no due date are excluded.
- Status: `done`; else `overdue` if `due < today`; else `open`. Overdue is **computed at read time**, never stored.
  An item due today is open.

### 4.3 `actiontrack done <id>`
Marks exactly one item done (manual, timestamped, sticky — see 6.3). Already-done: message, exit 0. Unknown id: error, exit 1.

### 4.4 `actiontrack followup <owner> | --all [--dry-run] [--sink json|mcp] [--out FILE]`
Builds **one email per owner** covering their open and overdue items (overdue first). `<owner>` resolved like `--owner`.
`--all` = every owner with at least one open/overdue item. Owners with nothing open are skipped with a message.
Unassigned and ambiguous-owner items are never emailed; they are summarised in the command output instead.

**Never sends.** No code path calls a send API. See section 7.

## 5. Parsing rules

### 5.1 Is a note valid?
Valid only if it has a parseable **meeting date**. Missing attendees is a warning only. Skipped notes are reported with
reason. No fallback to filename date or file mtime.

Meeting date sources, in order: YAML front matter `date:`, then a `Date:` line. Accepted formats: ISO `YYYY-MM-DD`,
`D Month YYYY`, optional leading weekday (`Monday, 14 September 2026`), `Month D, YYYY`. Numeric `09/10/2026` is rejected
(ambiguous). A weekday contradicting the date is a warning.

Attendees: `Attendees:` comma-separated line, or YAML list. Matched to the roster; unknown names kept with a warning.

### 5.2 What counts as an action item
Included:
- Checkbox lines with bullet `-`, `*` or `+`; box `[ ]`, `[x]`, `[X]`; **in any section** (not only "Action items").
- Nested checkboxes — each becomes its own item, not linked to its parent.
- Markdown table rows under a header containing Owner / Action / Due.
- Lines beginning `ACTION:`.

Excluded: blockquoted lines (`>`), fenced code blocks, HTML comments, and anything not matching the above.

### 5.3 Owner / task / due extraction
- **Owner**: text before the first ` to ` or `:` of the item, with `@` stripped (`Priya N.`, `@anita:`).
- **Multiple owners** (`Anita & Rahul to …`): one item per owner, same task text, each tracked and emailed.
- **Helper** (`Lena (with Priya) to …`): Lena is the owner; Priya stays in the task text and is not an owner.
- **No owner** (`Someone`, `TBD`, `Team`, `Everyone`, or unparseable): owner = null, shown `(unassigned)`.
  `Everyone` additionally flagged `group`; it is **not** fanned out to attendees.
- **Due**: the first date expression preceded by `by`, `due`, `on`, `from`, `for`, `until` or `before`
  (`tomorrow` / `EOD` count after one of these). The date text is removed from the task. `due_raw` keeps the original text.
  Unparseable or absent → `due = null`.

### 5.4 Resolving relative dates (base = meeting date)
| Phrase | Resolves to |
|---|---|
| `by Friday`, `by Wed` | next such weekday **strictly after** the meeting date (meeting on that weekday → +7 days) |
| `next Tuesday` | that weekday in the **following calendar week** |
| `tomorrow`, `EOD tomorrow` | meeting date + 1 |
| `EOD`, `by EOD` | meeting date |
| `end of next week` | Friday of next week |
| `end of month` | last calendar day of the month |
| `mid-October` | the 15th |
| `10 Sep`, `Oct 3rd`, `2 Oct` (no year) | meeting's year |
| `from 9 Oct` | due = 9 Oct |
| `Saturday 10 Oct`, `Wed 16 Sep` | the explicit date wins over the weekday word |

### 5.5 Resolving owner identity
In order: (1) explicit initial/surname matches roster (`Priya N.`); (2) first name unique in the **roster**;
(3) exactly one matching person among the meeting's attendees; (4) otherwise **ambiguous**: stored as `Priya (?)`
with candidates recorded, shown in `list`, excluded from `followup`, reported as a warning. We never guess.
Names not in the roster are kept as-is with a warning. Fixing ambiguity = add aliases to `people.json` or edit the note,
then re-ingest. There is no fifth command.

## 6. Data semantics

### 6.1 Duplicates
Two mentions are the same item if they have the same resolved owner and the same **normalized task** (lowercase, strip
articles/punctuation/due phrase), compared by token similarity. Similarity ≥ 0.85 → merge. 0.5 ≤ similarity < 0.85 →
**not merged**, listed under "possible duplicates" in the ingest report. A merged item keeps one id, keeps all source files,
and takes the **latest meeting's due date**; if that mention has no due date, the most recent non-null due date is kept
(*assumption A1*). All merges are printed.

### 6.2 Status from notes
Status comes from the latest-dated mention (same-day tie: `[x]` wins). A later `[x]` closes the item; a later `[ ]` after
an earlier `[x]` reopens it.

### 6.3 Ids, re-ingest, manual done
- Integer ids assigned at first sight, keyed by owner + normalized task; never reused or renumbered. On an empty DB items are
  processed in (meeting date, filename, line) order so ids are deterministic.
- Files tracked by path + content hash; a changed file has its mentions replaced; files that vanish are left alone.
- Manual `done` records a timestamp and is sticky: a note mention reopens it only if that meeting's date is **later than
  the manual-done date**.

## 7. Follow-up emails and Gmail

Plain text. Subject: `Action items follow-up: <N> open (<M> overdue)`. Body: `Hi <First>,`, then sections
**Overdue**, **Open**, **No due date** (omitted when empty). Bullet format:
`[#id] task — due 2026-09-18 (67 days overdue) — from <meeting title>, <meeting date>`.
Each run is logged in a `followups` table (owner, timestamp, item ids, draft id). A second draft for the same owner on the
same day **warns but does not block**.

Draft payload matches the Gmail connector's `create_draft` tool (verified against its schema this session):
```json
{"to": ["priya@example.com"], "subject": "...", "body": "...(plain text, no Markdown)...", "includeSignature": true}
```
`includeSignature: true` lets Gmail append the user's own signature (*assumption A2*, replaces the earlier "no signature" idea).

Sinks (`DraftSink` interface):
- **`json`** (default): writes all drafts to stdout / `--out FILE`. A Claude session with the Gmail connector then calls
  `create_draft` for each. This is the path usable today, because the Gmail connector lives inside the Claude session and a
  standalone CLI cannot call it directly.
- **`mcp`**: MCP stdio client using `actiontrack.config.json` (server command, tool name, argument mapping, default
  `{to, subject, body, includeSignature}`). **Safety guard:** refuses any tool whose name contains "send"; only a
  create-draft tool is callable. **Not verified against a live server in v1.**
- `--dry-run`: render and print drafts; create nothing.

## 8. Acceptance criteria (against the 36 sample files; `--today 2026-11-24` unless stated)

**Ingest**
- AC1. 35 `.md` files examined; 31 parsed; 4 skipped with reasons: `2026-09-09-notes-final-v2.md` (empty),
  `2026-10-03-sales-pipeline.md` (date "sometime last week"), `scratch.md` (binary), `untitled.md` (no date).
  `2026-09-19-offsite-draft.txt` is ignored. Exit code 0.
- AC2. Re-running ingest on the same folder changes nothing (item count, ids, statuses identical).

**Traps and formats**
- AC3. `2026-09-21`: the blockquoted "Karan to refund … by Friday" yields **no** item; the real "Karan to refund affected customers"
  yields exactly one (no due date).
- AC4. `2026-10-09`: no item from the fenced `Owner to remediate finding` and none for "buy hardware tokens" (HTML comment).
- AC5. `2026-09-30-q4-offsite`: yields items for `-`/`*`/`+` checkboxes, the nested Karan item (due 2026-10-05), the table row
  (Anita, brand kit, due 2026-10-09) and the `ACTION:` line (Karan, offsite summary, due 2026-10-01).
- AC6. `[X]` (2026-10-02) is treated as done.

**Dates**
- AC7. 2026-09-01 "by Friday" → 2026-09-04. 2026-09-18 "by Mon" → 2026-09-21. 2026-09-03 "by end of next week" → 2026-09-11.
  2026-09-21 "EOD tomorrow" → 2026-09-22. 2026-10-05 "by next Tuesday" → 2026-10-13. 2026-09-14 "end of month" → 2026-09-30.
  2026-09-30 "mid-October" → 2026-10-15; "Oct 3rd" → 2026-10-03; "results due 15 October" → 2026-10-15.
  2026-09-29 "from 9 Oct" → 2026-10-09; "Saturday 10 Oct" → 2026-10-10. 2026-09-14 "Wed 16 Sep" → 2026-09-16.
- AC8. Unit test: meeting on a Friday saying "by Friday" → +7 days.

**Names / ownership**
- AC9. Resolved: 2026-09-01 "Priya" → Priya Nair (only Priya attending). 2026-09-03 "Priya" → Priya Shah, "Rahul" → Rahul Verma.
  "Priya N." → Nair, "Priya S." → Shah.
- AC10. Ambiguous (owner `(?)`, excluded from followup): 2026-09-17 "Priya to sign off the cutover checklist"; 2026-09-30 "Priya to own the
  hiring plan" and "Rahul to prepare the architecture overview"; 2026-10-07 "Rahul to review Anita's PR"; 2026-09-23 "Rahul to rebase …"
  (no attendees; roster has two Rahuls); 2026-10-08 "Priya to circulate the go/no-go summary".
- AC11. 2026-09-23 "Sam to rotate the staging credentials" resolves to Sam Okafor despite no attendee list.
- AC12. Unassigned: "Someone to update the CRM notes", "TBD to write the design-system changelog", "Team to decide on the launch date",
  "Everyone to review the prototype", "Everyone to submit their Q4 goals" (flagged `group`).
- AC13. 2026-09-18 "Anita & Rahul to agree on the component API" → two items (Anita Desai, Rahul Mehta), due 2026-09-23.
  "Lena (with Priya)" → owner Lena only.

**Duplicates and status**
- AC14. "Send the revised quote to Acme" (09-01, 09-08 as "Priya N.", 09-15 `[x]`) → one item, owner Priya Nair, **done**.
- AC15. "Confirm cloud budget with finance" (09-01, 09-15, 09-22 `[x]`) → one item, done. Staging environment (09-01, 09-08 `[x]`) → done.
- AC16. "Fix the pagination bug" (09-15 no date, 09-22 "by Wednesday") → one item, open, due 2026-09-23.
- AC17. "Share updated wireframes" (09-15, 09-22) → one item; "share first wireframes" (09-01) is **not** merged and appears under
  possible duplicates.
- AC18. "Collect 5 customer quotes" (09-14, 09-24) merge; "publish the customer quotes" (10-05) does not merge with it.
- AC19. "Freeze invoice edits from 9 Oct" (09-29, 10-08) → one open item, due 2026-10-09. "Notify customers about the maintenance window"
  (09-29 Priya N., 10-08 `[x]`) → one item, done.
- AC20. "Review the schema mapping doc" (09-03 due 2026-09-07; 09-17 no date) → one item, due 2026-09-07 (assumption A1).

**Commands**
- AC21. `--today 2026-10-01`: an item due 2026-10-01 is open; due 2026-09-30 is overdue. `list --status overdue` excludes every done item.
- AC22. `list --due-before 2026-10-01` excludes no-due items and items due on 2026-10-01.
- AC23. `list --owner Priya` exits 2 listing Priya Nair and Priya Shah; `--owner "Priya Nair"` and `--owner "Priya N."` work.
- AC24. `done <id>` on an open item → done; again → exit 0 with a message; `done 99999` → exit 1. After `done`, re-ingesting the notes does not reopen it.
- AC25. `followup "Karan Joshi" --dry-run` prints one email with Overdue before Open, items sorted by due date, no ambiguous or
  unassigned items, and creates nothing. `followup --all` yields one draft per owner with open/overdue items.
- AC26. Owner with blank email → draft has `to: []` and a warning. No code path invokes a send tool; the `mcp` sink rejects tool names containing "send".

## 9. Test strategy (TDD)
1. Unit tests first for `dates.py`, `people.py` (identity), and the line parser, using the tables above.
2. Integration test: ingest the fixture copy of the 36 files, assert AC1–AC24.
3. `followup` tests use a fake sink; the safety-guard test asserts the MCP adapter refuses a send tool.
4. Live Gmail/MCP is out of the test suite and flagged unverified.

## 10. Assumptions to confirm
- **A1** When the latest duplicate mention has no due date, keep the most recent non-null due date.
- **A2** Drafts set `includeSignature: true`.
- **A3** Owner identity step "first name unique in roster" runs before attendee matching (needed for AC11).
- **A4** Same-day status tie goes to `[x]`.
- **A5** The `followups` log is informational only (warn on same-day repeat, never block).
- **A6** `git init` is not run unless you ask; no commits are made.

## 11. Out of scope for v1
Sending email, recursion into subfolders, a command to edit/reassign items, fan-out of "Everyone", GUI, non-UTF-8 notes,
live Gmail verification.

## 12. Deviations and additions made during the build
1. **Tests use stdlib `unittest`, not `pytest`** (pytest was not installed and installing it would download a package; the
   tests also run under pytest). Run: `python -m unittest discover -s tests`.
2. **Bare trailing `tomorrow`** (no `by`) is accepted as a due date. Needed for AC5: `ACTION: Karan to circulate the offsite
   summary tomorrow` has no preposition. Section 5.3 otherwise unchanged.
3. **`--owner ambiguous`** added beside `--owner unassigned`, so ambiguous items stay filterable (they cannot be reached with
   `--owner Priya`, which exits 2).
4. **`--sink json` output shape:** `{"drafts":[{"owner","item_ids","warnings","create_draft":{to,subject,body,includeSignature}}]}`;
   only the `create_draft` object is passed to the Gmail tool.
5. **Subject count:** `N open (M overdue)` where N counts every open-or-overdue item, i.e. M is a subset of N.
6. **Owner identity** also handles names missing from the roster: they resolve from the meeting's attendee list when
   unambiguous, otherwise are kept as written with a warning.
7. **Known cosmetic limit:** when the due phrase is mid-sentence (`..., results due 15 October`) the leftover words stay in the
   task (`run the customer survey, results`).
8. **Id stability when ambiguity is fixed:** if an unresolved item gets an owner after a roster or note edit, it keeps its id
   (matched on task text).
9. `people.json` also lists `Rahul M.` / `Rahul V.` aliases. A launcher `actiontrack.cmd` is provided; `pip install -e .`
   was not run (it needs to download build tooling).
10. Acceptance criteria AC1–AC26 are all automated in `tests/test_acceptance.py` (plus `test_dates`, `test_people`,
    `test_parser`, `test_sinks`). The `mcp` sink is verified only against a fake MCP server (`tests/fake_mcp_server.py`).
