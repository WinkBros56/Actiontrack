# actiontrack

Reads markdown meeting notes, tracks the action items, and drafts follow-up emails for review. **It never sends email.**
Full rules and acceptance criteria: [SPEC.md](SPEC.md).

Python 3.11+ (built on 3.14), standard library only.

## Run

From this folder (no install needed):

```powershell
.\actiontrack.cmd ingest .\tests\fixtures\notes
.\actiontrack.cmd list --status overdue
.\actiontrack.cmd done 12
.\actiontrack.cmd followup --all --dry-run
```

or `$env:PYTHONPATH = "src"; python -m actiontrack <command>`. With `pip install -e .` you also get a bare `actiontrack` command.

Global flags (before or after the command): `--db FILE` (default `./actiontrack.db`, or `$ACTIONTRACK_DB`),
`--people FILE` (default `./people.json`), `--today YYYY-MM-DD` (or `$ACTIONTRACK_TODAY`; handy for demos and tests).

## Commands

| Command | What it does |
|---|---|
| `ingest <folder>` | Parses every `*.md` directly in the folder. Skips and reports malformed notes; safe to re-run. |
| `list [--owner X] [--status open\|done\|overdue] [--due-before YYYY-MM-DD] [--json]` | Shows items, soonest due first. `--owner unassigned` / `--owner ambiguous` list the unowned / unresolved ones. |
| `done <id>` | Marks one item done. Sticky across re-ingest unless a *later* meeting note lists it as open again. |
| `followup <owner> \| --all [--dry-run] [--sink json\|mcp] [--out FILE]` | One email per owner covering open and overdue items. |

Exit codes: `0` ok (even if some notes were skipped), `1` runtime error (missing folder, unknown id, sink failure), `2` usage error (bad date/status, ambiguous owner).

## Before first use: fill in `people.json`

It is the roster: canonical names, emails and aliases (`"Priya N."`). The notes contain no email addresses and the tool
never invents any. An owner without an email still gets a draft, with an empty To and a warning.
Two people with the same first name (Priya Nair / Priya Shah, Rahul Mehta / Rahul Verma) are resolved from initials,
the roster, then the meeting's attendee list. If it is still unclear the item is marked `Priya (?)`, listed in the ingest
report, and left out of follow-ups; fix it by editing the note or adding an alias, then re-ingest.

## Follow-up drafts and Gmail

`followup` produces plain-text drafts shaped for the Gmail connector's `create_draft` tool
(`to`, `subject`, `body`, `includeSignature`). Two ways to get them into Gmail:

1. **`--sink json` (default).** Prints (or `--out`) a JSON file of drafts. Hand it to a Claude session that has the Gmail
   connector and ask it to call `create_draft` for each `create_draft` object. This is the path that works today.
2. **`--sink mcp`.** actiontrack acts as an MCP stdio client and calls one tool on a Gmail MCP server that you run
   locally. Copy [actiontrack.config.example.json](actiontrack.config.example.json) to `actiontrack.config.json` and edit it.
   Any configured tool whose name contains "send" is refused. This path is tested against a fake server only; it has
   **not** been run against a real Gmail server.

`--dry-run` prints the emails and creates nothing. Each real run is logged in the database; running it twice for the same
owner on the same day warns but does not block.

## Tests

```powershell
python -m unittest discover -s tests
```

87 tests, including the SPEC acceptance criteria against `tests/fixtures/notes` (a verbatim copy of the 36 sample files).
