"""Review of PR #54 (Sunil, 20 Nov): reminder subjects carry the date; older unsent drafts are pointed out;
event-stream data loses exactly one leading space; the retry budget holds for the whole request."""
import io
import socket
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from o2c import client as C, drafts
from o2c.client import Client, PlatformUnavailable, _response_message


def inv(n, party="P1", out=100.0):
    return {"number": n, "id": n, "party_id": party, "customer": "Acme Ltd", "due_date": "2026-10-01",
            "outstanding": out, "currency": "INR", "payment_state": "unpaid", "fixture": False}


PARTIES = {"P1": {"id": "P1", "name": "Acme Ltd", "email": "ap@acme.example"}}


class ReminderSubjectDate(unittest.TestCase):
    def test_the_subject_carries_the_report_date(self):
        (d,), _ = drafts.overdue_reminders([inv("I1")], PARTIES, report_date="2026-11-20")
        self.assertTrue(d["subject"].startswith("Payment reminder 2026-11-20: 1 overdue invoice"), d["subject"])

    def test_same_count_and_amount_on_another_day_is_a_new_draft(self):
        (old,), _ = drafts.overdue_reminders([inv("I1")], PARTIES, report_date="2026-11-19")
        (new,), _ = drafts.overdue_reminders([inv("I9")], PARTIES, report_date="2026-11-20")
        existing = {drafts._draft_key(old["to"], old["subject"])}
        ready, dupes = drafts.without_duplicates([new], existing)
        self.assertEqual((len(ready), len(dupes)), (1, 0))


class OlderDrafts(unittest.TestCase):
    def test_an_older_reminder_or_digest_to_the_same_recipient_is_pointed_out(self):
        (new,), _ = drafts.overdue_reminders([inv("I1", out=250.0)], PARTIES, report_date="2026-11-20")
        existing = {drafts._draft_key("ap@acme.example", "Payment reminder 2026-11-19: 1 overdue invoice, 100.00 INR"),
                    drafts._draft_key("other@x.example", "Payment reminder 2026-11-19: 1 overdue invoice, 100.00 INR"),
                    drafts._draft_key("ap@acme.example", "Quote follow-up"),
                    drafts._draft_key(new["to"], new["subject"])}
        older = drafts.older_drafts([new], existing)
        self.assertEqual(older, [("ap@acme.example", "Payment reminder 2026-11-19: 1 overdue invoice, 100.00 INR")])

    def test_a_digest_whose_parts_changed_is_pointed_out(self):
        new = {"to": "acc@x.example", "subject": "Order-to-cash exceptions 2026-11-20: 9 orders (part 1 of 2)"}
        existing = {drafts._draft_key("acc@x.example", "Order-to-cash exceptions 2026-11-20: 8 orders (part 1 of 3)")}
        self.assertEqual(len(drafts.older_drafts([new], existing)), 1)

    def test_create_prints_the_check_line_and_still_saves(self):
        prop = {"drafts": [{"kind": "accounts_digest", "to": "acc@x.example", "fixture": False, "body_text": "B",
                            "subject": "Order-to-cash exceptions 2026-11-20: 9 orders"}],
                "report_date": "2026-11-20", "match_summary": {"payments": 0, "with_candidates": 0}}
        writer = mock.Mock()
        writer.create.return_value = {"id": "m1"}
        with mock.patch.multiple(drafts, Client=mock.Mock(), propose=mock.Mock(return_value=prop), write_pair=mock.Mock(),
                                 to_markdown=mock.Mock(return_value=""), DraftWriter=mock.Mock(return_value=writer),
                                 find_mailbox=mock.Mock(return_value={"id": "M", "email": "me@x.example", "company_id": "C"}),
                                 existing_drafts=mock.Mock(return_value={("acc@x.example", "Order-to-cash exceptions 2026-11-19: 8 orders")})), \
                mock.patch("builtins.input", return_value="save drafts"), \
                mock.patch.object(sys, "argv", ["o2c.drafts", "--out", "unused", "--create", "--instance", "x"]), \
                redirect_stdout(io.StringIO()) as out:
            drafts.main()
        self.assertIn("check: an older unsent draft to x.example", out.getvalue())
        writer.create.assert_called_once()


class EventStreamSpace(unittest.TestCase):
    def test_exactly_one_leading_space_is_removed(self):
        raw = 'data:  {"jsonrpc":"2.0","id":1,\ndata:   "result":{"v":"a"}}\n\n'
        self.assertEqual(_response_message(raw, "text/event-stream", 200, 1, "t")["result"], {"v": "a"})
        # the payload lines keep their own indentation after the one space SSE removes
        payloads = []
        with mock.patch.object(C.json, "loads", side_effect=lambda t, **k: payloads.append(t) or {"id": 1, "result": {}}):
            _response_message("data:  x\ndata:   y\n\n", "text/event-stream", 200, 1, "t")
        self.assertEqual(payloads, [" x\n  y"])


class RetryBudget(unittest.TestCase):
    def test_a_read_that_keeps_timing_out_stops_by_the_budget(self):
        clock = [0.0]
        timeouts = []
        c = Client("https://example.invalid", "t")

        def post(body):
            t = getattr(c, "_try_timeout", None) or c.timeout
            timeouts.append(t)
            clock[0] += t                     # each try waits its full timeout
            raise socket.timeout()

        c._post = post
        c._sleep = lambda s: clock.__setitem__(0, clock[0] + s)
        with mock.patch.object(C.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(PlatformUnavailable):
                c._rpc("tools/call", {"name": "Payment.list", "arguments": {}})
        self.assertLessEqual(clock[0], C.RETRY_BUDGET + 0.001)
        self.assertEqual(timeouts[0], c.timeout)
        self.assertLess(timeouts[1], c.timeout)          # the second try only gets what is left

    def test_quick_transient_errors_still_get_all_four_tries(self):
        c = Client("https://example.invalid", "t")
        calls = []
        c._post = lambda body: calls.append(1) or (_ for _ in ()).throw(socket.timeout())
        c._sleep = lambda s: None
        with self.assertRaises(PlatformUnavailable):
            c._rpc("tools/call", {"name": "Payment.list", "arguments": {}})
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
