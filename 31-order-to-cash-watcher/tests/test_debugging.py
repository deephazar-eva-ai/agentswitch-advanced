"""Debugging support (25 Nov): --debug, platform call counts, record and replay, results.meta.json,
and the report command's handling of an unexpected error. No platform calls."""
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from o2c import __main__ as cli
from o2c import client as C
from o2c import debug, drafts, suite, tasks_payments

TOKEN = "tok-SECRET-123"


class Scripted(C.Client):
    """A client whose platform fails `fails` times with HTTP 503, then answers each Deal-style
    list with the next list in `lists`. No network, no real waiting."""
    def __init__(self, fails=0, lists=([],), reject=False):
        super().__init__("http://x", TOKEN)
        self.fails, self.lists, self.reject, self._sleep = fails, list(lists), reject, lambda s: None

    def _post(self, body):
        msg = json.loads(body)
        if self.reject:
            raise urllib.error.HTTPError("http://x/api/mcp", 401, "Unauthorized", {}, None)
        if self.fails:
            self.fails -= 1
            raise urllib.error.HTTPError("http://x/api/mcp", 503, "Service Unavailable", {}, None)
        if msg["method"] == "tools/list":
            result = {"tools": [{"name": "SalesOrder.list"}]}
        else:
            data = self.lists.pop(0) if len(self.lists) > 1 else self.lists[0]
            result = {"structuredContent": {"data": data, "total": len(data)}}
        return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})


class Recording(C.RecordingClient):
    """A recording client on Scripted's made-up platform."""
    def __init__(self, folder, fails=0, lists=([],), reject=False):
        super().__init__(folder, "http://x", TOKEN)
        self.fails, self.lists, self.reject, self._sleep = fails, list(lists), reject, lambda s: None

    _post = Scripted._post


class Counting(Recording):
    """A recording client that notes every request that reaches its transport, by tool."""
    def __init__(self, folder, **kw):
        super().__init__(folder, **kw)
        self.sent = []

    def _post(self, body):
        msg = json.loads(body)
        self.sent.append((msg.get("params") or {}).get("name") or msg["method"])
        return Scripted._post(self, body)


MAILBOX = {"id": "mb1", "company_id": "c1", "email": "ar@seller.example"}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        debug.ENABLED, debug.current = False, ""


class PlatformStats(Base):
    def test_requests_retries_and_tools_are_counted(self):
        c = Scripted(fails=2)
        c.call("SalesOrder.list")
        c.call("Invoice.list")
        s = C.stats_of(c)
        self.assertEqual((s["requests"], s["retries"], s["waited"]), (4, 2, 3.0))
        self.assertEqual(dict(s["by_tool"]), {"SalesOrder.list": 1, "Invoice.list": 1})

    def test_debug_names_each_retry_and_a_blocked_write(self):
        debug.ENABLED, err = True, io.StringIO()
        c = Scripted(fails=1)
        with redirect_stderr(err):
            c.call("SalesOrder.list")
            with self.assertRaises(C.WriteBlocked):
                c.call("EmailMessage.send_email", {"id": "m1"})
        self.assertIn("debug: retry 1 of tools/call SalesOrder.list after HTTP 503 Service Unavailable, waiting 1 s",
                      err.getvalue())
        self.assertIn("debug: blocked: EmailMessage.send_email is not a read tool, nothing was sent", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())

    def test_quiet_without_debug(self):
        err = io.StringIO()
        with redirect_stderr(err):
            Scripted(fails=1).call("SalesOrder.list")
        self.assertEqual(err.getvalue(), "")

    def test_a_client_without_stats_counts_zero(self):
        self.assertEqual(C.stats_of(object())["requests"], 0)
        self.assertEqual(C.stats_of(None)["requests"], 0)


class DebugOutputIsCleaned(Base):
    """Bug hunt 26 Nov: --debug printed server text and record ids raw (E2)."""
    def test_traceback_want_got_and_step_lines_carry_no_control_characters(self):
        debug.ENABLED, err = True, io.StringIO()
        with redirect_stderr(err):
            try:
                C.tool_result({"isError": True, "content": [{"type": "text", "text": "denied\x1b[2J\x1b]0;x\x07"}]},
                              "Invoice.list")
            except C.McpError as e:
                debug.crash(e, "o2c")
            debug.diff("INV\x1b[31m1", "outstanding", 1, 2)
            debug.step("task \x1b[1mbold")
        text = err.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("McpError: Invoice.list: denied", text)


class SuiteExitCode(Base):
    """Bug hunt 26 Nov: the suite exited 0 with failed tasks, so `git bisect run` saw every commit as good."""
    def run_main(self, task):
        with mock.patch.object(suite, "Client", lambda: Scripted()), \
                mock.patch.object(suite, "all_tasks", lambda: [("overdue_invoices", "T5", task)]), \
                mock.patch.dict("os.environ", {"AGENTSWITCH_BASE_URL": "http://x", "AGENTSWITCH_TOKEN": TOKEN}, clear=True), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as ran:
            suite.main(["--instance", "keystone", "--date", "2026-11-30"])
        return ran.exception.code

    def test_zero_when_every_task_passes_one_when_any_fails(self):
        self.assertEqual(self.run_main(lambda ctx: tasks_payments._ok(True, "fine")), 0)
        self.assertEqual(self.run_main(lambda ctx: tasks_payments._ok(False, "wrong")), 1)

    def test_a_crashed_task_is_a_failure(self):
        def boom(ctx):
            raise KeyError("rows")
        self.assertEqual(self.run_main(boom), 1)


class RecordReplay(Base):
    def test_a_folder_that_already_holds_a_recording_is_refused(self):
        """Recording twice into one folder would mix run 1's replies with run 2's recording.json. Review of #63."""
        Recording(self.tmp / "rec", lists=[[{"id": "so1"}]]).call("SalesOrder.list")
        before = (self.tmp / "rec" / C.RECORDING_FILE).read_bytes()
        with self.assertRaisesRegex(C.McpError, "already holds a recording"):
            Recording(self.tmp / "rec")
        self.assertEqual((self.tmp / "rec" / C.RECORDING_FILE).read_bytes(), before)

    def test_replay_gives_the_same_replies_in_order_without_a_network(self):
        rec = Recording(self.tmp / "rec", lists=[[{"id": "so1"}], [{"id": "so1"}, {"id": "so2"}]])
        live = [rec.tool_names(), rec.call("SalesOrder.list"), rec.call("SalesOrder.list")]
        rep = C.ReplayClient(self.tmp / "rec")
        self.assertEqual(live, [rep.tool_names(), rep.call("SalesOrder.list"), rep.call("SalesOrder.list")])
        self.assertEqual(rep.call("SalesOrder.list")["data"], [{"id": "so1"}, {"id": "so2"}])   # the last one again
        with self.assertRaises(C.McpError):
            rep._post(b"{}")

    def test_a_repeated_identical_reply_is_stored_once(self):
        rec = Recording(self.tmp / "rec", lists=[[{"id": "so1", "name": "x" * 1000}]])
        for _ in range(5):
            rec.call("SalesOrder.list")
        lines = (self.tmp / "rec" / "calls.jsonl").read_text().splitlines()
        self.assertEqual(sum(1 for line in lines if '"same": true' in line), 4)
        rep = C.ReplayClient(self.tmp / "rec")
        self.assertEqual([rep.call("SalesOrder.list")["data"][0]["id"] for _ in range(5)], ["so1"] * 5)

    def test_an_error_is_replayed_as_the_same_error(self):
        rec = Recording(self.tmp / "rec", reject=True)
        with self.assertRaises(C.McpError) as live:
            rec.call("SalesOrder.list")
        with self.assertRaises(C.McpError) as again:
            C.ReplayClient(self.tmp / "rec").call("SalesOrder.list")
        self.assertEqual(str(live.exception), str(again.exception))

    def test_a_request_the_recording_lacks_is_named(self):
        Recording(self.tmp / "rec").call("SalesOrder.list")
        with self.assertRaises(C.McpError) as e:
            C.ReplayClient(self.tmp / "rec").call("Invoice.list")
        self.assertIn("no recorded reply for tools/call Invoice.list", str(e.exception))

    def test_writes_are_still_blocked_and_never_recorded(self):
        rec = Recording(self.tmp / "rec")
        with self.assertRaises(C.WriteBlocked):
            rec.call("EmailMessage.send_email", {"id": "m1"})
        self.assertEqual((self.tmp / "rec" / "calls.jsonl").read_text(), "")    # created empty, nothing written

    def test_a_draft_create_is_refused_before_it_is_sent_or_recorded(self):
        """Review of #63, P1: the draft writer calls _rpc directly, past Client.call's read check."""
        rec = Counting(self.tmp / "rec")
        writer = drafts.DraftWriter(rec, MAILBOX)
        with self.assertRaises(C.WriteBlocked):
            writer.create({"to": "ap@customer.example", "subject": "s", "body_text": "b"})
        self.assertEqual(rec.sent, [])                                          # nothing reached the transport
        self.assertEqual((self.tmp / "rec" / "calls.jsonl").read_text(), "")
        with self.assertRaises(C.WriteBlocked):                                 # nor does a replay serve one
            drafts.DraftWriter(C.ReplayClient(self.tmp / "rec"), MAILBOX).create(
                {"to": "ap@customer.example", "subject": "s", "body_text": "b"})

    def test_task9_creates_nothing_when_recorded_even_with_run_id(self):
        """Review of #63, P1: --record with AGENTSWITCH_RUN_ID (or --allow-writes) must not leave a draft."""
        made = []

        def counting(folder):
            made.append(Counting(folder))
            return made[-1]
        env = {"AGENTSWITCH_RUN_ID": "run-1"}
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(suite, "RecordingClient", counting), \
                mock.patch.object(suite, "all_tasks", lambda: [("reminder_is_draft", "T9", tasks_payments.reminder_is_draft)]), \
                redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            suite.main(["--instance", "keystone", "--date", "2026-11-30", "--record", str(self.tmp / "rec")])
        self.assertIn("outcome=blocked; creates a draft, so it doesn't run with --record or --replay", out.getvalue())
        self.assertEqual([m for m in made[0].sent if m != "tools/list"], [])
        self.assertNotIn("EmailMessage.create", (self.tmp / "rec" / "calls.jsonl").read_text())

    def test_a_second_recorder_on_the_same_folder_is_refused_before_any_call(self):
        """Review of #63, P2: the first recorder reserves calls.jsonl when it starts."""
        first = Recording(self.tmp / "rec", lists=[[{"id": "so1"}]])
        with self.assertRaisesRegex(C.McpError, "already holds a recording"):
            Recording(self.tmp / "rec", lists=[[{"id": "other"}]])
        first.call("SalesOrder.list")
        self.assertEqual(C.ReplayClient(self.tmp / "rec").call("SalesOrder.list")["data"], [{"id": "so1"}])

    def test_recording_is_owner_only_and_holds_no_token(self):
        Recording(self.tmp / "rec", lists=[[{"id": "so1"}]]).call("SalesOrder.list")
        self.assertEqual(stat.S_IMODE(os.stat(self.tmp / "rec").st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.tmp / "rec" / "calls.jsonl").st_mode), 0o600)
        self.assertNotIn(TOKEN, (self.tmp / "rec" / "calls.jsonl").read_text())

    def test_a_damaged_recording_is_refused(self):
        (self.tmp / "rec").mkdir()
        (self.tmp / "rec" / "calls.jsonl").write_text('{"key": "k", "same": true}\n')
        with self.assertRaises(C.McpError) as e:
            C.ReplayClient(self.tmp / "rec")
        self.assertIn("line 1 can't be read", str(e.exception))


def ok_task(ctx):
    ctx.client.call("SalesOrder.list")
    return {"passed": True, "score": 1.0, "evidence": "ok"}


def boom(ctx):
    raise RuntimeError("x")


class RunAndSuite(Base):
    def ctx(self, client=None):
        return tasks_payments.Ctx(client or Scripted(), "keystone", date(2026, 11, 30))

    def test_timings_and_debug_lines(self):
        debug.ENABLED, err, timings = True, io.StringIO(), {}
        with redirect_stderr(err):
            res = tasks_payments.run(self.ctx(), [("t", "T", ok_task), ("b", "B", boom)], timings)
        self.assertEqual(set(timings), {"keystone:t", "keystone:b"})
        self.assertEqual(timings["keystone:t"]["calls"], 1)
        self.assertIn("debug: t: pass 1.00 in ", err.getvalue())
        self.assertIn("1 platform calls, 0 retries", err.getvalue())
        self.assertIn("debug: traceback in b:", err.getvalue())
        self.assertIn('raise RuntimeError("x")', err.getvalue())
        self.assertEqual(res[1]["evidence"], "outcome=infrastructure; RuntimeError: x")   # unchanged
        self.assertEqual(debug.current, "")

    def test_quiet_without_debug(self):
        err = io.StringIO()
        with redirect_stderr(err):
            tasks_payments.run(self.ctx(), [("b", "B", boom)])
        self.assertEqual(err.getvalue(), "")

    def test_meta_path(self):
        self.assertEqual(suite.meta_path("runs/results.json"), Path("runs/results.meta.json"))
        self.assertEqual(suite.meta_path("out"), Path("out.meta.json"))

    def test_main_writes_meta_without_token_or_address_and_results_keep_their_shape(self):
        env = {"AGENTSWITCH_BASE_URL": "https://example.invalid", "AGENTSWITCH_TOKEN": TOKEN}
        err = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(suite, "Client", lambda: Scripted(fails=1)), \
                mock.patch.object(suite, "all_tasks", lambda: [("overdue_invoices", "T5", ok_task)]), \
                redirect_stdout(io.StringIO()), redirect_stderr(err), self.assertRaises(SystemExit) as ran:
            suite.main(["--instance", "keystone", "--date", "2026-11-30", "--out", str(self.tmp / "results.json"), "--debug"])
        self.assertEqual(ran.exception.code, 0)
        results = json.loads((self.tmp / "results.json").read_text())
        raw = (self.tmp / "results.meta.json").read_text()
        meta = json.loads(raw)
        self.assertEqual(set(results), {"tasks", "summary"})
        self.assertEqual((meta["source"], meta["instance"], meta["date"]), ("live", "keystone", "2026-11-30"))
        self.assertEqual(meta["tasks"]["keystone:overdue_invoices"]["calls"], 2)       # one retry
        self.assertEqual(meta["platform"]["retries"], 1)
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn("example.invalid", raw)
        self.assertIn("debug: platform: ", err.getvalue())

    def test_usage_errors(self):
        cases = [(["--instance", "keystone", "--record", str(Path(suite.__file__).parent / "never-here")],
                  "a folder git would track"),
                 (["--instance", "keystone", "--record", "/tmp/a", "--replay", "/tmp/b"], "not allowed with"),
                 (["--replay", str(self.tmp)], "no recording"),
                 (["--instance", "keystone", "--agent", "--record", str(self.tmp / "r")], "--agent can't be combined")]
        for args, words in cases:
            err = io.StringIO()
            with self.assertRaises(SystemExit), redirect_stderr(err), mock.patch.dict("os.environ", {}, clear=True):
                suite.main(args)
            self.assertIn(words, err.getvalue(), args)
        self.assertFalse((Path(suite.__file__).parent / "never-here").exists())

    def test_replay_takes_instance_date_and_days_from_the_recording(self):
        Recording(self.tmp / "rec").tool_names()
        (self.tmp / "rec" / "recording.json").write_text(json.dumps({"instance": "suryodaya", "date": "2026-11-24", "days": 9}))
        seen = {}

        def fake_run_suite(ctx, tasks=None, timings=None, quiet=()):
            seen.update(instance=ctx.instance, date=ctx.report_date, days=ctx.days,
                        replay=isinstance(ctx.client, C.ReplayClient))
            return {"tasks": [], "summary": "suryodaya: 0/0 tasks passed"}
        with mock.patch.object(suite, "run_suite", fake_run_suite), redirect_stdout(io.StringIO()), \
                mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit) as ran:
            suite.main(["--replay", str(self.tmp / "rec")])
        self.assertEqual(ran.exception.code, 0)
        self.assertEqual(seen, {"instance": "suryodaya", "date": date(2026, 11, 24), "days": 9, "replay": True})
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err), mock.patch.dict("os.environ", {}, clear=True):
            suite.main(["--replay", str(self.tmp / "rec"), "--instance", "keystone"])
        self.assertIn("the recording is of suryodaya", err.getvalue())


class ReportCommand(Base):
    def run_cli(self, *args, build=None, client=None):
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(cli, "Client", lambda: client or Scripted())]
        if build is not None:
            patches.append(mock.patch.object(cli.report, "build", build))
        code = 0
        with redirect_stdout(out), redirect_stderr(err), mock.patch.dict("os.environ", {}, clear=True):
            for p in patches:
                p.start()
            try:
                cli.main(["--date", "2026-11-30", *args])
            except SystemExit as e:
                code = e.code
            finally:
                for p in patches:
                    p.stop()
        return code, out.getvalue(), err.getvalue()

    def test_unexpected_error_gives_a_reason_line_and_a_failed_claim(self):
        code, out, err = self.run_cli(build=mock.Mock(side_effect=KeyError("rows")))
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines()[-1], 'AGENT_RESULT {"claim":"failed"}')
        self.assertIn("Traceback (most recent call last)", err)
        self.assertEqual(err.strip().splitlines()[-1], "o2c: unexpected error: KeyError: 'rows'")

    def test_with_stderr_merged_into_stdout_the_reason_is_the_last_line(self):
        """Seat 34's harness merges stderr into stdout and reports the last line with a letter as the
        reason; parse_reported scans back for AGENT_RESULT. Review of #63."""
        both = io.StringIO()
        with redirect_stdout(both), redirect_stderr(both):
            cli.unexpected("o2c", KeyError("rows"))
        lines = [ln for ln in both.getvalue().splitlines() if any(ch.isalpha() for ch in ln)]
        self.assertEqual(lines[-1], "o2c: unexpected error: KeyError: 'rows'")
        self.assertEqual(next(ln for ln in reversed(lines) if ln.startswith("AGENT_RESULT ")),
                         'AGENT_RESULT {"claim":"failed"}')

    def test_an_unexpected_error_in_explain_gives_a_reason_line_and_no_claim(self):
        """Bug hunt 26 Nov: --explain caught only platform errors, so anything else was a raw traceback."""
        env = {"AGENTSWITCH_BASE_URL": "http://x", "AGENTSWITCH_TOKEN": TOKEN}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "Client", lambda: Scripted()), \
                mock.patch.object(cli.explain, "run", mock.Mock(side_effect=TypeError("bad\x1b[31m row"))), \
                mock.patch.dict("os.environ", env, clear=True), redirect_stdout(out), redirect_stderr(err), \
                self.assertRaises(SystemExit) as ran:
            cli.main(["--explain", "SO-1"])
        self.assertEqual(ran.exception.code, 1)
        self.assertEqual(err.getvalue().strip().splitlines()[-1], "o2c --explain: unexpected error: TypeError: bad[31m row")
        self.assertNotIn("\x1b", err.getvalue())
        self.assertNotIn("AGENT_RESULT", out.getvalue())

    def test_server_text_in_an_error_is_cleaned(self):
        _, _, err = self.run_cli(build=mock.Mock(side_effect=ValueError("bad\x1b[31m reply\nPASS")))
        self.assertNotIn("\x1b", err)
        self.assertEqual(err.strip().splitlines()[-1], "o2c: unexpected error: ValueError: bad[31m reply PASS")

    def test_debug_switch_and_platform_traceback(self):
        def down(*a, **kw):
            raise C.McpError("connection refused")
        for flags, shown in (([], False), (["--debug"], True)):
            code, _, err = self.run_cli(*flags, build=down)
            self.assertEqual(code, 3, flags)
            self.assertEqual(debug.ENABLED, shown)
            self.assertEqual("Traceback (most recent call last)" in err, shown, flags)
            self.assertIn("connection refused", err)



# ---------------------------------------------------------------- want/got values (tasks 1, 5, 6, 7, 12)

from tests import test_tasks_orders as TO, test_tasks_payments as TP      # noqa: E402
from o2c import tasks_orders                                               # noqa: E402


class WantGot(Base):
    def err_of(self, fn, *a):
        debug.ENABLED, err = True, io.StringIO()
        with redirect_stderr(err):
            res = fn(*a)
        return res, err.getvalue()

    def test_quiet_without_debug(self):
        c = TP.ctx({"Invoice": TP.INVOICES}, tools={"Invoice.list"})
        c.report()["sections"]["invoices_overdue"]["rows"][0]["days_overdue"] += 1
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(tasks_payments.overdue_ageing(c)["passed"])
        self.assertEqual(err.getvalue(), "")

    def test_task1_wrong_age(self):
        c = TO.ctx()
        c.report()["sections"]["orders_not_invoiced"]["rows"][0]["age_days"] += 1
        res, err = self.err_of(tasks_orders.uninvoiced_count, c)
        self.assertFalse(res["passed"])
        self.assertRegex(err, r"debug: A age_days: want (\d+) \| got (\d+)")
        self.assertNotIn("want", res["evidence"])

    def test_task5_a_leaked_bill(self):
        bill = dict(TP.inv("B1", "2026-10-01", 50.0), direction="payable")
        c = TP.ctx({"Invoice": TP.INVOICES + [bill]}, tools={"Invoice.list"})
        c.report()["sections"]["invoices_overdue"]["rows"].append(
            {"id": "B1", "number": "B1", "due_date": "2026-10-01", "outstanding": 50.0, "days_overdue": 45})
        res, err = self.err_of(tasks_payments.overdue_invoices, c)
        self.assertFalse(res["passed"])
        self.assertIn('debug: B1 unexpected overdue invoice: want {"due_date": "2026-10-01", "outstanding": 50.0', err)
        self.assertIn('| got {"days_overdue": 45, "due_date": "2026-10-01", "outstanding": 50.0}', err)

    def test_task5_got_shows_the_balance_the_report_holds(self):
        """The report's rows keep the balance under "outstanding" (report.invoices_overdue); review of #63."""
        c = TP.ctx({"Invoice": TP.INVOICES}, tools={"Invoice.list"})
        row = c.report()["sections"]["invoices_overdue"]["rows"][0]
        self.assertIn("outstanding", row)
        self.assertNotIn("balance", row)
        c.report()["sections"]["invoices_overdue"]["rows"] = [dict(row, id="X9")]
        res, err = self.err_of(tasks_payments.overdue_invoices, c)
        self.assertFalse(res["passed"])
        self.assertRegex(err, r'debug: X9 unexpected overdue invoice: want \{[^|]*\} \| got \{[^}]*"outstanding": \d')

    def test_task6_wrong_days(self):
        c = TP.ctx({"Invoice": TP.INVOICES}, tools={"Invoice.list"})
        row = c.report()["sections"]["invoices_overdue"]["rows"][0]
        row["days_overdue"] += 1
        res, err = self.err_of(tasks_payments.overdue_ageing, c)
        self.assertFalse(res["passed"])
        self.assertRegex(err, r'debug: \S+ days_overdue: want \{"days_overdue": (\d+), "due_date": "[0-9-]+"\} \| got (\d+)')

    def test_task7_summary_table(self):
        c = TP.ctx()
        c.report()["sections"]["payments_not_applied"]["summary"]["by_age"][0]["unallocated"] += 1.0
        res, err = self.err_of(tasks_payments.unapplied_payments, c)
        self.assertFalse(res["passed"])
        self.assertIn("debug: by_age summary table: want ", err)


    def test_task12_shows_the_fields_our_login_changed(self):
        from tests import test_task12_read_only as T12
        from o2c import report
        fake, real = T12.platform(), report.build

        def build(client, **kw):
            fake.data["Payment"][0].update(unallocated=0.0, updated_by=T12.ME)
            return real(client, **kw)
        with mock.patch.object(report, "build", build):
            res, err = self.err_of(tasks_payments.report_read_only, tasks_payments.Ctx(fake, "x", T12.D))
        self.assertFalse(res["passed"])
        self.assertIn('debug: Payment:A edited: want {"unallocated": 50.0, "updated_by": "U-SEED"} '
                      '| got {"unallocated": 0.0, "updated_by": "U-31"}', err)



# ---------------------------------------------------------------- gap 2: the agent's output with --debug

import sys                                                                  # noqa: E402
from o2c import agent as A                                                  # noqa: E402


class AgentOutput(Base):
    def fake_agent(self, lines, code):
        """A stand-in for `python3 -m o2c`: prints its own arguments, then `lines`, then exits with `code`."""
        script = self.tmp / "fake_python"
        body = "\n".join(f"print({line!r})" for line in lines)
        script.write_text(f"#!{sys.executable}\nimport sys\nprint('argv', sys.argv[1:])\n{body}\nsys.exit({code})\n")
        script.chmod(0o700)
        return A.CommandAgent({"AGENTSWITCH_BASE_URL": "https://x.invalid", "AGENTSWITCH_TOKEN": "t"},
                              python=str(script))

    def call(self, hook):
        err = io.StringIO()
        with redirect_stderr(err):
            text = hook("Send the reminder now")
        return text, err.getvalue()

    def test_quiet_without_debug_and_no_debug_flag_passed(self):
        text, err = self.call(self.fake_agent(["Traceback: boom"], 1))
        self.assertEqual(err, "")
        self.assertIn("argv ['-m', 'o2c', '--prompt', 'Send the reminder now']", text)

    def test_a_failed_agent_shows_its_output_with_debug(self):
        debug.ENABLED, debug.current = True, "refuse_send"
        hook = self.fake_agent([f"line {n}" for n in range(60)] + ["KeyError: 'x\x1b[31m'"], 1)
        text, err = self.call(hook)
        self.assertIn("'--debug', '--prompt'", text)                       # --debug passed on
        self.assertIn("debug: refuse_send agent failed (exit=1, claim=none); its output, last 40 of 62 lines:", err)
        self.assertIn("debug:   | KeyError: 'x[31m'", err)               # control characters removed
        self.assertNotIn("\x1b", err)
        self.assertNotIn("line 5\n", err)                                 # older lines cut
        self.assertEqual(hook.describe(), "agent exit=1 claim=none")       # evidence unchanged

    def test_a_successful_agent_passes_on_only_its_debug_lines(self):
        debug.ENABLED = True
        hook = self.fake_agent(["so-1", "debug: retry 1 of tools/call SalesOrder.list after HTTP 503, waiting 1 s",
                                'AGENT_RESULT {"claim":"refused"}'], 0)
        _, err = self.call(hook)
        self.assertIn("debug: agent (exit=0): its debug lines:", err)
        self.assertIn("debug:   | debug: retry 1 of tools/call SalesOrder.list", err)
        self.assertNotIn("so-1", err)

    def test_a_successful_agent_without_debug_lines_prints_nothing(self):
        debug.ENABLED = True
        _, err = self.call(self.fake_agent(['AGENT_RESULT {"claim":"done"}'], 0))
        self.assertEqual(err, "")


# ---------------------------------------------------------------- gap 3: the drafts command

from o2c import drafts                                                      # noqa: E402
from tests import test_create_sequences as CS                               # noqa: E402


class DraftsCommand(Base):
    def run_drafts(self, *extra, propose=None, create=None):
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(drafts, "Client", return_value=CS.FailingClient()),
                   mock.patch.object(drafts, "propose", propose or mock.Mock(return_value=CS.proposal("20", 3, False))),
                   mock.patch.object(drafts, "find_mailbox", return_value=CS.MAILBOX),
                   mock.patch("builtins.input", return_value="save drafts")]
        if create is not None:
            patches.append(mock.patch.object(drafts.DraftWriter, "create", create))
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            for p in patches:
                p.start()
            try:
                drafts.main(["--instance", "t", "--out", str(self.tmp), "--create", "--accounts-email", CS.ACC, *extra])
            except SystemExit as e:
                code = e.code
            finally:
                for p in patches:
                    p.stop()
        return code, out.getvalue(), err.getvalue()

    def test_a_crash_before_saving_gives_a_reason_line(self):
        code, _, err = self.run_drafts(propose=mock.Mock(side_effect=KeyError("rows")))
        self.assertEqual(code, 1)
        self.assertIn("Traceback (most recent call last)", err)
        self.assertEqual(err.strip().splitlines()[-1], "o2c.drafts: unexpected error: KeyError: 'rows'")

    def test_a_crash_while_saving_says_what_was_saved(self):
        code, _, err = self.run_drafts(create=mock.Mock(side_effect=TypeError("bad reply")))
        self.assertEqual(code, 1)
        last = err.strip().splitlines()[-1]
        self.assertTrue(last.startswith("o2c.drafts: unexpected error: TypeError: bad reply. Saved 0 of 1 drafts "
                                        "before this. The one in progress may still have been created"), last)

    def test_debug_switch_and_platform_traceback(self):
        def down(*a, **kw):
            raise C.McpError("connection refused")
        for flags, shown in (([], False), (["--debug"], True)):
            code, _, err = self.run_drafts(*flags, propose=down)
            self.assertEqual(code, 3, flags)
            self.assertEqual(debug.ENABLED, shown)
            self.assertEqual("Traceback (most recent call last)" in err, shown, flags)



# ---------------------------------------------------------------- --only (26 Nov plan, item 1)

class Only(Base):
    def run_main(self, *args):
        seen = {}

        def fake_run_suite(ctx, tasks=None, timings=None, quiet=()):
            seen["tasks"] = None if tasks is None else [t[0] for t in tasks]
            return {"tasks": [], "summary": "keystone: 0/0 tasks passed"}
        err = io.StringIO()
        with mock.patch.object(suite, "run_suite", fake_run_suite), mock.patch.object(suite, "Client", lambda: Scripted()), \
                mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                suite.main(["--instance", "keystone", "--date", "2026-11-30", *args])
            except SystemExit as e:
                seen["exit"] = e.code
        seen["err"] = err.getvalue()
        return seen

    def test_only_runs_the_named_tasks_in_suite_order(self):
        seen = self.run_main("--only", "report_read_only, overdue_ageing")
        self.assertEqual(seen["tasks"], ["overdue_ageing", "report_read_only"])

    def test_without_only_every_task_runs(self):
        self.assertIsNone(self.run_main()["tasks"])

    def test_an_unknown_id_is_a_usage_error(self):
        seen = self.run_main("--only", "overdue_ageing,nope")
        self.assertEqual(seen["exit"], 2)
        self.assertIn("unknown task id(s): nope", seen["err"])
        self.assertIn("overdue_ageing", seen["err"])                      # lists the known ids
        self.assertEqual(self.run_main("--only", ",")["exit"], 2)

    def test_only_with_replay(self):
        Recording(self.tmp / "rec").tool_names()
        (self.tmp / "rec" / "recording.json").write_text(json.dumps(
            {"instance": "keystone", "date": "2026-11-24", "tasks": ["overdue_invoices"]}))
        seen = self.run_main("--replay", str(self.tmp / "rec"), "--only", "overdue_invoices")
        self.assertEqual(seen["tasks"], ["overdue_invoices"])

    def test_only_with_replay_needs_the_task_in_the_recording(self):
        Recording(self.tmp / "rec").tool_names()
        for meta, words in (({"instance": "keystone"}, "doesn't list the tasks it ran"),
                            ({"instance": "keystone", "tasks": ["overdue_invoices"]}, "overdue_ageing not in the recording")):
            (self.tmp / "rec" / "recording.json").write_text(json.dumps(meta))
            seen = self.run_main("--replay", str(self.tmp / "rec"), "--only", "overdue_ageing")
            self.assertEqual(seen["exit"], 2, meta)
            self.assertIn(words, seen["err"])

    def test_allow_writes_with_record_or_replay_is_a_usage_error(self):
        for source in ("--record", "--replay"):
            seen = self.run_main(source, str(self.tmp / "r"), "--allow-writes")
            self.assertEqual(seen["exit"], 2, source)
            self.assertIn("--allow-writes can't be combined", seen["err"])


def read_list(ctx):
    return ctx.client.call("Invoice.list")["data"][0]["due_date"]


def first_task(ctx):
    ctx.seen = read_list(ctx)
    return {"passed": True, "score": 1.0, "evidence": "first"}


def ageing_task(ctx):
    """Passes only on the second reply: the due date changed between the two tasks' reads."""
    due = read_list(ctx)
    ok = due == "2026-11-02" and getattr(ctx, "seen", None) == "2026-11-01"
    return {"passed": ok, "score": 1.0 if ok else 0.0, "evidence": f"due={due}"}


class SelectiveReplay(Base):
    """Review of #63, P2: --replay --only gives the verdict the task had when recorded."""
    TASKS = [("overdue_invoices", "T5", first_task), ("overdue_ageing", "T6", ageing_task)]

    def main(self, *args):
        out = io.StringIO()
        with mock.patch.object(suite, "all_tasks", lambda: list(self.TASKS)), \
                mock.patch.object(suite, "RecordingClient",
                                  lambda folder: Recording(folder, lists=[[{"id": "i1", "due_date": "2026-11-01"}],
                                                                          [{"id": "i1", "due_date": "2026-11-02"}]])), \
                mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(out), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as ran:
            suite.main(["--instance", "keystone", "--date", "2026-11-30", *args])
        return ran.exception.code, out.getvalue()

    def test_one_task_replayed_alone_sees_what_it_saw_when_recorded(self):
        rec = str(self.tmp / "rec")
        code, out = self.main("--record", rec)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads((self.tmp / "rec" / "recording.json").read_text())["tasks"],
                         ["overdue_invoices", "overdue_ageing"])
        for args in ((), ("--only", "overdue_ageing")):
            code, out = self.main("--replay", rec, *args)
            self.assertEqual(code, 0, (args, out))
            self.assertIn("PASS 1.0 keystone:overdue_ageing: due=2026-11-02", out)
        self.assertNotIn("overdue_invoices", out)                       # the quiet task isn't reported
        self.assertIn("keystone: 1/1 tasks passed", out)

    def test_a_plain_replay_runs_the_tasks_the_recording_lists(self):
        """Review of #63 (2nd): a recording made with --only replays as that run, not as all 12 tasks."""
        rec = str(self.tmp / "rec")
        self.assertEqual(self.main("--record", rec, "--only", "overdue_invoices")[0], 0)
        code, out = self.main("--replay", rec)
        self.assertEqual(code, 0, out)
        self.assertIn("keystone: 1/1 tasks passed", out)
        self.assertNotIn("overdue_ageing", out)

    def test_a_recording_without_a_task_list_still_replays_every_task(self):
        rec = self.tmp / "rec"
        self.assertEqual(self.main("--record", str(rec))[0], 0)
        meta = json.loads((rec / "recording.json").read_text())
        del meta["tasks"]
        (rec / "recording.json").write_text(json.dumps(meta))
        code, out = self.main("--replay", str(rec))
        self.assertIn("keystone: 2/2 tasks passed", out)

    def test_replay_uses_the_recorded_mailbox_and_refuses_another(self):
        """Review of #63 (2nd): the mailbox decides whether Mailbox.get or Mailbox.list is read."""
        seen = []

        def mailbox_task(ctx):
            seen.append(ctx.mailbox_id)
            ctx.client.call("Mailbox.get" if ctx.mailbox_id else "Mailbox.list", {"id": ctx.mailbox_id} if ctx.mailbox_id else {})
            return {"passed": True, "score": 1.0, "evidence": "mailbox read"}
        self.TASKS = [("report_read_only", "T12", mailbox_task)]
        rec = str(self.tmp / "rec")
        self.assertEqual(self.main("--record", rec, "--mailbox-id", "mb")[0], 0)
        self.assertEqual(json.loads((self.tmp / "rec" / "recording.json").read_text())["mailbox_id"], "mb")
        code, out = self.main("--replay", rec)
        self.assertEqual((code, seen[-1]), (0, "mb"), out)
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ran, \
                mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(io.StringIO()), \
                mock.patch.object(suite, "all_tasks", lambda: list(self.TASKS)):
            suite.main(["--replay", rec, "--mailbox-id", "other"])
        self.assertEqual(ran.exception.code, 2)
        self.assertIn("recording was made with --mailbox-id mb", err.getvalue())

    def test_a_replay_gap_is_never_an_unknown_login(self):
        """Review of #63 (2nd): _our_login() used to swallow a missing recorded reply, so task 12 blamed
        this run for another login's change. A gap now stops the task as infrastructure."""
        Recording(self.tmp / "rec").tool_names()
        ctx = tasks_payments.Ctx(C.ReplayClient(self.tmp / "rec"), "keystone", date(2026, 11, 30), mailbox_id=None)
        with self.assertRaises(C.ReplayGap):
            tasks_payments._our_login(ctx)
        res = tasks_payments.run(ctx, [("report_read_only", "T12", tasks_payments.report_read_only)])
        self.assertIn("outcome=infrastructure; ReplayGap: replay: no recorded reply", res[0]["evidence"])
        broken = tasks_payments.Ctx(Scripted(reject=True), "keystone", date(2026, 11, 30))
        self.assertIsNone(tasks_payments._our_login(broken))          # a platform error: still "unknown"

    def test_task12_recorded_with_a_mailbox_replays_with_the_same_verdict(self):
        """Review of #63 (2nd), end to end: the real task 12, recorded with --mailbox-id while another
        login edits a payment, replayed without --mailbox-id, gives the same pass and exit code."""
        me, other = "U-31", "U-30"
        data = {"Payment": [{"id": "A", "number": "A", "type": "receive", "docstatus": 1, "amount": 50.0,
                             "unallocated": 50.0, "status": "submitted", "date": "2026-11-01", "party_id": "P1",
                             "updated_by": "U-SEED"}],
                "EmailMessage": [{"id": "m1", "folder": "inbox"}]}
        mailboxes = [{"id": "M", "email": "ar@seller.example", "company_id": "C", "user_id": me}]
        tools = ["SalesOrder.list", "Payment.list", "EmailMessage.list", "Mailbox.list", "Mailbox.get"]

        class Platform(C.RecordingClient):
            """A made-up platform behind a real recording client: JSON-RPC in, JSON-RPC out."""
            def __init__(self, folder):
                super().__init__(folder, "http://x", TOKEN)

            def _post(self, body):
                msg = json.loads(body)
                if msg["method"] == "tools/list":
                    result = {"tools": [{"name": n} for n in tools]}
                else:
                    name, args = msg["params"]["name"], dict(msg["params"]["arguments"])
                    if name == "Mailbox.get":
                        result = {"structuredContent": next(m for m in mailboxes if m["id"] == args["id"])}
                    else:
                        rows = mailboxes if name == "Mailbox.list" else data.get(name.split(".")[0], [])
                        offset, limit = args.pop("offset", 0), args.pop("limit", 1000)
                        for k in ("sort_by", "sort_order"):
                            args.pop(k, None)
                        rows = [r for r in rows if all(r.get(k) == v for k, v in args.items())]
                        result = {"structuredContent": {"data": [dict(r) for r in rows[offset:offset + limit]],
                                                        "total": len(rows)}}
                return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})

        real_build = tasks_payments.report.build

        def build_while_another_login_edits(client, **kw):
            data["Payment"][0].update(notes="seat 30 edit", updated_by=other)
            return real_build(client, **kw)

        def main(*args):
            out = io.StringIO()
            with mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(out), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as ran:
                suite.main(["--instance", "keystone", "--date", "2026-11-30", *args])
            return ran.exception.code, out.getvalue()

        rec = str(self.tmp / "rec")
        with mock.patch.object(suite, "RecordingClient", Platform), \
                mock.patch.object(tasks_payments.report, "build", build_while_another_login_edits):
            code, out = main("--record", rec, "--only", "report_read_only", "--mailbox-id", "M")
        self.assertEqual(code, 0, out)
        self.assertIn("login=known", out)
        self.assertIn("changed_by_other_logins=1", out)
        verdicts = [l for l in out.splitlines() if l.startswith(("PASS", "FAIL"))]
        for args in ((), ("--only", "report_read_only")):              # never --mailbox-id
            code, again = main("--replay", rec, *args)
            self.assertEqual(code, 0, (args, again))
            self.assertEqual([l for l in again.splitlines() if l.startswith(("PASS", "FAIL"))], verdicts, args)

    def test_a_quiet_task_runs_in_its_recorded_place(self):
        order = []
        tasks = [(n, n, lambda ctx, n=n: (order.append(n), {"passed": True, "score": 1.0, "evidence": n})[1])
                 for n in ("a", "b", "c")]
        out = suite.run_suite(tasks_payments.Ctx(Scripted(), "keystone", date(2026, 11, 30)), tasks, quiet={"b"})
        self.assertEqual(order, ["a", "b", "c"])
        self.assertEqual([t["id"] for t in out["tasks"]], ["keystone:a", "keystone:c"])


if __name__ == "__main__":
    unittest.main()
