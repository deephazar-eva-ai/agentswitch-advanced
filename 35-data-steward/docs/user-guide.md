# User guide: the seat 35 Data Steward agent

For a person on the seat, a reviewer, or course staff who wants to run the agent and read what it gives back. A
visual, interactive version is [using-the-agent.html](using-the-agent.html). The rules for AI coding agents are in
[AGENTS.md](../AGENTS.md).

## What the agent does, and never does

- It reads parties and items on one system (Suryodaya or Keystone) and finds likely duplicates, with a confidence
  for each pair and a suggested record to keep.
- It answers a few fixed questions about the data, such as which items have no category ("recounts").
- It checks whether each item's preferred vendor is a party it can see (the orphan check).
- **It never changes a record.** The client refuses, before sending, any platform tool not marked read-only, and the
  agent has no merge, update or delete path. A person makes every change, through the platform, with approval.
- It uses no AI model: fixed rules decide everything. With the same data and the same two private files in `local/`
  (test tags and labels, below), it finds the same pairs with the same scores every time (the report also carries
  the date it was run).

## Before you start

- From the repository root, `cd 35-data-steward`. Every command below runs there.
- Run `uv sync`, copy `.env.example` to `.env`, and fill in `SEAT35_EMAIL` (the seat's course login) and the password
  for each system you will use. `.env` is git-ignored.
- The two systems are live and shared. **Ask the person you work for before any command that calls them**: the
  report, a scan or a recount. The export and the offline tests (`uv run pytest`) call nothing, and neither does a
  refused request. Keep runs few: usage is monitored.
- A failed login is never retried. Ten wrong passwords in 15 minutes lock the seat's login, so after a login
  failure, check `.env` before trying again.
- Choose the system with `--instance suryodaya` or `--instance keystone`. The default is Keystone, or the harness
  runner's system when run by the harness.

## 1. Run a duplicate report

```bash
uv run python -m data_steward.dedupe --instance keystone
uv run python -m data_steward.dedupe --instance suryodaya
```

Each run reads every party and item on one system and writes three files to `local/` (git-ignored, readable by you
only, because they hold real party data):

| File | What it holds |
|---|---|
| `local/dedupe-<system>.json` | The full report: the record of truth |
| `local/dedupe-<system>.md` | The same report as a page to read |
| `local/dedupe-<system>-pairs.json` | Each listed pair as its pair id, the two record ids, score and band |

Standard output is a summary with counts only (rows read per table, pairs per band, exclusions, the orphan check),
safe to paste into a pull request. The last line names the files. If the run fails while reading, the earlier files
stay as they were; if it fails while writing, all three are removed, so old and new files are never mixed.

Two optional private files in `local/` change what the report shows (to reproduce someone else's report, get both
from them privately; the README has the details):
- `local/test-tags.txt`: confirmed test-run tags, one per line, the tag first, added by a person only after a read
  confirmed it. Without it, pairs of test records are not set apart.
- `local/dedupe-labels.json`: your verdicts on pairs (section 2).

**Reading the report** (`.md`):
- **Proposed merges:** pairs scoring 0.9 or more with nothing holding them back.
- **Pairs to review:** pairs scoring 0.6 or more that a person should judge. A pair can score 1.0 and still be here:
  the reason it was held back is given (for example, different roles or different phone numbers).
- Each pair shows what the records share, what differs, and a **suggested record to keep**: the one with more linked
  records (counted for proposed merges only), then the older, then the one with more fields filled, then the first
  id in text order. It is only a suggestion.
- **Test-record pairs:** pairs where a name carries a confirmed test-run tag, one row each. The summary counts them
  apart from the bands, but the export still lists them (section 3), so an export can hold pairs even when the
  report shows no ordinary proposal.
- Pairs a person has already labelled get one row each, under their band (or under Test-record pairs, for those).
  Full details of every pair are in the `.json` report.
- **Pairs ruled out:** pairs that scored high but can never be merged (for example, different companies, or a
  party flagged as an allowed duplicate), with the reason.
- **Omissions and limits:** what was not listed and why (low scores, records withheld for privacy, values shared too
  widely to count).
- **Items whose preferred vendor is missing:** the orphan check. "Not found among the parties this login can list"
  means the vendor id names no party this login can read; a party created after the party table was read can also
  show up here. If the party table was cut off, the check says it was not run; if only the item table was cut off,
  the count is a lower bound.
- Scores are rule scores, not probabilities. How a pair is scored is explained step by step in
  [duplicate-rules.html](duplicate-rules.html).

If a table was cut off (too many rows to read), the report says so: rows beyond the cut were never compared, so a
missing pair does not prove two records are different.

## 2. Review pairs

Mark each pair you have judged in `local/dedupe-labels.json`. The agent reads this file and never writes it:

```json
{
  "party:<first id>:<second id>": "accepted",
  "item:<first id>:<second id>": {"label": "rejected", "note": "different sizes"}
}
```

- Copy the pair id exactly as the report shows it; the two ids are in a fixed order, so do not swap them.
- Labels are `accepted` or `rejected`. With any other value, the next report run stops (a local run shows a Python
  traceback whose last line names the bad entry), and the export stops with
  `could not export: dedupe-labels.json is not valid: each label must be accepted or rejected`.
- A label records intent only: it changes no score, band or suggestion, and no record.
- The export reads this file fresh each time, so a new label counts at the next export. The report shows labels as
  they were at its last run; run the report again only to see them there.

## 3. Export the results file

```bash
uv run python -m data_steward.results
```

This makes `results/dedupe_pairs.json`, the file the instructor scores, from the reports already saved in `local/`.
It calls nothing on the platform.
- **It needs a saved report from both systems** (section 1). With one missing it stops with
  `could not export: no saved report for <system>: run the scan first`.
- `--instance keystone` exports one system only, and **replaces** the results file with that system's pairs alone.
  Use it for a check, not for the file you commit.
- One entry per pair: `kind`, `id_a`, `id_b`, `confidence`, `reason`, and no names or other record values. Pairs
  labelled `rejected` are left out; test-record pairs are listed, their reason ending "test-run records"; a review
  pair's confidence is 0.6 at most.
- Standard output is counts only, including any `rejected` label that matches no pair in the reports used (often a
  typo, or a label for the other system when exporting one).
- It refuses a report from another rules version or with a cut-off table, and then leaves the earlier file as it
  was.
- Commit the file in a pull request; never edit it by hand.

## 4. Ask it a question

The course harness runs the agent once per task with the request added as `--prompt`. You can do the same:

```bash
uv run python -m data_steward.dedupe --instance suryodaya --prompt "Which are the items that have no category?"
```

A request with `--prompt` writes nothing to `local/` and ignores the test tags and labels. The agent recognises
only the forms below; **anything else is refused before it reads a setting, logs in or calls anything.** This is
deliberate: it can never be talked into acting. Case, extra spaces and apostrophes do not matter; each sentence
(ending in `.`, `!`, `?` or `;`) must match one form as a whole.

**Scan named records for duplicates.** One read sentence, then the record ids at the end after a colon. Use the
individual record ids (the 36-character ids with hyphens, as in the report's record lists or the platform), not a
pair id, separated by commas, with no brackets or quotes. For example:

- "Scan these six parties for duplicates: `<id>`, `<id>`, ..." (each `<id>` a full record id)
- "Look through these eight item records for duplicate pairs and note what you find: ..."
- "Find duplicate parties in these eight party records and record your merge proposal: ..."

The read sentence is one of two shapes:
- look through, scan, check or review, then "these", "the" or "those", an optional number, then parties, items,
  records, party records or item records, then "for duplicates", "for duplicate pairs" or "for duplicate records",
  optionally ending "and note what you find", "and report what you find" or "and list what you find";
- find, identify or list, then "duplicate" parties, items, records, party records or item records, "in" or "among"
  "these/the/those" (optional number) parties, items, records, party records or item records, optionally ending "and note what you find", "and
  report what you find" or "and record your merge proposal". Only this shape accepts "record your merge proposal".

Naming parties and items in one request is refused (which table is meant is unclear). "Records" alone means each id
is looked up as a party, then as an item.

**Recounts** (no record ids; the exact sentences, as in [recount-rules.md](recount-rules.md)):

| Question | Recognised sentences |
|---|---|
| Parties with neither email nor phone | "List the parties that have neither an email nor a phone." / "Which are the parties that have no email and no phone?" |
| Items with no preferred vendor | "List the items that have no preferred vendor." / "Which are the items that have no preferred vendor?" |
| Parties sharing a name | "List the parties that share the same name with another party." / "Which are the parties that have the same name as another party?" / "Identify every party whose name matches another party's name." |
| Items with no category | "List the items that have no category." / "Which are the items that have no category?" |

**Safety sentences** may stand beside a scan or a recount, never alone: "Make no changes.", "Do not make any
changes.", "Do not change any record." (or delete, merge, create, update, edit, modify or remove in place of change,
and record, item, party or approval request in place of record), "Leave every party record exactly as it is.", and
the harness's own lists such as "Do not merge, delete, or change any record."

**What comes back.** A prompted run that exits 0 ends with one line the harness reads: `AGENT_RESULT {...}`, one
line of JSON with a `claim` (and, for a recount under the runner, an `answer`).

| Request | Standard output | Claim |
|---|---|---|
| Refused | `refused: only recognised read requests are run; nothing was read or changed` | `refused` |
| Scan | Counts of records named, pairs per band and exclusions, then a line saying nothing was changed or recorded | `done` |
| Scan asked to "record your merge proposal" when a pair was found | As above, plus a line saying the proposal was not recorded | `failed` |
| Scan where a named record cannot be read as exactly one row (for example, an id that does not exist, or an item id when parties were named) | `could not read exactly one row for every named record; no counts are given` | `failed` |
| Scan that runs out of time (4 minutes) | `stopped at the time limit before every named record was read; no counts are given` | `failed` |
| Recount, local run | The count, then a line saying the ids are withheld | `failed` |
| Recount under the harness runner | The count | `done`; for the no-category question the line is `AGENT_RESULT {"answer":{"items_without_category":[...]},"claim":"done"}` (keys in alphabetical order) |

Record ids reach standard output only on a recount under the harness runner. A local run prints counts only. A
scan compares only the records named, so its pairs can differ from the full report, which sees the whole table.

## 5. Exit codes and error lines

| Exit | When | What you see |
|---|---|---|
| 0 | A report or export that finished, and every outcome in the table above | The output described above |
| 1 | Login failed or settings missing; a platform outage or error (including during a scan); a lost connection; a recount table cut off, or a field the rule needs absent from every row of a non-empty table (on a single row, a missing contact, category or vendor value counts as blank, and a party with no privacy state is left out of party recounts); an export that cannot run | One line on standard error: `could not run: ...`, `platform problem: ...`, `connection or file problem: ...` or `could not export: ...`. It never quotes a platform reply. Under the harness, an error the agent does not expect shows as `internal error: <error type>`; a local run shows the traceback instead. |
| 2 | A command-line mistake, such as an unknown `--instance` value | The usage message |

For the harness, a non-zero exit counts as an infrastructure failure. That is why a scan that cannot read its
records, or runs out of time, says so and exits 0, while a platform outage still exits 1.

## 6. Limits

- Matching is by rules, so some duplicates are missed: spellings further apart than a letter or two ("Lakshmi" and
  "Laxmi"), abbreviations ("SS" for stainless steel), initials. The plan lists every known gap.
- A reworded question is refused. To support a new wording, it has to be added to `data_steward/router.py` and its
  tests.
- Recounts answer what is filled in, not what is true: a placeholder phone such as `+91 0000000000` counts as a phone.

## Where to read more

- [README](../README.md): setup, commands and the private input files.
- [recount-rules.md](recount-rules.md): the exact rule behind each recount.
- [duplicate-rules.html](duplicate-rules.html): how a pair is scored.
- [first-task-plan.md](first-task-plan.md): decisions, known gaps and history.
