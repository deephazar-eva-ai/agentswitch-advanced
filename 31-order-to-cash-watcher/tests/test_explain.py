"""`python3 -m o2c --explain ID`: why one record is in or out of the report. Made-up records only."""
import io
import itertools
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from unittest import mock

from o2c import __main__ as cli, explain, report

DAY = date(2026, 11, 25)


def order(**extra):
    row = {"id": "so-1", "number": "SO-1", "docstatus": 1, "invoiced_status": "unbilled", "date": "2026-11-01",
           "grand_total": 100, "party_id": "P", "company_id": "CO", "_party_id_display": "Synthetic"}
    row.update(extra)
    return row


def invoice(**extra):
    row = {"id": "inv-1", "number": "INV-1", "docstatus": 1, "direction": "receivable", "due_date": "2026-11-01",
           "outstanding": 50, "amount_paid": 0, "grand_total": 50, "party_id": "P", "_party_id_display": "Synthetic"}
    row.update(extra)
    return row


def payment(**extra):
    row = {"id": "pay-1", "number": "PAY-1", "type": "receive", "docstatus": 1, "date": "2026-11-01",
           "amount": 30, "unallocated": 30, "status": "submitted", "party_id": "P", "_party_id_display": "Synthetic"}
    row.update(extra)
    return row


class Fake:
    """A read-only platform that applies list filters, as the real one does."""

    def __init__(self, orders=(), invoices=(), payments=(), names=None):
        self.data = {"SalesOrder": list(orders), "Invoice": list(invoices), "Payment": list(payments),
                     "Party": [{"id": "P", "currency_id": "USD"}], "Company": [{"id": "CO", "default_currency": "INR"}]}
        self.names = names or {"SalesOrder.list", "Invoice.list", "Payment.list", "Party.list", "Company.list"}
        self.reads = []

    def tool_names(self):
        return set(self.names)

    def list_all(self, entity, filters=None):
        self.reads.append((entity, dict(filters or {})))
        return [dict(r) for r in self.data[entity] if all(r.get(k) == v for k, v in (filters or {}).items())]


def run(client, wanted, days=7):
    out = io.StringIO()
    code = explain.run(client, wanted, DAY, days, out)
    return code, out.getvalue()


class WalkAgreesWithReport(unittest.TestCase):
    """The rule-by-rule walk reaches the same verdict as report.py on every combination tried."""

    def check(self, entity, rows, days=7):
        for row in rows:
            steps, walked = explain.walk(entity, row, DAY, days)
            verdict, _ = explain.report_verdict(entity, row, DAY, days)
            self.assertEqual(walked, verdict, (entity, row))
            self.assertTrue(steps)

    def test_orders(self):
        rows = [order(docstatus=d, invoiced_status=s, date=t) for d, s, t in itertools.product(
            (0, 1, 2, None), ("unbilled", "partially_invoiced", "invoiced", None),
            ("2026-11-01", "2026-11-18", "2026-11-17", "2026-11-25", "2026-12-01", None, "", "not a date"))]
        self.check("SalesOrder", rows)
        self.check("SalesOrder", rows, days=0)

    def test_invoices(self):
        rows = [invoice(docstatus=d, direction=w, due_date=t, outstanding=o) for d, w, t, o in itertools.product(
            (0, 1, 2), ("receivable", "payable", "Receivable", ""), ("2026-11-01", "2026-11-25", "2026-12-01", None, "x"),
            (50, 0, -1, None, "bad"))]
        rows.append({k: v for k, v in invoice().items() if k != "direction"})     # older rows: receivable
        self.check("Invoice", rows)

    def test_payments(self):
        rows = [payment(type=t, docstatus=d, unallocated=u, date=w) for t, d, u, w in itertools.product(
            ("receive", "pay", None), (0, 1, 2), (30, 0, -5, None, "bad"), ("2026-11-01", "2026-12-01", None))]
        self.check("Payment", rows)


class Explains(unittest.TestCase):
    def test_an_order_in_the_report_says_why(self):
        code, text = run(Fake(orders=[order()]), "SO-1")
        self.assertEqual(code, 0)
        self.assertIn("Section 1, orders not invoiced: IN", text)
        self.assertIn("more than 7 days old by order date: dated 2026-11-01, 24 days before 2026-11-25", text)
        self.assertIn("Currency: USD from the customer", text)
        self.assertNotIn("NO ", text)

    def test_the_failing_rule_is_named(self):
        _, text = run(Fake(orders=[order(date="2026-11-20")]), "SO-1")
        self.assertIn("OUT", text)
        self.assertIn("NO   more than 7 days old by order date: dated 2026-11-20, 5 days before", text)
        _, text = run(Fake(orders=[order(invoiced_status="invoiced")]), "so-1")      # by id too
        self.assertIn("NO   not fully invoiced", text)
        self.assertNotIn("days old", text)                     # stops at the first rule that fails

    def test_an_unusable_date_points_to_its_list(self):
        _, text = run(Fake(orders=[order(date="2026-12-01")]), "SO-1")
        self.assertIn("future date", text)
        self.assertIn("Orders with unusable dates", text)

    def test_invoices(self):
        _, text = run(Fake(invoices=[invoice()]), "INV-1")
        self.assertIn("Section 2, invoices overdue: OVERDUE", text)
        self.assertIn("In the report: 24 days overdue", text)
        _, text = run(Fake(invoices=[invoice(due_date=None)]), "INV-1")
        self.assertIn("NO DUE DATE", text)
        _, text = run(Fake(invoices=[invoice(direction="payable")]), "INV-1")
        self.assertIn("a supplier bill, never in this report", text)
        _, text = run(Fake(invoices=[invoice(outstanding=0)]), "INV-1")
        self.assertIn("NO   a balance left (outstanding > 0): outstanding is 0", text)

    def test_payments(self):
        _, text = run(Fake(payments=[payment(docstatus=0)]), "PAY-1")
        self.assertIn("Section 3, payments not applied: IN", text)
        self.assertIn("0 (draft)", text)
        _, text = run(Fake(payments=[payment(unallocated=0)]), "PAY-1")
        self.assertIn("NO   money not yet allocated", text)

    def test_a_record_the_report_never_reads_is_found_without_a_filter(self):
        fake = Fake(orders=[order(docstatus=0)], payments=[payment(type="pay", number="PAY-9")])
        code, text = run(fake, "SO-1")
        self.assertEqual(code, 0)
        self.assertIn("not among the records the report reads; found with no filter", text)
        self.assertIn("NO   submitted (docstatus 1): docstatus is 0 (draft)", text)
        _, text = run(fake, "PAY-9")
        self.assertIn("NO   incoming (type receive): type is pay", text)

    def test_the_first_reads_are_the_reports_own(self):
        fake = Fake(orders=[order()])
        run(fake, "SO-1")
        self.assertEqual(fake.reads, [("Party", {}), ("Company", {}), ("SalesOrder", {"docstatus": 1}),
                                      ("Invoice", {"docstatus": 1}), ("Payment", {"type": "receive"})])

    def test_not_found(self):
        code, text = run(Fake(orders=[order()]), "SO-404")
        self.assertEqual(code, explain.EXIT_NOT_FOUND)
        self.assertIn("is not the id or number of any order, invoice or payment", text)

    def test_a_login_without_invoices_says_so(self):
        fake = Fake(orders=[order()], names={"SalesOrder.list", "Payment.list"})
        code, text = run(fake, "SO-1")
        self.assertEqual(code, 0)
        self.assertIn("note: this login can't read Invoice (Invoice.list missing)", text)

    def test_a_replay_without_the_unfiltered_read_says_so(self):
        class NoUnfiltered(Fake):
            def list_all(self, entity, filters=None):
                if not filters and entity in ("SalesOrder", "Invoice", "Payment"):
                    raise cli.McpError(f"replay: no recorded reply for tools/call {entity}.list")
                return super().list_all(entity, filters)
        code, text = run(NoUnfiltered(orders=[order(docstatus=0)]), "SO-1")
        self.assertEqual(code, explain.EXIT_NOT_FOUND)
        self.assertIn("the recording has no unfiltered SalesOrder read", text)

    def test_record_text_never_carries_control_characters(self):
        _, text = run(Fake(orders=[order(_party_id_display="Acme\x1b[31m‮")]), "SO-1")
        self.assertNotIn("\x1b", text)
        self.assertNotIn("‮", text)

    def test_report_py_is_what_decides(self):
        """If the walk ever drifts from report.py, the report's verdict is shown, with a warning."""
        with mock.patch.object(explain, "order_steps", return_value=([(True, "stale rule")], "in")):
            _, text = run(Fake(orders=[order(invoiced_status="invoiced")]), "SO-1")
        self.assertIn("Section 1, orders not invoiced: OUT", text)
        self.assertIn("warning: the rule-by-rule walk says IN, but report.py says OUT", text)


class Command(unittest.TestCase):
    def main(self, *argv, client=None):
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k not in ("AGENTSWITCH_INSTANCE", "O2C_DAYS")}
        with mock.patch.object(cli, "Client", lambda: client or Fake(orders=[order()])), \
                mock.patch.dict("os.environ", env, clear=True), redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main(list(argv))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_explain_prints_no_id_list_and_no_agent_result(self):
        code, out, _ = self.main("--explain", "SO-1", "--date", "2026-11-25")
        self.assertEqual(code, 0)
        self.assertIn("Section 1, orders not invoiced: IN", out)
        self.assertNotIn("AGENT_RESULT", out)
        self.assertNotIn("so-1\n", out.splitlines()[0])

    def test_days_option_reaches_the_rules(self):
        _, out, _ = self.main("--explain", "SO-1", "--date", "2026-11-25", "--days", "30")
        self.assertIn("NO   more than 30 days old", out)

    def test_not_found_exits_5(self):
        code, _, _ = self.main("--explain", "nope", "--date", "2026-11-25")
        self.assertEqual(code, 5)

    def test_usage_errors(self):
        for argv in (("--explain", "SO-1", "--prompt", "x"), ("--explain", "SO-1", "--markdown"),
                     ("--explain", "SO-1", "--out", "reports"), ("--explain", " "), ("--replay", "somewhere"),
                     ("--explain", "SO-1", "--replay", "/nonexistent-recording")):
            code, out, err = self.main(*argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(out, "", argv)

    def test_a_platform_error_exits_3(self):
        class Down(Fake):
            def tool_names(self):
                raise cli.McpError("platform unreachable")
        code, out, err = self.main("--explain", "SO-1", client=Down())
        self.assertEqual(code, 3)
        self.assertIn("o2c: could not complete: platform unreachable", err)

    def test_replay_from_a_recording(self):
        """Recorded through the real RecordingClient on a made-up platform, then replayed by the command."""
        import json
        import tempfile
        from pathlib import Path
        from o2c.client import Client, RecordingClient

        fake = Fake(orders=[order()])

        class FakePlatform(Client):
            def _rpc(self, method, params):
                if method == "tools/list":
                    return {"tools": [{"name": n} for n in sorted(fake.names)]}
                args = {k: v for k, v in params["arguments"].items() if k not in ("limit", "offset", "sort_by", "sort_order")}
                rows = fake.list_all(params["name"].split(".")[0], args)[params["arguments"]["offset"]:]
                return {"content": [{"type": "text", "text": json.dumps({"data": rows, "total": len(rows)})}]}

        class Recorder(RecordingClient, FakePlatform):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "rec"
            rep = report.build(Recorder(folder, "http://x", "t"), report_date=DAY, days=7)
            self.assertEqual(len(rep["sections"]["orders_not_invoiced"]["rows"]), 1)
            (folder / "recording.json").write_text(json.dumps({"instance": "keystone", "date": "2026-11-25", "days": 7}))
            class NoPlatform(Fake):
                def tool_names(self):
                    raise AssertionError("replay must not use the platform")
            code, out, err = self.main("--explain", "SO-1", "--replay", str(folder), client=NoPlatform())
            self.assertEqual(code, 0, err)
            self.assertIn("report date 2026-11-25, N = 7 days", out)
            self.assertIn("Section 1, orders not invoiced: IN", out)
            code, _, err = self.main("--explain", "SO-1", "--replay", str(folder), "--instance", "suryodaya")
            self.assertEqual(code, 2)
            self.assertIn("the recording is of keystone", err)

if __name__ == "__main__":
    unittest.main()
