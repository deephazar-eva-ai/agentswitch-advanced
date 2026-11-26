# Data Steward: user-facing messages

Every fixed message the agent prints for a person to read, grouped by where it appears. Text in `{braces}` is filled
in by the program at run time. JSON output (counts, the report summary) and the `AGENT_RESULT {...}` line are
machine-read and left out.

Constraints for any rewrite:

- Each message stays one line.
- Keep every `{placeholder}`, file name, environment variable, HTTP code and the words `propose`, `review`, `scan`,
  `accepted`, `rejected` exactly.
- Keep the prefixes `refused:`, `platform problem:`, `connection or file problem:`, `could not run:`,
  `internal error:` and `could not export:` exactly: the harness and the user guide refer to them.
- No message may quote a record id or a record value.
- These lines are quoted word for word in `tests/test_report.py`, `tests/test_requests.py`, `docs/user-guide.md` and
  `docs/using-the-agent.html`, so a reworded message changes those too.

## 1. Answers to a harness request (`--prompt`), standard output

| # | Message | When | Source |
|---|---|---|---|
| 1 | `refused: only supported read requests can run; nothing was read or changed` | The request is not one of the supported forms | `data_steward/report.py` |
| 2 | `could not read exactly one row for every named record; no counts are given` | A named record was missing or not unique | `data_steward/report.py` |
| 3 | `pairs are compared among the named records only; nothing was changed or recorded on the platform` | After every scan's counts | `data_steward/report.py` |
| 4 | `merge proposal not recorded: the request asked to record one, but this agent does not record proposals` | The request asked to record a proposal and the scan found a pair | `data_steward/report.py` |
| 5 | `stopped at the time limit before every named record was read; no counts are given` | The 4-minute scan limit ran out | `data_steward/report.py` |
| 6 | `record ids withheld: they print only under the harness runner, and only while IDS_ALLOWED is set` | A recount run locally, or with `IDS_ALLOWED` off | `data_steward/report.py` |

## 2. Failure lines from `python -m data_steward.dedupe`, standard error

The first words name the kind of problem. `report.py` builds these in `_error_line`.

| # | Message | When | Source |
|---|---|---|---|
| 7 | `platform problem: a tool call failed ({kind})` | A platform tool call failed; `{kind}` is `permission_denied`, `unknown_tool` or `error` | `data_steward/report.py` |
| 8 | `platform problem: the token was rejected` | The token expired or was refused | `data_steward/report.py` |
| 9 | `connection or file problem: {error type} ({reason})` | Network or file error; `({reason})` only when there is one | `data_steward/report.py` |
| 10 | `could not run: {message}` | Any message from section 3 | `data_steward/report.py` |
| 11 | `internal error: {error type}` | An unforeseen error under the harness (a local run shows the traceback) | `data_steward/report.py` |

## 3. Messages that follow `could not run:`

| # | Message | When | Source |
|---|---|---|---|
| 12 | `the {table} table was incomplete; no partial answer is given` | A recount table was incomplete | `data_steward/report.py` |
| 13 | `privacy_state is missing from every party row; no answer is given` | Recount: the field is missing from every party | `data_steward/checks.py` |
| 14 | `{field} is missing from every row; no answer is given` | Recount: the field is missing from every row | `data_steward/checks.py` |
| 15 | `AGENTSWITCH_TOKEN is set but {names} is not` (`are not` for two names) | Harness settings incomplete | `data_steward/config.py` |
| 16 | `AGENTSWITCH_INSTANCE={value} is not one of {instances}` | Unknown system name from the harness | `data_steward/config.py` |
| 17 | `Set SEAT35_EMAIL in .env` | Login name missing locally | `data_steward/config.py` |
| 18 | `Set SEAT35_PASSWORD_{INSTANCE} in .env` | Password missing locally | `data_steward/config.py` |
| 19 | `{base url} rejected the harness runner's token (401)` | The runner's token was refused | `data_steward/mcp_client.py` |
| 20 | `login failed on {instance}: HTTP 401 (check SEAT35_EMAIL and the password)` | Wrong login name or password | `data_steward/mcp_client.py` |
| 21 | `login failed on {instance}: HTTP {status}` | Any other login failure | `data_steward/mcp_client.py` |

## 4. Export, `python -m data_steward.results`

Failures print `could not export: {detail}` on standard error (`data_steward/results.py`). `{detail}` is one of
the messages below, or an error type such as `OSError (Permission denied)`.

| # | Message | Source |
|---|---|---|
| 22 | `a pair in a report has an invalid format` | `data_steward/results.py` |
| 23 | `a pair in a report is marked propose even though it is held at review` | `data_steward/results.py` |
| 24 | `a pair in a report has a signal or cap with no wording here` | `data_steward/results.py` |
| 25 | `a report's pair list has an invalid format` | `data_steward/results.py` |
| 26 | `no saved report for {instance}: run the scan first` | `data_steward/results.py` |
| 27 | `{file} is not the {instance} report` | `data_steward/results.py` |
| 28 | `{file} was made with a different rules version (expected {version}): run the scan again` | `data_steward/results.py` |
| 29 | `{file} has no valid date` | `data_steward/results.py` |
| 30 | `{file} does not hold both tables` | `data_steward/results.py` |
| 31 | `{file}: the {tables} table was incomplete, so pairs may be missing` | `data_steward/results.py` |
| 32 | `the same pair is in two systems' reports; export one system at a time` | `data_steward/results.py` |
| 33 | `a pair is listed twice` | `data_steward/results.py` |
| 34 | `dedupe-labels.json is not valid: each label must be accepted or rejected` | `data_steward/results.py` |

## 5. Success lines, standard output

| # | Message | Source |
|---|---|---|
| 35 | `report: {json file} and {markdown file}; pairs: {pairs file}` | `data_steward/report.py` |
| 36 | `results: {file} ({count} pairs)` | `data_steward/results.py` |
| 37 | `{instance}: {count} tools -> local/tools-{instance}.json` | `data_steward/probe.py` |
| 38 | `  {module}: {count}` (indented, one per tool group) | `data_steward/probe.py` |
| 39 | `{instance}: FAILED {error type}: {message}` | `data_steward/probe.py` |

## 6. Seen only in a local traceback or after `FAILED McpError:` in the probe

Under the harness these show only as line 7 or 11. A platform error reads `{code}: {message}`.

| # | Message | Source |
|---|---|---|
| 40 | `{file}: expected a mapping of pair id to label` | `data_steward/report.py` |
| 41 | `{file}: labels must be one of {labels}; fix {pair ids}` | `data_steward/report.py` |
| 42 | `unknown instance {name}` | `data_steward/mcp_client.py` |
| 43 | `this run is for {instance} only, not {other instance}` | `data_steward/mcp_client.py` |
| 44 | `bad_reply: tools/list returned no tool list` | `data_steward/mcp_client.py` |
| 45 | `refused: {tool} is not a read-only tool; this agent never changes a record` | `data_steward/mcp_client.py` |
| 46 | `bad_reply: {tool} returned no result` | `data_steward/mcp_client.py` |
| 47 | `bad_reply: {table}.list did not return a list of rows` | `data_steward/mcp_client.py` |
| 48 | `bad_reply: {table}.list repeated a page (offset ignored)` | `data_steward/mcp_client.py` |
| 49 | `bad_reply: {table}.list returned more rows than its reported total` | `data_steward/mcp_client.py` |
| 50 | `unknown entity {name}` | `data_steward/dedupe.py` |
