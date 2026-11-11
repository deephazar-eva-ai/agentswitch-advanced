# Seat 33 replay — boundary probes

The replay script repeats the catalogue attempts from a login (read from the environment, never a file), as required by `../CATALOGUE_FORMAT.md`. The course team re-runs it against the test systems before each release.

Stdlib-only Python. No credentials, no platform source, no schemas are stored here.

## Setup
Tokens come from the environment (see `mcp_client.py`). Get one per system (lasts ~7 days):

```bash
curl -s https://agentswitch.theschoolofai.in/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<you>","password":"<pw>"}'        # read the "token" field
export AGENTSWITCH_SURYODAYA_TOKEN=<token>
# and/or Keystone:
export AGENTSWITCH_KEYSTONE_TOKEN=<token>
```

Never echo tokens into files that get committed. `.env`, `secrets*`, `*.key` etc. are git-ignored at the repo root.

**Not in this script.** Catalogue attempts 01–03 (BUG-33-001, BUG-33-002 and attempt-03) are not in this public script. This repository is public, so their steps are kept on the harness copy only. The catalogue keeps their title, affected component and fix status.

## Run
```bash
python run_boundary_probes.py          # read-only + auth probes (safe)
PROBE_SYSTEM=keystone python run_boundary_probes.py
```

**Login check.** Before any probe, the script makes one control call (`Company.list`) with your token. If the token is missing, expired or rejected, it stops with a message and runs no probes, so a bad login can never show up as a clean run. If the other system's token is set for F3, it is checked on its own system first. Otherwise a 401 would prove nothing about per-system tokens.

### Verdicts
| Verdict | Meaning |
|---|---|
| `HELD` | The call was refused: `permission_denied` or HTTP 403. For the auth probes (F1–F3), HTTP 401. |
| `HELD(not-exposed)` | `tool_not_available`: the tool is not in this login's `tools/list`. If a Broke attempt flips to this, check `tools/list` for a renamed tool before calling it fixed. |
| `HELD(no-data)` | HTTP 200 with no records. |
| `FINDING?` | HTTP 200 with records. Compare with the catalogue. |
| `UNCLEAR` | The probe ran but cannot decide the boundary: C1–C3 (see below). |
| `ERROR` | Anything else: bad arguments, a server or network error, or an unexpected 401. The probe did not test the boundary, so it is never counted as Held. |

The script prints a summary and **exits with status 1 if any probe is `ERROR`**. A forced verdict (`UNCLEAR`) never hides an `ERROR`.

The cross-tenant probes (C1–C3, catalogue attempts 11–13) read `FOREIGN_COMPANY_ID` and `FOREIGN_PARTY_ID` from the environment and fall back to placeholder ids:
```bash
FOREIGN_COMPANY_ID=<id> FOREIGN_PARTY_ID=<id> python run_boundary_probes.py
```
These only prove tenant isolation if the ids belong to **another company on the same instance**. Ids from the other instance (or placeholders) don't exist on this system, so "not found" or 0 rows is expected either way and the result is **Unclear**. Our login cannot see another company's ids, so for now these probes are recorded as Unclear. The script reports them as `UNCLEAR` unless you set `FOREIGN_IDS_SAME_INSTANCE=1` to confirm the ids are from another company on the same instance.

## Gated probes (change/create records → human approval required)
Category B (write-boundary) and E1 (prompt injection) are **off by default**. Per the course rule, a person approves anything that changes a record. After approval:
```bash
RUN_WRITE_PROBES=1 python run_boundary_probes.py
```
Write probes use idempotent no-op values (set a field to its current value) so that even if a boundary is broken, no stored content changes.

## Output
`results.json` (git-ignored — redact internal ids before committing any evidence). The reviewed, redacted catalogue is in `../catalogue/` (`attempts.md` + `attempt-NN.md`) and the findings for Broke results are in `../findings/`.

## Toward v1 (30 Nov)
This runner will be wrapped to the Eval-Harness interface (`agentswitch-harness.toml`, see `../../34-eval-harness/TASK_FORMAT.md`) so the boundary checks run as negative/refusal tasks in the 12-task suite.
