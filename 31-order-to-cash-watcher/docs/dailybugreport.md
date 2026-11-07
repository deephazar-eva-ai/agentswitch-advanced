# Seat 31 — Daily bug report

Bugs raised by seat 31, in the format in `BUG_REPORT_FORMAT.md`. Newest day last. No passwords, tokens or customer data: the repository is public.

| ID | Title | System | Severity | Status |
|---|---|---|---|---|
| BUG-31-001 | Invoice and PaymentReceived read tools are not available to seat 31 | Both | Medium | Open |
| BUG-31-002 | Login `team31a` has no mailbox, so reminder drafts cannot be saved | Both | Medium | Open |

---

## 7 Nov 2026

### BUG-31-001: Invoice and PaymentReceived read tools are not available to seat 31

| Field | Value |
|---|---|
| Seat and login | 31, `team31a` |
| System | Suryodaya and Keystone |
| Date and time (IST) | First seen 6 Nov, about 11:20 pm. Checked again 7 Nov at 00:11 and later on 7 Nov; same result each time |
| Severity | Medium: blocks a first-task section; no data was changed |
| Repeats | Every time, on both systems |
| Status | Open, raised in the seat group on 7 Nov |

**Steps**
1. Log in as `team31a` (`POST /api/auth/login`).
2. Call MCP `tools/call` with `{"name": "Invoice.list", "arguments": {"limit": 10}}`.
3. Call `PaymentReceived.list` the same way.
4. Call `tools/list` and `tools.search` with "invoice".
5. Call `endpoint.make.thread` on an order that has an invoice.

**Expected**
The 3 Nov access update gives seat 31 the accounting tools. The instructor's 7 Nov answer says to read `due_date`, `payment_terms`, `amount_paid` and `outstanding` from Invoice, and to use PaymentReceived for applied receipts (215 on Suryodaya, 141 on Keystone). So both `.list` tools should return rows.

**Actual**
- Steps 2–3 reply `tool_not_available`: "This tool is not available to your seat; it is not in your tools/list".
- Step 4: `tools/list` has 408 tools on each system, none for Invoice or PaymentReceived. `tools.search` "invoice" returns only `endpoint.make.thread`.
- Step 5: the invoice node comes back as `{"entity": "Invoice", "redacted": true}`. WorkOrder and DeliveryChallan are redacted too.

**Evidence**
The request and reply text are above. Nothing else is attached, so no platform data is included.

**Impact**
The "invoices overdue" section of the 15 Nov report is blocked, and so is matching the 40 unallocated Suryodaya payments to invoices. Suite tasks 5–6 (`overdue_invoices`, `overdue_ageing`) depend on it. The rule is already written (commit `94e14fd`) and switches on as soon as `Invoice.list` is in our tool list, so no code change is needed on our side.

**Ask**
Please give `team31a` read access to `Invoice.list` / `.get` and `PaymentReceived.list` / `.get` on both systems. If seat 31 isn't meant to have them, please tell us how to read invoice due dates and balances instead.

**Log**
- 6 Nov, ~11:20 pm IST: found during the first exploration. Invoice nodes redacted; no Invoice tool.
- 7 Nov, 00:11 IST: after the instructor's answer, tried `Invoice.list` and `PaymentReceived.list` directly and got `tool_not_available` on both systems.
- 7 Nov, later: `tools/list` checked again, 408 tools, still none. Posted in the seat group for the admin.

### BUG-31-002: Login `team31a` has no mailbox, so reminder drafts cannot be saved

| Field | Value |
|---|---|
| Seat and login | 31, `team31a` |
| System | Suryodaya and Keystone |
| Date and time (IST) | 7 Nov 2026, evening |
| Severity | Medium: blocks saving reminder drafts (v1); no data was changed |
| Repeats | Every time, on both systems |
| Status | Open, to raise in the seat group |

**Steps**
1. Log in as `team31a`.
2. Call MCP `tools/call` with `{"name": "EmailAccount.list", "arguments": {"limit": 20}}`.
3. Read the `EmailMessage.create` schema in `tools/list`.

**Expected**
The brief says the agent drafts reminders for a person to send, and the 3 Nov access update grants email tools. So we expected a mailbox for our login, where drafts can be saved.

**Actual**
- Step 2 returns 0 rows on both systems.
- Step 3: `EmailMessage.create` requires `mailbox_id`, so with no mailbox no draft can be saved.

We did not try to create a mailbox or a message.

**Impact**
Suite task 9 (`reminder_is_draft`) cannot pass, and the demo cannot show a saved draft. Meanwhile drafts are written to a local review file (`python -m o2c.drafts`), and saving them needs only `--create --mailbox-id <id>` once a mailbox exists.

**Ask**
Please give `team31a` a mailbox on both systems, or tell us which mailbox seat 31 should save drafts in. We only create drafts; we never send.
