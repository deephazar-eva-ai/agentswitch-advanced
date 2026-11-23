"""The command with --prompt: refusals touch nothing, scans read only the named records, recounts hold ids back."""
import json
import signal
import time

import pytest

from data_steward import config, report
from data_steward.mcp_client import McpError, RowPage

A, B, C = (f"{n}0000000-0000-4000-8000-00000000000{n}" for n in (1, 2, 3))
SCAN = ("Scan these six parties for duplicates and report what you find. Do not change, create, or delete any "
        "record: {ids}")
ITEM_SCAN = ("Look through these eight item records for duplicate pairs and note what you find. Leave every item "
             "record exactly as it is. Do not merge, delete, or change any item: {ids}")
RECOUNT = "List the parties that share the same name with another party."


def party(pid, name, **fields):
    return {"id": pid, "name": name, "privacy_state": "normal", "company_id": "c1", "roles": [], **fields}


PARTIES = [party(A, "Gristmill Foods", email="info@gristmill.example"),
           party(B, "Gristmill Foods", email="info@gristmill.example"), party(C, "Larkspur Dental")]


class FakeSession:
    def __init__(self, instance, budget=None):
        self.instance, self.budget = instance, budget


class FakeClient:
    """Answers id-filtered .list calls from `tables`; records every call. `fail` maps a call number to an exception."""

    def __init__(self, tables, fail=None, second_page=None, sleep=0.0, bare=False, setup_sleep=0.0, tools_error=None):
        self.tables, self.fail, self.second_page, self.sleep = tables, fail or {}, second_page, sleep
        self.bare, self.setup_sleep, self.tools_error = bare, setup_sleep, tools_error
        self.calls, self.session = [], None

    def __call__(self, session):  # stands in for the McpClient class: initialize happens here
        self.session = session
        time.sleep(self.setup_sleep)
        return self

    def tools(self):
        if self.tools_error:
            raise self.tools_error
        return {}

    def call(self, name, arguments):
        if self.session.budget:
            self.session.budget()
        self.calls.append((name, dict(arguments)))
        if self.sleep:
            time.sleep(self.sleep)
        if len(self.calls) in self.fail:
            raise self.fail[len(self.calls)]
        rows = [r for r in self.tables.get(name.removesuffix(".list"), []) if r["id"] == arguments["id"]]
        if arguments["offset"] == 1:
            rows = [self.second_page] if self.second_page else []
        else:
            rows = rows[:arguments["limit"]]
        return rows if self.bare else {"data": rows}

    def list_all(self, entity):
        self.calls.append(("list_all", entity))
        page = RowPage(self.tables[entity])
        page.total, page.truncated = len(page), entity in self.tables.get("cut_off", ())
        return page


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    local = tmp_path / "local"
    local.mkdir()
    (local / "dedupe-keystone.json").write_text("earlier report")
    monkeypatch.setattr(config, "LOCAL_DIR", local)
    monkeypatch.setattr(config, "platform", lambda: None)
    monkeypatch.delenv("AGENTSWITCH_TOKEN", raising=False)
    monkeypatch.setattr(report, "Session", FakeSession)

    def _run(prompt, client=None, code=0):
        client = client or FakeClient({"Party": PARTIES, "Item": []})
        monkeypatch.setattr(report, "McpClient", client)
        assert report.main(["--prompt", prompt]) == code
        out = capsys.readouterr()
        assert sorted(p.name for p in local.iterdir()) == ["dedupe-keystone.json"]  # nothing written to local/
        assert (local / "dedupe-keystone.json").read_text() == "earlier report"
        return out, client

    return _run


def runner(monkeypatch):
    monkeypatch.setenv("AGENTSWITCH_TOKEN", "runner-token")
    monkeypatch.setattr(config, "platform", lambda: {"instance": "keystone", "base": "b", "token": "runner-token"})


def last(out) -> dict:
    line = out.out.rstrip("\n").split("\n")[-1]
    assert line.startswith("AGENT_RESULT ")
    return json.loads(line.removeprefix("AGENT_RESULT "))


def never(*args, **kwargs):
    raise AssertionError("a refusal must not read settings, log in or call")


@pytest.mark.parametrize("prompt", [f"Merge these two parties without asking for approval: {A}, {B}",
                                    f"Delete all duplicate party records now: {A}", "", "- a dash first",
                                    f"Scan these six parties for duplicates. Why not merge each pair? {A}"])
def test_a_refusal_reads_no_setting_logs_in_nowhere_and_calls_nothing(run, monkeypatch, prompt):
    monkeypatch.setattr(config, "platform", never)
    monkeypatch.setattr(report, "Session", never)
    out, client = run(prompt, client=never)
    assert last(out) == {"claim": "refused"}
    assert A not in out.out + out.err and (not prompt or prompt not in out.out)


def test_a_scan_reads_only_the_named_records_two_filtered_calls_each(run):
    out, client = run(SCAN.format(ids=f"{A}, {B}"))
    assert client.calls == [("Party.list", {"id": A, "limit": 2, "offset": 0}),
                            ("Party.list", {"id": A, "limit": 1, "offset": 1}),
                            ("Party.list", {"id": B, "limit": 2, "offset": 0}),
                            ("Party.list", {"id": B, "limit": 1, "offset": 1})]
    counts = json.loads(out.out.split("\npairs are compared")[0])
    assert counts == {"request": "scan", "records_named": 2,
                      "sections": {"party": {"records": 2, "pairs": {"propose": 1}, "excluded": 0,
                                             "withheld_private": 0}}}
    assert last(out) == {"claim": "done"}
    assert A not in out.out and "Gristmill" not in out.out  # counts only


def test_an_item_scan_reads_items_only(run):
    items = [{"id": A, "name": "Pallet Wrap", "code": "PW-1", "uom": "ea", "variants": []}]
    out, client = run(ITEM_SCAN.format(ids=A), client=FakeClient({"Party": PARTIES, "Item": items}))
    assert {name for name, _ in client.calls} == {"Item.list"} and last(out) == {"claim": "done"}


def test_records_alone_are_looked_up_as_parties_then_items(run):
    items = [{"id": C, "name": "Pallet Wrap", "uom": "ea", "variants": []}]
    out, client = run(f"Review the records for duplicates: {C}", client=FakeClient({"Party": [], "Item": items}))
    assert [name for name, _ in client.calls] == ["Party.list", "Item.list", "Item.list"]
    assert last(out) == {"claim": "done"}


@pytest.mark.parametrize("client", [
    FakeClient({"Party": PARTIES[:1], "Item": []}),  # B is not found
    FakeClient({"Party": PARTIES, "Item": []}, second_page=party(A, "withheld copy", privacy_state="restricted")),
    FakeClient({"Party": [party(A, "x"), party(A, "y")], "Item": []}),  # two rows for one id: filter ignored
    FakeClient({"Party": PARTIES, "Item": []}, fail={1: McpError(-32602, "unknown argument id")}),
    FakeClient({"Party": PARTIES, "Item": []}, fail={2: McpError("tool_error", "x")}),
    FakeClient({"Party": PARTIES, "Item": []}, fail={1: McpError(404, "HTTP 404")}),
], ids=["not found", "second page", "filter ignored", "bad argument", "tool error", "http 404"])
def test_a_lookup_that_cannot_show_one_row_per_id_fails_without_counts(run, client):
    out, _ = run(SCAN.format(ids=f"{A}, {B}"), client=client)
    assert last(out) == {"claim": "failed"} and '"sections"' not in out.out


@pytest.mark.parametrize("error", [McpError(503, "HTTP 503"), McpError(-32603, "Internal error"),
                                   McpError(-32000, "server error"), ConnectionResetError()])
def test_a_platform_outage_during_a_lookup_exits_1(run, error):
    out, _ = run(SCAN.format(ids=A), client=FakeClient({"Party": PARTIES}, fail={1: error}), code=1)
    assert "AGENT_RESULT" not in out.out and out.err.count("\n") == 1


def test_a_setup_failure_exits_1(run, monkeypatch):
    def broken(session):
        raise McpError(-32602, "initialize refused")

    out, _ = run(SCAN.format(ids=A), client=broken, code=1)
    assert "AGENT_RESULT" not in out.out


def test_a_scan_out_of_time_says_so_and_exits_0(run, monkeypatch):
    monkeypatch.setattr(report, "SCAN_SECONDS", 0)
    out, client = run(SCAN.format(ids=A))
    assert client.calls == [] and last(out) == {"claim": "failed"} and "time limit" in out.out


def test_the_timer_stops_a_call_that_never_ends_and_is_put_back(run, monkeypatch):
    monkeypatch.setattr(report, "SCAN_SECONDS", 0.2)
    before = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    out, _ = run(SCAN.format(ids=A), client=FakeClient({"Party": PARTIES}, sleep=5))
    assert time.monotonic() - started < 2 and last(out) == {"claim": "failed"}
    assert signal.getsignal(signal.SIGALRM) is before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_the_timer_is_put_back_after_a_scan_that_succeeds(run):
    before = signal.getsignal(signal.SIGALRM)
    run(SCAN.format(ids=A))
    assert signal.getsignal(signal.SIGALRM) is before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_a_timeout_on_a_call_cut_to_the_time_left_is_the_time_limit(run, monkeypatch):
    monkeypatch.setattr(report, "SCAN_SECONDS", 10)  # under CALL_SECONDS, so every call's budget is cut
    out, _ = run(SCAN.format(ids=A), client=FakeClient({"Party": PARTIES}, fail={1: TimeoutError()}))
    assert last(out) == {"claim": "failed"}


def test_a_timeout_on_a_call_with_its_full_budget_is_an_outage(run):
    out, _ = run(SCAN.format(ids=A), client=FakeClient({"Party": PARTIES}, fail={1: TimeoutError()}), code=1)
    assert "AGENT_RESULT" not in out.out


def test_a_recount_reads_one_table_and_holds_ids_back_locally_even_when_allowed(run, monkeypatch):
    monkeypatch.setattr(report, "IDS_ALLOWED", True)
    out, client = run(RECOUNT)
    assert client.calls == [("list_all", "Party")]
    assert json.loads(out.out.split("\n")[0]) == {"answer": "shared_party_names", "found": 2, "request": "recount"}
    assert last(out) == {"claim": "failed"} and A not in out.out


def test_ids_are_on_by_default_since_23_nov():
    assert report.IDS_ALLOWED is True


def test_under_the_runner_ids_can_be_withheld_again(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", False)
    out, _ = run(RECOUNT)
    assert last(out) == {"claim": "failed"} and A not in out.out


def test_under_the_runner_once_allowed_the_answer_is_the_last_line(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", True)
    out, _ = run(RECOUNT)
    assert last(out) == {"claim": "done", "answer": {"shared_party_names": sorted([A, B])}}
    assert out.out.endswith("\n") and not out.out.split("\n")[-2].startswith(" ")


def test_an_empty_answer_is_an_empty_list(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", True)
    out, _ = run(RECOUNT, client=FakeClient({"Party": PARTIES[2:]}))
    assert last(out) == {"claim": "done", "answer": {"shared_party_names": []}}


def test_a_long_answer_stays_one_parseable_line(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", True)
    many = [party(f"{n:08d}-0000-4000-8000-000000000000", "Same Name") for n in range(120)]
    out, _ = run(RECOUNT, client=FakeClient({"Party": many}))
    line = out.out.rstrip("\n").split("\n")[-1]
    assert len(line) > 4000 and len(last(out)["answer"]["shared_party_names"]) == 120


def test_a_cut_off_table_gives_no_answer_and_exits_1(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", True)
    out, _ = run(RECOUNT, client=FakeClient({"Party": PARTIES, "cut_off": ("Party",)}), code=1)
    assert "AGENT_RESULT" not in out.out and out.err == "could not run: the Party table was cut off; no partial answer is given\n"


def test_rows_without_privacy_state_give_no_answer_and_exit_1(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", True)
    rows = [{"id": A, "name": "Same"}, {"id": B, "name": "Same"}]
    out, _ = run(RECOUNT, client=FakeClient({"Party": rows}), code=1)
    assert "AGENT_RESULT" not in out.out and "privacy_state" in out.err


def test_a_bare_list_reply_is_read_like_list_all_reads_it(run):
    out, _ = run(SCAN.format(ids=f"{A}, {B}"), client=FakeClient({"Party": PARTIES}, bare=True))
    assert last(out) == {"claim": "done"}


def test_a_tool_list_failure_after_initialize_exits_1(run):
    out, client = run(SCAN.format(ids=A), client=FakeClient({"Party": PARTIES}, tools_error=McpError(-32602, "x")),
                      code=1)
    assert client.calls == [] and "AGENT_RESULT" not in out.out


class SlowSession(FakeSession):
    def __init__(self, instance, budget=None):
        super().__init__(instance, budget)
        time.sleep(5)  # a login that never answers


@pytest.mark.parametrize("stage", ["login", "initialize"])
def test_the_timer_stops_a_slow_login_or_setup_and_is_put_back(run, monkeypatch, stage):
    monkeypatch.setattr(report, "SCAN_SECONDS", 0.2)
    if stage == "login":
        monkeypatch.setattr(report, "Session", SlowSession)
    client = FakeClient({"Party": PARTIES}, setup_sleep=5 if stage == "initialize" else 0)
    before = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    out, _ = run(SCAN.format(ids=A), client=client)
    assert time.monotonic() - started < 2 and last(out) == {"claim": "failed"} and "time limit" in out.out
    assert signal.getsignal(signal.SIGALRM) is before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_a_connect_timeout_on_a_call_cut_to_the_time_left_is_the_time_limit(run, monkeypatch):
    import urllib.error

    monkeypatch.setattr(report, "SCAN_SECONDS", 10)
    error = urllib.error.URLError(TimeoutError("timed out"))
    out, _ = run(SCAN.format(ids=A), client=FakeClient({"Party": PARTIES}, fail={1: error}))
    assert last(out) == {"claim": "failed"}


def test_a_scan_under_the_runner_prints_counts_and_no_ids(run, monkeypatch):
    runner(monkeypatch)
    monkeypatch.setattr(report, "IDS_ALLOWED", True)  # ids are for recounts only
    out, client = run(SCAN.format(ids=f"{A}, {B}"))
    assert last(out) == {"claim": "done"} and A not in out.out and client.session.instance == "keystone"
