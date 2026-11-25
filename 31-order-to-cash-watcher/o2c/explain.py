"""Why one order, invoice or payment is in or out of the report (`python3 -m o2c --explain ID`).

    python3 -m o2c --explain SO-0042
    python3 -m o2c --explain SO-0042 --replay reports/recordings/keystone-2026-11-25

ID is a record id or number, as a suite failure names it (`missing=`, `extra=`, `wrong_age=`).
explain reads what report.build() reads (so a suite recording can answer it offline), finds the
record and walks its section's rules in order, saying for each whether it held and why.

The verdict is the report's own: the section's function in report.py (orders_not_invoiced,
invoices_overdue, payments_not_applied) run on that one record, so explain and the report can't
disagree. If the rule-by-rule walk ever reaches a different verdict, explain says so and shows the
report's. Nothing in report.py changes for this.

The output, on stdout, is for a person. It shows the record's values, which are customer data:
keep it in the seat. Exit 0 when the record is found, 5 when it isn't, 3 when the platform (or the
recording) can't be read.
"""
import json
import os
import sys
from pathlib import Path

from . import report
from .report import CANCELLED, NOT_INVOICED, RECEIVABLE, SUBMITTED
from .values import display_text

EXIT_NOT_FOUND = 5
RECORDING_META = "recording.json"          # written by o2c.suite --record
DOCSTATUS = {0: "draft", 1: "submitted", 2: "cancelled"}

# What report.build() reads, with the same filters, so the requests match a suite recording.
SECTION_READS = (
    ("SalesOrder", {"docstatus": SUBMITTED}, "SalesOrder.list"),
    ("Invoice", {"docstatus": SUBMITTED}, "Invoice.list"),
    ("Payment", {"type": "receive"}, "Payment.list"),
)
SECTIONS = {"SalesOrder": "1, orders not invoiced", "Invoice": "2, invoices overdue",
            "Payment": "3, payments not applied"}


def _t(value):
    return "(empty)" if value in (None, "") else display_text(value)


def _docstatus(value):
    return f"{_t(value)} ({DOCSTATUS[value]})" if value in DOCSTATUS else _t(value)


def _first_issue(issues, default):
    return issues[0]["reason"] if issues else default


# --- The rule-by-rule walks. Each returns (steps, verdict); a step is (held, text). ---------------

def order_steps(o, report_date, days):
    steps = []
    held = o.get("docstatus") == SUBMITTED
    steps.append((held, f"submitted (docstatus 1): docstatus is {_docstatus(o.get('docstatus'))}"))
    if not held:
        return steps, "out"
    held = o.get("invoiced_status") in NOT_INVOICED
    steps.append((held, f"not fully invoiced ({' or '.join(NOT_INVOICED)}): invoiced_status is {_t(o.get('invoiced_status'))}"))
    if not held:
        return steps, "out"
    issues = []
    day = report._date_value(o, "date", issues, report_date)
    if day is None:
        steps.append((False, f"a usable order date: {_first_issue(issues, 'no usable date')} (date is {_t(o.get('date'))}); "
                             "listed under 'Orders with unusable dates', not in the table"))
        return steps, "out"
    age = (report_date - day).days
    held = age > days
    steps.append((held, f"more than {days} days old by order date: dated {day}, {age} days before {report_date}"))
    return steps, "in" if held else "out"


def invoice_steps(inv, report_date):
    steps = []
    held = inv.get("docstatus") == SUBMITTED
    steps.append((held, f"submitted (docstatus 1): docstatus is {_docstatus(inv.get('docstatus'))}"))
    if not held:
        return steps, "out"
    direction = inv.get("direction", RECEIVABLE)
    held = direction == RECEIVABLE
    why = ("" if held else "; a supplier bill, never in this report" if direction == "payable"
           else "; unknown, so left out and listed as a data issue")
    steps.append((held, f"a customer invoice (direction receivable): direction is {_t(direction)}{why}"))
    if not held:
        return steps, "out"
    issues = []
    due = report._date_value(inv, "due_date", issues)
    if due is None:
        steps.append((False, f"a due date: {_first_issue(issues, 'none')} (due_date is {_t(inv.get('due_date'))}); "
                             "listed under 'Invoices with no due date', whatever its balance"))
        return steps, "no due date"
    held = due < report_date
    steps.append((held, f"due before the report date: due {due}, report date {report_date}"))
    if not held:
        return steps, "out"
    issues = []
    outstanding = report._amount_value(inv, "outstanding", issues)
    if outstanding is None:
        steps.append((False, f"a balance left (outstanding > 0): {_first_issue(issues, 'no amount')} "
                             f"(outstanding is {_t(inv.get('outstanding'))}); listed as a data issue"))
        return steps, "out"
    held = outstanding > 0
    steps.append((held, f"a balance left (outstanding > 0): outstanding is {outstanding}"))
    return steps, "overdue" if held else "out"


def payment_steps(p, report_date):
    steps = []
    held = p.get("type") == "receive"
    steps.append((held, f"incoming (type receive): type is {_t(p.get('type'))}"))
    if not held:
        return steps, "out"
    held = p.get("docstatus") != CANCELLED
    steps.append((held, f"not cancelled (drafts are kept): docstatus is {_docstatus(p.get('docstatus'))}"))
    if not held:
        return steps, "out"
    issues = []
    unallocated = report._amount_value(p, "unallocated", issues)
    if unallocated is None:
        steps.append((False, f"money not yet allocated (unallocated > 0): {_first_issue(issues, 'no amount')} "
                             f"(unallocated is {_t(p.get('unallocated'))}); listed as a data issue"))
        return steps, "out"
    held = unallocated > 0
    steps.append((held, f"money not yet allocated (unallocated > 0): unallocated is {unallocated}"))
    return steps, "in" if held else "out"


# --- The report's own verdict, from report.py ----------------------------------------------------

def report_verdict(entity, record, report_date, days):
    """(verdict, row): what report.py's section function does with this one record."""
    if entity == "SalesOrder":
        rows = report.orders_not_invoiced([record], report_date, days, [])
        return ("in", rows[0]) if rows else ("out", None)
    if entity == "Invoice":
        overdue, no_due = report.invoices_overdue([record], report_date, [])
        return ("overdue", overdue[0]) if overdue else ("no due date", no_due[0]) if no_due else ("out", None)
    rows = report.payments_not_applied([record], report_date, [])
    return ("in", rows[0]) if rows else ("out", None)


def walk(entity, record, report_date, days):
    if entity == "SalesOrder":
        return order_steps(record, report_date, days)
    if entity == "Invoice":
        return invoice_steps(record, report_date)
    return payment_steps(record, report_date)


# --- Finding the record and printing ---------------------------------------------------------------

def _matches(row, wanted):
    return wanted in (str(row.get("id") or ""), str(row.get("number") or ""))


def find(client, wanted, names):
    """[(entity, record, how)] for every record whose id or number is `wanted`.

    First the reads report.build() makes; only if nothing matches there, the same entities with no
    filter (a draft order, a supplier bill, an outgoing payment). A replay that lacks an unfiltered
    read says so instead of failing."""
    found, notes = [], []
    for entity, filters, tool in SECTION_READS:
        if tool not in names:
            notes.append(f"this login can't read {entity} ({tool} missing)")
            continue
        found += [(entity, r, "read as the report reads it") for r in client.list_all(entity, filters) if _matches(r, wanted)]
    if found:
        return found, notes
    for entity, _filters, tool in SECTION_READS:
        if tool not in names:
            continue
        try:
            rows = client.list_all(entity)
        except Exception as e:                    # replay without this read, or a platform error
            if "replay:" in str(e):
                notes.append(f"the recording has no unfiltered {entity} read, so records the report never reads "
                             "can't be checked offline")
                continue
            raise
        found += [(entity, r, "not among the records the report reads; found with no filter")
                  for r in rows if _matches(r, wanted)]
    return found, notes


def describe(entity, record, how, report_date, days, currencies):
    """The explanation for one record, as lines of text."""
    steps, walked = walk(entity, record, report_date, days)
    verdict, row = report_verdict(entity, record, report_date, days)
    code, source = report.resolve_currency(record.get("currency_code"), record.get("party_id"),
                                           record.get("company_id"), currencies)
    lines = [f"{entity} {_t(record.get('number'))} (id {_t(record.get('id'))}), customer {_t(record.get('_party_id_display'))}",
             f"  {how}",
             f"  Section {SECTIONS[entity]}: {verdict.upper()}"]
    lines += [f"    {'yes' if held else 'NO '}  {text}" for held, text in steps]
    if walked != verdict:
        lines.append(f"  warning: the rule-by-rule walk says {walked.upper()}, but report.py says {verdict.upper()}; "
                     "the report's verdict is the one above. Please report this: explain is out of date")
    if row is not None:
        extra = {"SalesOrder": f"age bucket {row.get('age_bucket')}, total {row.get('grand_total')}",
                 "Invoice": f"{row.get('days_overdue')} days overdue, outstanding {row.get('outstanding')}, "
                            f"{row.get('payment_state')}" if verdict == "overdue" else
                            f"outstanding {row.get('outstanding')}, {row.get('payment_state')}",
                 "Payment": f"age {row.get('age_days')} days ({row.get('age_bucket')}), unallocated {row.get('unallocated')}, "
                            f"status {_t(row.get('status'))}" + (f" ({'; '.join(row['notes'])})" if row.get("notes") else "")}
        lines.append(f"  In the report: {extra[entity]}")
    lines.append(f"  Currency: {code + ' from the ' + source if code else 'not recorded (record, customer and company all empty)'}"
                 f"{'; marked fixture (looks like test data)' if report.looks_like_fixture(record) else ''}")
    others = [s for e, s in SECTIONS.items() if e != entity]
    lines.append(f"  Sections {others[0].split(',')[0]} and {others[1].split(',')[0]}: not this kind of record")
    return lines


def read_recording_meta(folder):
    try:
        return json.loads((Path(folder) / RECORDING_META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run(client, wanted, report_date, days, out=None):
    """Print the explanation of `wanted`; return the exit code."""
    out = out or sys.stdout
    names = client.tool_names()
    currencies = report.currency_context(client, names)
    found, notes = find(client, wanted, names)
    print(f"Explaining {display_text(wanted)}: report date {report_date}, N = {days} days", file=out)
    for note in notes:
        print(f"  note: {note}", file=out)
    if not found:
        print(f"  {display_text(wanted)} is not the id or number of any order, invoice or payment this login can read",
              file=out)
        return EXIT_NOT_FOUND
    for entity, record, how in found:
        print("", file=out)
        print("\n".join(describe(entity, record, how, report_date, days, currencies)), file=out)
    return 0


def resolve_days(args, recorded, days_option):
    """--days, else the recording's, else O2C_DAYS, else 7 (as the report). Raises ValueError."""
    if args.days is not None:
        return args.days
    if recorded.get("days") is not None:
        return days_option(str(recorded["days"]))
    return days_option(os.environ.get("O2C_DAYS", "7"))
