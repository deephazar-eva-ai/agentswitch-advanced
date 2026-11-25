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
from o2c import debug, suite, tasks_payments

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


class RecordReplay(Base):
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
        self.assertFalse((self.tmp / "rec" / "calls.jsonl").exists())

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
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            suite.main(["--instance", "keystone", "--date", "2026-11-30", "--out", str(self.tmp / "results.json"), "--debug"])
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

        def fake_run_suite(ctx, tasks=None, timings=None):
            seen.update(instance=ctx.instance, date=ctx.report_date, days=ctx.days,
                        replay=isinstance(ctx.client, C.ReplayClient))
            return {"tasks": [], "summary": "suryodaya: 0/0 tasks passed"}
        with mock.patch.object(suite, "run_suite", fake_run_suite), redirect_stdout(io.StringIO()), \
                mock.patch.dict("os.environ", {}, clear=True):
            suite.main(["--replay", str(self.tmp / "rec")])
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


if __name__ == "__main__":
    unittest.main()
