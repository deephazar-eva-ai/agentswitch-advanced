# Debugging seat 31

One page: a failed suite or report run, and what to do first. Run every command from `31-order-to-cash-watcher/`, after `source .env`.

## 1. Find the symptom

| Symptom | First step |
|---|---|
| Exit code 3, or `could not complete: …` | The platform can't be read. The message names the setting (`AGENTSWITCH_TOKEN`, `AGENTSWITCH_BASE_URL`), or says the platform is down. Don't retry a failed login in a loop: repeated wrong passwords lock the account |
| Exit code 2 | Bad options. The line above it says which one |
| `python3 -m o2c` exits 1 with `o2c: unexpected error: <type>: <message>` | A crash in the report. The traceback is just above that line; rerun with `--debug` for retries and slow requests too |
| `python3 -m o2c.drafts` exits 1 with `o2c.drafts: unexpected error: …` | A crash in the drafts command. If it says "Saved N of M drafts before this", check the mailbox before running `--create` again: the draft in progress may have been created |
| `python3 -m o2c.suite` exits 1 | At least one task failed; its `FAIL` line says which (exit 0 means every task passed). `o2c.tasks_orders` and `o2c.tasks_payments` do the same |
| `python3 -m o2c` exits 4 | The report was produced, but the order list may be incomplete. Stderr says why |
| `outcome=infrastructure; <Error>: <message>` on one task | A crash in that task. Rerun just that task with `--only <task> --debug` (section 2) and read the traceback |
| `outcome=blocked; …` | The login can't read what the task needs. Not a failure of the report |
| `FAIL` naming records (`missing=[…]`, `extra=[…]`, `wrong_age=[…]`, `leaked=[…]`, `changed=[…]`) | A wrong answer on those records. Rerun with `--debug` to see the expected and actual values, and `python3 -m o2c --explain <record>` to see which rule put it in or left it out (section 3) |
| Task 9 fails with "harness copy" | It only creates a draft on the harness copy or with `--allow-writes`. Expected on live data |
| Tasks 10–11 score 0.50 | They ran without `--agent`, so only the guardrails were checked |
| Tasks 10–11 with `--agent` say `agent exit=1 claim=none` | The agent itself failed. Rerun with `--agent --debug`: the agent gets `--debug` too, and its last 40 lines are shown under `debug: <task> agent failed …` |
| The run is slow | Run with `--out` and read the per-task `seconds`, `calls` and `retries` in `results.meta.json`, or rerun with `--debug` |
| A failure you can't reproduce | The data may have changed. Record the next run (section 4) so it can be replayed exactly |

## 2. Rerun with `--debug`

```
python3 -m o2c.suite --instance keystone --only overdue_ageing --debug    # one task, in seconds
python3 -m o2c.suite --instance keystone --debug                          # the whole suite
python3 -m o2c --instance keystone --debug
python3 -m o2c.drafts --instance keystone --out reports/ --debug
```

`--only` takes task ids, comma-separated (the part after `<instance>:` in a result, e.g. `overdue_ageing`); an unknown id lists the valid ones. It works with `--record` and `--replay` too. `--debug` writes to stderr only: a line as each task starts and ends (its time, platform calls and retries), each retry, any request over 10 s, any blocked write, the full traceback of a crash (labelled with the task), and a closing platform summary. The `PASS`/`FAIL` lines, `results.json` and the report are the same with or without it. Seat 34's harness adds only `--prompt` to the command we register, so `--debug` has to be in that command to show in a harness run.

## 3. Read a wrong answer

The evidence names up to three records for each problem (`missing=`, `extra=`, `wrong_age=`, `leaked=`, `changed=`). With `--debug`, tasks 1, 5, 6, 7 and 12 also print the values behind them, on stderr only:

```
debug: overdue_ageing a1b2c3d4 days_overdue: want {"days_overdue": 14, "due_date": "2026-11-01"} | got 15
debug: report_read_only Payment:A edited: want {"unallocated": 50.0, …} | got {"unallocated": 0.0, …}
```

`want` is what the task worked out from the raw records; `got` is what the report said. The values are customer data, so they never go into the evidence or `results.json`, and shouldn't be pasted into a PR or a chat outside the seat.

To see why the report put a named record in or left it out, explain it, live or from a recording:

```
python3 -m o2c --explain SO-2026-00042
python3 -m o2c --explain SO-2026-00042 --replay reports/recordings/keystone-<date>
```

```
SalesOrder SO-2026-00042 (id 1a2b…), customer …
  read as the report reads it
  Section 1, orders not invoiced: OUT
    yes  submitted (docstatus 1): docstatus is 1 (submitted)
    yes  not fully invoiced (unbilled or partially_invoiced): invoiced_status is unbilled
    NO   more than 7 days old by order date: dated 2026-11-20, 5 days before 2026-11-25
```

- It takes an id or a number, and works for orders, invoices and payments. It stops at the first rule that fails.
- The verdict is `report.py`'s own: its section function run on that one record, so the explanation can't disagree with the report. If the step-by-step walk ever disagrees, it prints a warning, and the report's verdict is the one shown.
- It reads what the report reads, with the same filters, so a suite recording answers it offline, on the recorded date and days. A record the report never reads (a draft order, a supplier bill, an outgoing payment) is then looked for with no filter. A replay can only do that if the recorded run made the same read.
- `--days` and `--date` work as for the report. It prints no ID list and no `AGENT_RESULT`. Exit 5 means no record has that id or number.
- It prints the record's values, which are customer data, so keep the output within the seat.

## 4. Reproduce a failure offline

Record a run once, then replay it as often as needed, with no platform and no token:

```
python3 -m o2c.suite --instance keystone --record reports/recordings/keystone-<date> --out reports/results.json
python3 -m o2c.suite --replay reports/recordings/keystone-<date> --debug
```

- `--record` needs a new or empty folder: one that already holds a recording is refused, so two runs' replies are never mixed.
- Replay uses the recording's system, date and days unless they're given. It refuses a different system.
- Every rerun sees exactly the same data, takes about a second, and can be used with `git bisect run` (the suite exits 1 when a task fails) or to try a fix before a live run.
- If the code now asks for something the recorded run didn't, replay stops with `replay: no recorded reply for …`. Record a new run.
- `--agent` can't be combined with `--record` or `--replay`: the agent runs as a separate process that talks to the platform itself.
- A recording holds customer records. `--record` refuses any folder git would track, so keep recordings under `reports/` (gitignored), and delete them when they're no longer needed.

## 5. What a run leaves behind

| File | Written by | Holds |
|---|---|---|
| `results.json` | `--out` | The 12 tasks in the `34-eval-harness/TASK_FORMAT.md` shape |
| `results.meta.json` | `--out` | What was run: commit (and whether files were changed), source (live, record or replay), start and end, Python version, platform totals, and per task its seconds, calls and retries. No token, no platform address |
| `<DIR>/calls.jsonl`, `<DIR>/recording.json` | `--record DIR` | Every request and reply; what was recorded |

## 6. Before asking for help

Send the task id, the full `FAIL` line, the commit from `results.meta.json`, and whether the run was live or replayed. Keep the recording if there is one. Don't send `want`/`got` values or recordings outside the seat.
