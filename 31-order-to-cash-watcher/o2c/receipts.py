"""Applied customer receipts (PaymentReceived): the history side of "payments not applied".

Instructor, 7 Nov: Payment rows of type `receive` are money in that is not yet applied (the rows
to match). PaymentReceived rows are receipts already applied, each carrying an allocation to one
invoice. The two are related only through the customer: Payment.party_id = PaymentReceived.customer_id.

Our login cannot read PaymentReceived yet (BUG-31-001). Everything here is a pure function over
rows, so it is tested on made-up rows and switches on in the report as soon as
`PaymentReceived.list` appears in the tool list.

Field names come from the PaymentReceived schema snapshot. Two models exist:
  * v1 (`allocation_evidence_version == 1`): allocations in `applied_allocations[]`
    (`target_entity`, `target_id`, `amount`), with an `allocation_history[]` event log.
  * legacy: allocations in `invoices[]` (`invoice_id`, `payment`).
The schema's computed `total_applied` is None on paid legacy receipts, so we never rely on it:
the applied amount is summed from the child rows, and only `paid` receipts count.
"""

PAID = "paid"
TOLERANCE = 0.01


def _money(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def is_v1(receipt):
    try:
        return float(receipt.get("allocation_evidence_version")) == 1
    except (TypeError, ValueError):
        return False


def invoice_allocations(receipt):
    """[(invoice_id, amount)] for one receipt, from whichever child table its model uses."""
    if is_v1(receipt):
        return [(a.get("target_id"), _money(a.get("amount")))
                for a in receipt.get("applied_allocations") or [] if (a.get("target_entity") or "Invoice") == "Invoice"]
    return [(i.get("invoice_id"), _money(i.get("payment"))) for i in receipt.get("invoices") or []]


def applied_amount(receipt):
    return round(sum(a for _, a in invoice_allocations(receipt)), 2)


def counts_as_applied(receipt):
    """Draft receipts are not received yet and cancelled ones are void."""
    return receipt.get("status") == PAID


def history_net(receipt):
    """Net of the allocation_history log. An event that names `reverses_event_id` takes its amount
    back. We key on that field rather than on `event_type`, whose values the schema does not list."""
    net = 0.0
    for ev in receipt.get("allocation_history") or []:
        amt = _money(ev.get("amount"))
        net += -amt if ev.get("reverses_event_id") else amt
    return round(net, 2)


def schema_totals(receipt):
    """(total_applied, unused_amount) exactly as the schema computes them; None where it gives None."""
    if is_v1(receipt):
        total = round(sum(_money(a.get("amount")) for a in receipt.get("applied_allocations") or []), 2)
        consid = receipt.get("allocation_consideration")
        return total, (None if consid is None else round(_money(consid) - total, 2))
    if receipt.get("status") == "draft":
        total = round(sum(_money(i.get("payment")) for i in receipt.get("invoices") or []), 2)
        amount = receipt.get("amount")
        return total, (None if amount is None else round(_money(amount) - total, 2))
    return None, None


def receipt_issues(receipt):
    """Things worth a person's look. Empty list when the receipt is consistent."""
    issues = []
    allocs = invoice_allocations(receipt)
    if len(allocs) > 1:
        issues.append(f"{len(allocs)} invoice allocations (expected one per receipt)")
    if is_v1(receipt) and counts_as_applied(receipt) and receipt.get("allocation_history"):
        net = history_net(receipt)
        if abs(net - applied_amount(receipt)) > TOLERANCE:
            issues.append(f"allocations {applied_amount(receipt):,.2f} but history nets to {net:,.2f}")
    _, unused = schema_totals(receipt)
    if unused is not None and unused < -TOLERANCE:
        issues.append(f"over-applied by {-unused:,.2f}")
    return issues


def history_by_customer(receipts):
    """customer_id -> {"receipts": n, "paid": [rows], "applied_total": x, "last_date": d}"""
    out = {}
    for r in receipts:
        cid = r.get("customer_id")
        if not cid:
            continue
        h = out.setdefault(cid, {"receipts": 0, "paid": [], "applied_total": 0.0, "last_date": None})
        h["receipts"] += 1
        if counts_as_applied(r):
            h["paid"].append(r)
            h["applied_total"] = round(h["applied_total"] + applied_amount(r), 2)
            d = str(r.get("date") or "")[:10] or None
            if d and (h["last_date"] is None or d > h["last_date"]):
                h["last_date"] = d
    return out


HINTS = {
    "same_amount": "same customer and amount already recorded as an applied receipt; check before applying",
    "has_history": "customer has applied receipts; match against their open invoices",
    "no_history": "no applied receipts for this customer",
}


def hint_for(payment_row, history, tolerance=TOLERANCE):
    """Receipt-history facts for one unapplied payment row (from report.payments_not_applied)."""
    h = history.get(payment_row.get("party_id")) or {"receipts": 0, "paid": [], "applied_total": 0.0, "last_date": None}
    amount = _money(payment_row.get("unallocated"))
    same = sorted(r.get("payment_number") or r.get("id") or "?" for r in h["paid"]
                  if abs(_money(r.get("amount")) - amount) <= tolerance)
    key = "same_amount" if same else "has_history" if h["paid"] else "no_history"
    return {"hint": key, "receipts_paid": len(h["paid"]), "receipts_applied_total": h["applied_total"],
            "last_receipt_date": h["last_date"], "same_amount_receipts": same}
