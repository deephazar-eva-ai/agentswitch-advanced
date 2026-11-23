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

- **Go to the project directory.** From the repository root, run `cd 35-data-steward`. Every command below runs from
  there.
- **Set up your environment.** Run `uv sync`, copy `.env.example` to `.env`, and enter `SEAT35_EMAIL` (the seat's
  course login) and the password for each system you will use. `.env` is git-ignored.
- **Ask before running the report, a scan, or a recount.** Both systems are live and shared, and usage is monitored,
  so ask the person you work for first and keep runs few. The export, the offline tests (`uv run pytest`), and a
  refused request make no calls.
- **Check `.env` after a login failure.** A failed login is never retried. Ten wrong passwords in 15 minutes lock the
  seat's login, so check `.env` before trying again.
- **Choose a system.** Pick the system with `--instance suryodaya` or `--instance keystone`. The default is Keystone,
  or the harness runner's system when running under the harness.

## 1. Run a duplicate report

```bash
uv run python -m data_steward.dedupe --instance keystone
uv run python -m data_steward.dedupe --instance suryodaya
```

Each run reads every party and item on one system. Because they hold real party data, it writes three
files—readable only by your account—into the git-ignored `local/` folder:

| File | What it holds |
|---|---|
| `local/dedupe-<system>.json` | The full report and record of truth |
| `local/dedupe-<system>.md` | The same report formatted as a readable page |
| `local/dedupe-<system>-pairs.json` | Each listed pair, with its pair id, the two record ids, score, and band |

Standard output prints only a summary with counts (rows read per table, pairs per band, exclusions, the orphan
check), so it is safe to paste into a pull request; its last line names the files. If a run fails while reading, the
earlier files stay as they were. If it fails while writing, all three are removed so old and new files are never
mixed.

Two optional private files in `local/` change what the report shows. To reproduce someone else's report, get both
files from them privately; the README has the details.
- `local/test-tags.txt`: confirmed test-run tags, one per line, the tag first, added by a person only after a read
  confirmed it. Without this file, pairs of test records are not set apart.
- `local/dedupe-labels.json`: your verdicts on pairs (section 2).

**Reading the report** (`.md`):
- **Proposed merges:** pairs scoring 0.9 or more with nothing holding them back.
- **Pairs to review:** pairs scoring 0.6 or more that a person should judge. A pair can score 1.0 and still be here
  when a rule holds it back; the reason is shown (for example, different roles or different phone numbers).
- Each pair shows what the records share, what differs, and a **suggested record to keep**, chosen in this order:
  the one with more linked records (counted for proposed merges only), then the older record, then the one with more
  fields filled, and then the first id in text order. It is only a suggestion.
- **Test-record pairs:** pairs where a name carries a confirmed test-run tag, listed one per row. The summary counts
  them separately from the bands, but the export still lists them (section 3). As a result, an export can hold pairs
  even when the report shows no ordinary proposal.
- Pairs a person has already labelled are listed one per row under their band (or under Test-record pairs, for
  those). Full details of every pair are in the `.json` report.
- **Pairs ruled out:** pairs that scored high but can never be merged, along with the reason—for example, different
  companies or a party flagged as an allowed duplicate.
- **Omissions and limits:** what was not listed and why: low scores, records withheld for privacy, or values shared
  too widely to count.
- **Items whose preferred vendor is missing:** the orphan check. "Not found among the parties this login can list"
  means the vendor id does not name any party this login can read. A party created after the party table was read
  can also show up here. If the party table was cut off, the check reports that it was not run. If only the item
  table was cut off, the count is a lower bound.
- Scores are rule scores, not probabilities. For how a pair is scored step by step, see
  [duplicate-rules.html](duplicate-rules.html).

If a table was cut off because there were too many rows to read, the report says so. Rows beyond the cut were never
compared, so a missing pair does not prove two records are different.

## 2. Review pairs

Give each pair you have reviewed a verdict in `local/dedupe-labels.json`. The agent reads this file and never writes
to it:

```json
{
  "party:<first id>:<second id>": "accepted",
  "item:<first id>:<second id>": {"label": "rejected", "note": "different sizes"}
}
```

- **Copy each pair id exactly as the report shows it.** The two ids are in a fixed order, so do not swap them.
- **Use only `accepted` and `rejected`.** Any other value stops the next report run (a local run shows a Python
  traceback whose last line names the bad entry) and stops the export with
  `could not export: dedupe-labels.json is not valid: each label must be accepted or rejected`.
- **No effect on scores or records.** A label records intent only: it does not change any score, band, suggestion,
  or record.
- **Changes apply at the next export.** The export reads this file fresh each time, so a new label counts at the
  next export. The report shows labels as they were at its last run; run the report again only to see them there.

## 3. Export the results file

```bash
uv run python -m data_steward.results
```

This creates `results/dedupe_pairs.json`, the file the instructor scores, from the reports already saved in
`local/`.
- **Needs both systems.** If a report is missing (section 1), the export stops with
  `could not export: no saved report for <system>: run the scan first`.
- Using `--instance keystone` exports one system only and **replaces** the results file with that system's pairs
  alone. Use it for a check, not for the file you commit.
- **No platform calls or record values.** The export calls nothing on the platform. It writes one entry per pair
  with `kind`, `id_a`, `id_b`, `confidence`, and `reason`, and no names or other record values.
- **Filtering and pair rules.** Pairs labelled `rejected` are left out. Test-record pairs are listed, with their
  reason ending "test-run records". A review pair's confidence is 0.6 at most.
- **Standard output.** Standard output shows counts only, followed by the file's path, including any `rejected` label
  that matches no pair in the reports used (often a typo, or a label for the other system when exporting one).
- **Version and format checks.** The export refuses a report from another rules version or with a cut-off table,
  leaving the earlier file unchanged.
- Commit the file in a pull request; never edit it by hand.

## 4. Ask it a question

The course harness runs the agent once per task and passes the request with `--prompt`; you can do the same:

```bash
uv run python -m data_steward.dedupe --instance suryodaya --prompt "Which are the items that have no category?"
```

A request run with `--prompt` writes nothing to `local/` and ignores the test tags and labels. The agent recognises
only the forms below; **anything else is refused before it reads a setting, logs in or calls anything.** This is
deliberate: it can never be talked into acting. Case, extra spaces and apostrophes do not matter; each sentence
(ending in `.`, `!`, `?` or `;`) must match one form in full.

**How a request is read.** The request is free text, but it is never run as code:
1. **It arrives as one string.** The harness starts the agent with a list of arguments rather than through a shell,
   adding two items at the end: `--prompt` and the request text. On your own command line, quotes keep the sentence
   together as one argument. Python's `argparse` stores it as a plain string, and nothing evaluates it: no `eval`,
   no shell, and no AI model.
2. **The record ids are taken out first,** matched by their exact format (8-4-4-4-12 hexadecimal characters).
3. **The rest is tidied and split into sentences:** converted to lower case, reduced to single spaces, stripped of
   apostrophes, and split at `.`, `!`, `?` and `;`.
4. **Each sentence must match one known form in full.** For example, "scan these six parties for duplicates" matches
   the scan form, and "do not change any record" matches a safety form. If any sentence matches nothing (such as
   "Why not merge each pair?"), the whole request is refused before any setting is read, any login or any call.
5. **Only a small, fixed set of choices comes out:** whether to scan, recount or refuse; party or item; the list of
   ids; and which of the four questions. That output drives the code. The request text is never printed back or sent
   to the platform, and the ids go to the platform only as values in read-only list calls.

The patterns are in `data_steward/router.py`.

**Scan named records for duplicates.** Write one read sentence, followed at the end by a colon and the
comma-separated record ids. Use the individual record ids (the 36-character ids with hyphens, as in the report's
record lists or the platform), not a pair id, with no brackets or quotes. For example:

- "Scan these six parties for duplicates: `<id>`, `<id>`, ..." (each `<id>` a full record id)
- "Look through these eight item records for duplicate pairs and note what you find: ..."
- "Find duplicate parties in these eight party records and record your merge proposal: ..."

The read sentence is one of two shapes:
- look through, scan, check or review, followed by "these", "the" or "those", an optional number, then parties,
  items, records, party records or item records, then "for duplicates", "for duplicate pairs" or "for duplicate
  records", and optionally ending with "and note what you find", "and report what you find" or "and list what you
  find";
- find, identify or list, followed by "duplicate" parties, items, records, party records or item records, then "in"
  or "among" "these/the/those", an optional number, and parties, items, records, party records or item records,
  optionally ending with "and note what you find", "and report what you find" or "and record your merge proposal".
  Only this shape allows "record your merge proposal".

Naming both parties and items in one request is refused (which table is meant is unclear). "Records" alone means
each id is looked up as a party first, then as an item.

**Recounts** (no record ids; the exact sentences, as in [recount-rules.md](recount-rules.md)):

| Question | Recognised sentences |
|---|---|
| Parties with neither email nor phone | "List the parties that have neither an email nor a phone." / "Which are the parties that have no email and no phone?" |
| Items with no preferred vendor | "List the items that have no preferred vendor." / "Which are the items that have no preferred vendor?" |
| Parties sharing a name | "List the parties that share the same name with another party." / "Which are the parties that have the same name as another party?" / "Identify every party whose name matches another party's name." |
| Items with no category | "List the items that have no category." / "Which are the items that have no category?" |

**Safety sentences** may stand alongside a scan or a recount, never alone: "Make no changes.", "Do not make any
changes.", "Do not change any record." (or delete, merge, create, update, edit, modify or remove in place of change,
and record, item, party or approval request in place of record), "Leave every party record exactly as it is.", and
the harness's own lists such as "Do not merge, delete, or change any record."

**What the agent returns.** When a prompted run exits 0, it ends with one line the harness reads:
`AGENT_RESULT {...}`, one line of JSON with a `claim` (and, for a recount under the runner, an `answer`).

| Request | Standard output | Claim |
|---|---|---|
| Refused | `refused: only supported read requests can run; nothing was read or changed` | `refused` |
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
| 0 | A report or export completed, and every outcome in the table above | The output described above |
| 1 | Login fails or settings are missing; a platform outage or error occurs (including during a scan); a connection is lost; a recount table is cut off, or a field the rule needs is absent from every row of a non-empty table; or an export cannot run | One line on standard error—such as `could not run: ...`, `platform problem: ...`, `connection or file problem: ...` or `could not export: ...`—that never quotes a platform reply. Under the harness, an unexpected error shows as `internal error: <error type>`, while a local run shows the traceback. |
| 2 | A command-line mistake, such as an unknown `--instance` value | The usage message |

Because the harness treats a non-zero exit as an infrastructure failure, a scan that cannot read its records or runs
out of time says so and exits 0, while a platform outage still exits 1.

On a single row, a missing contact, category or vendor value counts as blank, and a party with no privacy state is
left out of party recounts.

## 6. Limits

- **Rule-based matching:** Matching is by rules, so some duplicates are missed—such as spellings further apart than
  a letter or two ("Lakshmi" and "Laxmi"), abbreviations ("SS" for stainless steel), and initials. The plan lists
  every known gap.
- **Fixed question wording:** A reworded question is refused. New wording must be added to `data_steward/router.py`
  and its tests.
- **Filled-in values in recounts:** Recounts answer what is filled in, not what is true: a placeholder phone such as
  `+91 0000000000` counts as a phone.

## Where to read more

- [README](../README.md): setup, commands and the private input files.
- [recount-rules.md](recount-rules.md): the exact rule behind each recount.
- [duplicate-rules.html](duplicate-rules.html): how a pair is scored.
- [first-task-plan.md](first-task-plan.md): decisions, known gaps and history.
