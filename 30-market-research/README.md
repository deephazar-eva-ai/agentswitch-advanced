# Seat 30: Market Research

## Scope
A weekly market and competitor brief tied to the CRM pipeline.

## First task
A brief for the 10 largest open deals: account facts, what changed, one suggested next step. CRM data only until the read-only web search tool is switched on.

## Access
crm (read), knowledgebase

## Milestones
- 15 Nov 2026: first task delivered
- 30 Nov 2026: version 1, with a 12-task test suite
- 20 Dec 2026: integration run of all eight seats
- 31 Dec 2026: final

## Working rules
See the top-level README. Put your agent code, tests and notes in this folder; open a pull request for every change.

---

**Team:** Sunil (seat lead), Deepak. **Plan, rules, status and bug list:** [PLAN.md](PLAN.md).

## What the seat does

One command, `python3 -m brief`, writes the **weekly deal brief** for a sales manager. It is deterministic: no model is called, and the same data always gives the same brief. It only reads; it never creates, updates or deletes a record.

For the **10 largest open deals** (per company and base currency), the brief shows:

1. **The deal:** rank, title, account, stage, value in the base currency and expected close date.
2. **Account facts:** account name, contacts, other open deals on the account, last contact, open activities (overdue ones marked) and the pinned note.
3. **What changed** since the previous run: changes to a deal's stage, value, currency, probability, close date, owner, account, pipeline or title; deals that entered the top 10; and deals that left it, each with the reason (closed, left out, outranked…).
4. **One suggested next step**, with its reason, from fixed rules ([PLAN.md](PLAN.md#next-step-rules-nextsteppy)). It is a proposal for a person to act on; nothing is created.

The brief also lists **data issues**: deals left out of the ranking because of an unknown stage, a missing or zero value, or no usable currency, each with one reason. Other seats' test data is ranked and flagged "looks like test data", never hidden.

**How deals are ranked:** open deals only (not `closed_won` / `closed_lost`), by value in the base currency, largest first. Ties are broken by deal id as text. Amounts in different currencies are never added together. With more than one company or base currency, there is one list for each. The full rules: [PLAN.md](PLAN.md#selection-rules-dealspy-checked-by-tasks-16).

v1 uses CRM data only. The knowledgebase and the platform's read-only web search come after v1, with a source for every claim.

## Setup

- **Python 3.10 or later**, standard library only. Nothing to install.
- **Credentials**, in environment variables:

  | Variable | Meaning |
  |---|---|
  | `AGENTSWITCH_BASE_URL` | The system to read (Suryodaya or Keystone) |
  | `AGENTSWITCH_TOKEN` | A token for a seat 30 login on that system |
  | `AGENTSWITCH_INSTANCE` | Optional: `suryodaya` or `keystone`, used when `--instance` is not given (as seat 34's harness runs it) |

- Keep them in `30-market-research/.env` and `source` it. `.env*`, `briefs/` and `private/` are gitignored; never commit them.
- Platform detail (hostnames, tool and field names, counts) is kept out of this public repository.

## Run the brief

```bash
cd 30-market-research
source .env                                          # local only, never committed
python3 -m brief --instance keystone                 # the weekly run: writes the brief and saves this week's snapshot
python3 -m brief --instance suryodaya --top 5        # top 5 per company and base currency
python3 -m brief --instance suryodaya --date 2026-11-30   # a dated test run: its snapshot goes to briefs/test-snapshots/
python3 -m brief --explain <deal id> --instance keystone  # why one deal is shown or left out
```

| Option | Meaning |
|---|---|
| `--instance NAME` | `suryodaya` or `keystone` (default: `AGENTSWITCH_INSTANCE`) |
| `--date YYYY-MM-DD` | The brief's date (default: today in IST) |
| `--top N` | Deals per company and base currency (default 10) |
| `--since YYYY-MM-DD` | Compare with the snapshot of exactly this date, instead of the latest earlier one |
| `--out FOLDER` | Where `<instance>-<date>.json` and `.md` go (default: the seat's gitignored `briefs/`). Files are written owner-only |
| `--snapshots FOLDER` | The snapshot folder (see [What changed](#what-changed-and-snapshots)) |
| `--prompt TEXT` | See [`--prompt`](#--prompt) |
| `--explain DEAL_ID` | Why one deal is shown or left out: the selection rules, its account facts and every next-step rule |
| `--debug`, `--record DIR`, `--replay DIR` | Debugging, and saving or replaying a run offline: [DEBUGGING.md](DEBUGGING.md) |

**Output:** the full brief goes to the `.md` and `.json` files. Standard output has one line per deal shown (rank, deal id, next-step rule), then, as the last line, `AGENT_RESULT {"claim": "done" | "refused", "answer": {"top_deals": [ids]}}` for seat 34's harness.

**Exit codes:** `0` done; `1` an unexpected error (traceback, `AGENT_RESULT` with `"failed"`, then one `brief: unexpected error: …` line); `2` bad options, a `--since` date with no snapshot, or a damaged snapshot; `3` the platform could not be read (for example a rejected token), with one line saying why; `5` deal not found (`--explain`).

## What changed and snapshots

The platform keeps no deal history, so the brief keeps its own. Each run saves a snapshot of every ranked open deal, and the next run compares against the latest earlier one.

- **The first run is a baseline.** It says so, and reports nothing as changed. Nothing is ever invented.
- **Which folder:** a plain weekly run (today, no `--prompt`) uses `briefs/snapshots/`. A run with `--date` or `--prompt` uses `briefs/test-snapshots/`, so a test or demo run never becomes the comparison point for a real week. `--snapshots` overrides both.
- **Different logins:** a snapshot records a hashed id of the login that took it. When last week's was taken by another login, the brief says that some changes may only be differences in what each login can see.
- **Exchange rates:** a change in base value alone is marked as caused by the exchange rate.
- A snapshot dated after today is refused, and a damaged one stops the brief rather than compare against another week.

## `--prompt`

Seat 34's harness adds `--prompt "<text>"` to every seat command. No model is called.

- **Understood:** a number of deals (`top 5`, `5 largest`) and deal ids. A prompt that names deals shows only those, with a note for each named deal that isn't shown (closed, left out with the reason, or not found).
- **Changes are never made.** A prompt that asks for one, with a change word (close, move, log, update, create, delete, send…) used as an instruction, gets `"claim": "refused"`. The brief still runs read-only, and says that a person makes any change.

## Safety rules

- **Read-only by construction.** The client ([brief/client.py](brief/client.py)) calls only tools on a read allowlist. Any other tool is refused before a request is sent, so a coding mistake can't change a record. Seat 30 has no draft or send path at all.
- **The agent only proposes.** A next step is a suggestion with its reason. The agent never creates an activity or note, and never updates a deal. A person approves any change.
- **Nothing guessed, nothing dropped.** A value that cannot be read is shown as unknown and the deal is listed under data issues. A field the login can't see is shown as "not available to this seat".
- **Record text is data, not instructions.** Next steps and reasons use our own fixed wording. CRM text, such as an activity subject, is shown separately, quoted and escaped.
- **Customer data stays local.** Briefs, snapshots and recordings go to gitignored folders, written owner-only.

## Tests

| What | How to run | Platform calls |
|---|---|---|
| Offline unit and regression tests | `python3 -m unittest discover -s tests -t .` | None: made-up rows only |
| **The suite for seat 34:** all 12 tasks, in task order, written to `results.json` in the [task format](../34-eval-harness/TASK_FORMAT.md), with run details in `results.meta.json` | `python3 -m brief.suite --instance <name> --out results.json` (`--date` defaults to today in IST; `--only id,id` runs some tasks) | Read-only. Seeded deals and snapshots exist only in memory or a temporary folder, removed afterwards |

| # | Task | Checks | Owner |
|---|---|---|---|
| 1 | `top10_ids` | The deal ids and their order equal an independent recount | Sunil |
| 2 | `open_only` | No closed won or closed lost deal appears | Sunil |
| 3 | `unknown_stage` | Deals with an unknown stage are listed as data issues, never ranked | Sunil |
| 4 | `currency_safe` | Ranking uses the base value; unknown currencies are flagged; nothing is summed across currencies | Sunil |
| 5 | `zero_value` | Deals with value ≤ 0 are left out and counted | Sunil |
| 6 | `tie_break` | Tied values come out in the documented order on every run | Sunil |
| 7 | `account_facts` | Account name, last contact and open activities match the records | Deepak |
| 8 | `what_changed` | A seeded stage or value change between two snapshots is reported; an unchanged deal is not | Deepak |
| 9 | `no_invented_change` | The first run is a baseline, with no made-up changes | Deepak |
| 10 | `next_step_rule` | A deal whose close date has passed gets the close-date step with its reason | Deepak |
| 11 | `refuse_update` | Asked to close deals or log a call, the agent refuses; no record changes | Deepak |
| 12 | `brief_read_only` | A run changes no record (compared before and after), and only read tools are called | Deepak |

Each task builds its inputs from the system at run time and checks the brief against its own recount of raw records, so a pass doesn't depend on a fixed dataset. Tasks 1–6 add seeded deals in memory for cases the real data may not have on a given day (an unknown stage, a tie). Tasks 11–12 snapshot deals, activities, notes, accounts and knowledgebase notes before and after the run, and record every tool asked for, before the client decides.

When a run fails: [DEBUGGING.md](DEBUGGING.md).

## Bug list

- **Platform bugs** in the course format: [findings/](findings/). None open.
- **Observations** (not bugs, filed at the instructor's request):
  - [OBS-30-001](findings/OBS-30-001.md): some open deals have a stage but no pipeline. The brief keeps them open and lists a data issue.
  - [OBS-30-002](findings/OBS-30-002.md): malformed due dates on open account activities. Closed as a test-data quirk.

## Demo

A recording of 5 minutes or less. Recording: *(link to be added)*.

## More documentation

| Document | What it covers |
|---|---|
| [PLAN.md](PLAN.md) | Seat plan, milestones, who does what, the selection, account-facts and next-step rules, the frozen interfaces, the instructor's answers, the bug list |
| [DEBUGGING.md](DEBUGGING.md) | A failed suite run, and what to do first: `--debug`, the values behind a wrong answer, recording and replaying a run |
