import json

import pytest

from data_steward import mcp_client
from data_steward.mcp_client import AuthExpired, McpClient, McpError


class FakeSession:
    base = "https://instance.example"

    def __init__(self):
        self.token = "old"
        self.logins = 0

    def login(self):
        self.logins += 1
        self.token = f"new{self.logins}"

    def with_reauth(self, fn):
        try:
            return fn()
        except AuthExpired:
            self.login()
            return fn()


def fake_http(replies):
    """Serve `replies` in order, recording each JSON-RPC body; an exception in the list is raised instead."""
    calls = []

    def _http(url, body=None, token=None, **kwargs):
        calls.append({"body": body, "token": token, **kwargs})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return _http, calls


def ok(result):
    return 200, {"jsonrpc": "2.0", "id": 1, "result": result}


def tool(name, read_only, destructive=False):
    return {"name": name, "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive}}


TOOLS = [tool("Party.list", True), tool("Party.update", False), tool("AddressBook.delete", False, True),
         {"name": "Item.list"}]


def client(monkeypatch, replies, tools=TOOLS):
    """A client past initialize and, unless tools is None, past tools/list: `calls` then holds only what follows."""
    _http, calls = fake_http([ok({}), *([ok({"tools": tools})] if tools is not None else []), *replies])
    monkeypatch.setattr(mcp_client, "_http", _http)
    c = McpClient(FakeSession())
    if tools is not None:
        c.tools()
        del calls[:]
    return c, calls


def test_jsonrpc_error_inside_http_200_raises(monkeypatch):
    c, _ = client(monkeypatch, [(200, {"jsonrpc": "2.0", "id": 2, "error": {
        "code": -32601, "message": "Unknown tool: Party.list"}})])
    with pytest.raises(McpError) as e:
        c.call("Party.list", {})
    assert e.value.kind == "unknown_tool"


def test_tool_error_flag_raises_with_content(monkeypatch):
    c, _ = client(monkeypatch, [ok({"isError": True, "content": [{"type": "text", "text": '{"code": "permission_denied"}'}]})])
    with pytest.raises(McpError) as e:
        c.call("Party.list")
    assert e.value.kind == "permission_denied"


def test_text_content_is_parsed_as_json(monkeypatch):
    c, _ = client(monkeypatch, [ok({"content": [{"type": "text", "text": '{"data": [{"id": 1}], "total": 1}'}]})])
    assert c.call("Party.list") == {"data": [{"id": 1}], "total": 1}


def test_expired_token_logs_in_once_and_retries(monkeypatch):
    c, calls = client(monkeypatch, [AuthExpired("x"), ok({"tools": [{"name": "Party.list"}]})], tools=None)
    assert c.tool_names() == {"Party.list"}
    assert c.session.logins == 1
    assert calls[-1]["token"] == "new1"


@pytest.mark.parametrize("name", ["Party.update", "AddressBook.delete", "Party.merge", "Item.list"])
def test_guard_refuses_anything_not_marked_read_only_before_sending(monkeypatch, name):
    # An update (not marked destructive), a delete, a tool not on the list, and one with no annotations at all.
    c, calls = client(monkeypatch, [])
    with pytest.raises(McpError) as e:
        c.call(name, {"id": 1})
    assert e.value.kind == "refused" and calls == []


def test_guard_sends_a_read_only_tool_as_a_read(monkeypatch):
    c, calls = client(monkeypatch, [ok({"content": []})])
    c.call("Party.list")
    assert len(calls) == 1 and calls[0]["read_only"] is True


def test_guard_loads_the_tool_list_before_the_first_call(monkeypatch):
    c, calls = client(monkeypatch, [ok({"tools": TOOLS}), ok({"content": []})], tools=None)
    c.call("Party.list")
    assert [call["body"]["method"] for call in calls] == ["initialize", "tools/list", "tools/call"]


def test_list_all_pages_until_total(monkeypatch):
    pages = [ok({"structuredContent": {"data": [{"id": i} for i in range(n, n + 2)], "total": 5}}) for n in (0, 2)]
    pages.append(ok({"structuredContent": {"data": [{"id": 4}], "total": 5}}))
    c, _ = client(monkeypatch, pages)
    rows = c.list_all("Party", page=2)
    assert [r["id"] for r in rows] == [0, 1, 2, 3, 4]
    assert rows.truncated is False


def test_list_all_reports_truncation(monkeypatch):
    c, _ = client(monkeypatch, [ok({"structuredContent": {"data": [{"id": 1}, {"id": 2}], "total": 9}})])
    rows = c.list_all("Party", page=2, max_rows=2)
    assert len(rows) == 2 and rows.total == 9 and rows.truncated is True


def test_sse_body_decodes_last_data_event():
    raw = b'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n\n'
    assert mcp_client._decode(raw, "text/event-stream; charset=utf-8")["result"] == {"ok": True}
    assert mcp_client._decode(json.dumps({"a": 1}).encode(), "application/json") == {"a": 1}


def test_rejected_login_is_a_plain_error_and_not_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(mcp_client.config, "credentials", lambda instance: ("seat@example.test", "wrong"))
    _http, calls = fake_http([AuthExpired("login")])
    monkeypatch.setattr(mcp_client, "_http", _http)
    with pytest.raises(RuntimeError, match="HTTP 401") as failed:
        mcp_client.Session("suryodaya")
    assert len(calls) == 1 and calls[0]["retries"] == 0
    assert "seat@example.test" not in str(failed.value)  # the login name stays out of pasted output


def test_list_all_without_total_reports_rows_left_behind(monkeypatch):
    # A bare-list reply carries no total: hitting the ceiling must not read as a whole table.
    c, _ = client(monkeypatch, [ok({"structuredContent": [{"id": 1}, {"id": 2}]}),
                                ok({"structuredContent": [{"id": 3}, {"id": 4}]})])
    rows = c.list_all("Party", page=2, max_rows=3)
    assert len(rows) == 3 and rows.truncated is True and rows.total is None


def test_list_all_without_total_ends_only_on_an_empty_page(monkeypatch):
    # A short page may be a server cap, not the end: the duplicate of id 1 is on the page after it.
    c, calls = client(monkeypatch, [ok({"structuredContent": [{"id": 1}, {"id": 2}]}),
                                    ok({"structuredContent": [{"id": 3}]}),
                                    ok({"structuredContent": [{"id": 4, "name": "copy of 1"}]}),
                                    ok({"structuredContent": []})])
    rows = c.list_all("Party", page=3)
    assert [r["id"] for r in rows] == [1, 2, 3, 4] and rows.truncated is False and rows.total == 4
    assert [call["body"]["params"]["arguments"]["offset"] for call in calls] == [0, 2, 3, 4]


def test_list_all_with_a_capped_page_keeps_reading_to_the_total(monkeypatch):
    pages = [ok({"structuredContent": {"data": [{"id": i} for i in range(n, min(n + 2, 5))], "total": 5}})
             for n in (0, 2, 4)]
    c, _ = client(monkeypatch, pages)
    rows = c.list_all("Party", page=200)
    assert [r["id"] for r in rows] == [0, 1, 2, 3, 4] and rows.truncated is False


def test_list_all_refuses_a_server_that_ignores_the_offset(monkeypatch):
    same = ok({"structuredContent": [{"id": 1}, {"id": 2}]})
    c, _ = client(monkeypatch, [same, same])
    with pytest.raises(McpError, match="offset ignored"):
        c.list_all("Party", page=3)


def test_list_all_keeps_a_total_a_later_page_omits(monkeypatch):
    c, _ = client(monkeypatch, [ok({"structuredContent": {"data": [{"id": 1}, {"id": 2}], "total": 9}}),
                                ok({"structuredContent": {"data": [{"id": 3}, {"id": 4}]}})])
    rows = c.list_all("Party", page=2, max_rows=4)
    assert rows.total == 9 and rows.truncated is True


def test_list_all_refuses_a_reply_without_rows(monkeypatch):
    # call() falls back to {"text": ...} for a non-JSON reply; that must not read as an empty table.
    c, _ = client(monkeypatch, [ok({"content": [{"type": "text", "text": "rate limited, try later"}]})])
    with pytest.raises(McpError):
        c.list_all("Party")


def test_sse_event_with_several_data_lines_is_joined():
    raw = b'event: message\ndata: {"jsonrpc": "2.0",\ndata:  "id": 1, "result": {}}\n\n'
    assert mcp_client._decode(raw, "text/event-stream")["id"] == 1


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def gateway_error(url):
    return mcp_client.urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)


@pytest.mark.parametrize("read_only, attempts", [(True, mcp_client.CONNECT_RETRIES + 1), (False, 1)])
def test_gateway_error_is_retried_only_for_reads(monkeypatch, read_only, attempts):
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        raise gateway_error(req.full_url)

    monkeypatch.setattr(mcp_client.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mcp_client.time, "sleep", lambda s: None)
    status, _ = mcp_client._http("https://instance.example/api/mcp", {"x": 1}, read_only=read_only)
    assert status == 503 and len(seen) == attempts


def test_real_session_logs_in_once_when_token_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    (tmp_path / "seat35-keystone.token").write_text("stale")
    monkeypatch.setattr(mcp_client.config, "credentials", lambda instance: ("seat@example.test", "pw"))
    logins = []

    def _http(url, body=None, token=None, **kwargs):
        logins.append(url)
        return 200, {"token": "fresh"}

    monkeypatch.setattr(mcp_client, "_http", _http)
    session = mcp_client.Session("keystone")
    tokens = []

    def call():
        tokens.append(session.token)
        if session.token == "stale":
            raise AuthExpired("x")
        return "ok"

    assert session.with_reauth(call) == "ok"
    assert tokens == ["stale", "fresh"] and len(logins) == 1
    assert (tmp_path / "seat35-keystone.token").read_text() == "fresh"


def test_a_refreshed_token_is_private_even_over_a_loose_file_or_a_symlink(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(mcp_client.config, "credentials", lambda instance: ("seat@example.test", "pw"))
    monkeypatch.setattr(mcp_client, "_http", lambda url, body=None, token=None, **kw: (200, {"token": "fresh"}))
    loose = tmp_path / "seat35-keystone.token"
    loose.write_text("")  # empty: the session logs in
    loose.chmod(0o644)
    mcp_client.Session("keystone")
    assert loose.read_text() == "fresh" and loose.stat().st_mode & 0o777 == 0o600
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    (tmp_path / "seat35-suryodaya.token").symlink_to(target)
    monkeypatch.setattr(mcp_client.Session, "__init__", lambda self, instance: None)
    s = mcp_client.Session.__new__(mcp_client.Session)
    s.platform, s.instance, s.base = None, "suryodaya", "https://instance.example"
    s._token_file = tmp_path / "seat35-suryodaya.token"
    s.login()
    assert target.read_text() == "keep" and not s._token_file.is_symlink()
    assert s._token_file.read_text() == "fresh" and s._token_file.stat().st_mode & 0o777 == 0o600


def test_reauth_skips_login_when_another_call_already_refreshed(monkeypatch, tmp_path):
    # Two calls fail on the same stale token; the second must reuse the first one's login, not spend another try.
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    (tmp_path / "seat35-keystone.token").write_text("stale")
    session = mcp_client.Session("keystone")
    logins = []

    def login():
        logins.append(1)
        session.token = "fresh"

    monkeypatch.setattr(session, "login", login)

    def call_that_failed_before_the_refresh():
        if not logins:
            login()  # the other call's login lands while this one is still in flight
            raise AuthExpired("x")
        return "ok"

    assert session.with_reauth(call_that_failed_before_the_refresh) == "ok"
    assert len(logins) == 1


def test_a_budgeted_session_gives_every_call_its_time_left_and_no_retries(monkeypatch, tmp_path):
    # The prompted scan has a deadline: a retry or the default 120 s timeout could hold it past the harness's limit.
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    (tmp_path / "seat35-keystone.token").write_text("cached")
    left = iter([30, 20, 12.5])
    _http, calls = fake_http([ok({}), ok({"tools": TOOLS}), ok({"data": []})])
    monkeypatch.setattr(mcp_client, "_http", _http)
    c = McpClient(mcp_client.Session("keystone", budget=lambda: next(left)))
    c.call("Party.list", {"id": "x"})
    assert [(k["timeout"], k["retries"]) for k in calls] == [(30, 0), (20, 0), (12.5, 0)]


def test_an_unbudgeted_session_keeps_the_default_timeout_and_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    (tmp_path / "seat35-keystone.token").write_text("cached")
    _http, calls = fake_http([ok({}), ok({"tools": TOOLS})])
    monkeypatch.setattr(mcp_client, "_http", _http)
    McpClient(mcp_client.Session("keystone")).tools()
    assert all("timeout" not in k and "retries" not in k for k in calls)


def test_a_relogin_late_in_a_budgeted_scan_gets_only_the_time_left(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(mcp_client.config, "credentials", lambda instance: ("seat@example.test", "pw"))
    (tmp_path / "seat35-keystone.token").write_text("stale")
    _http, calls = fake_http([AuthExpired("mcp"), (200, {"token": "fresh"}), ok({})])
    monkeypatch.setattr(mcp_client, "_http", _http)
    McpClient(mcp_client.Session("keystone", budget=lambda: 5))
    login = calls[1]
    assert login["body"] == {"email": "seat@example.test", "password": "pw"}
    assert login["timeout"] == 5 and login["retries"] == 0


@pytest.mark.parametrize("result", [{}, {"tools": None}, {"tools": [{"no": "name"}]}, [], {"tools": [{"name": None}]},
                                    {"tools": [{"name": " "}]}, {"tools": [{"name": 7}]}])
def test_an_unreadable_tool_list_is_an_error_not_an_empty_catalogue(monkeypatch, result):
    # An empty catalogue would refuse every call, which a caller could mistake for an answer about the records.
    _http, _ = fake_http([ok({}), ok(result)])
    monkeypatch.setattr(mcp_client, "_http", _http)
    with pytest.raises(McpError, match="no tool list"):
        McpClient(FakeSession()).tools()


def test_a_later_page_with_a_changed_copy_of_a_known_id_is_read_not_called_a_repeat(monkeypatch):
    # The recount rules let two rows of one id disagree; a withheld copy on page two must reach the privacy rule.
    c, _ = client(monkeypatch, [ok({"structuredContent": [{"id": 1, "privacy_state": "normal"}]}),
                                ok({"structuredContent": [{"id": 1, "privacy_state": "restricted"}]}),
                                ok({"structuredContent": []})])
    rows = c.list_all("Party", page=1)
    assert [r["privacy_state"] for r in rows] == ["normal", "restricted"] and rows.truncated is False


def test_an_ignored_offset_returning_a_changed_row_never_reads_as_a_whole_table(monkeypatch):
    # Two records, total 2, one row a page; the server ignores the offset and returns A again with a value changed.
    # Counting that copy toward the total would report A twice as the whole table and never read B.
    a, changed = {"id": "A", "updated_at": "1"}, {"id": "A", "updated_at": "2"}
    for third in ([{"id": "A", "updated_at": "3"}], [a]):
        c, _ = client(monkeypatch, [ok({"data": [a], "total": 2}), ok({"data": [changed], "total": 2}),
                                    ok({"data": third, "total": 2})])
        with pytest.raises(McpError, match="past its reported total|repeated a page"):
            c.list_all("Party", page=1)


@pytest.mark.parametrize("after", [[{"id": "D"}], []])
def test_a_page_partly_repeating_known_ids_needs_an_empty_page_to_end(monkeypatch, after):
    # Total 4: [A, B] then [B changed, C]. Reaching 4 must not end the read, or D is never asked for.
    pages = [ok({"data": [{"id": "A"}, {"id": "B", "v": 1}], "total": 4}),
             ok({"data": [{"id": "B", "v": 2}, {"id": "C"}], "total": 4}), ok({"data": after, "total": 4})]
    c, _ = client(monkeypatch, pages)
    if after:
        with pytest.raises(McpError, match="past its reported total"):
            c.list_all("Party", page=2)
    else:  # a server whose total counts both copies of B: all four rows, complete
        rows = c.list_all("Party", page=2)
        assert len(rows) == 4 and rows.truncated is False


def test_a_changed_copy_within_the_total_still_ends_on_an_empty_page(monkeypatch):
    c, calls = client(monkeypatch, [ok({"data": [{"id": "A", "privacy_state": "normal"}], "total": 2}),
                                    ok({"data": [{"id": "A", "privacy_state": "restricted"}], "total": 2}),
                                    ok({"data": [], "total": 2})])
    rows = c.list_all("Party", page=1)
    assert len(rows) == 2 and rows.truncated is False and len(calls) == 3


def test_an_empty_tool_list_is_a_real_answer(monkeypatch):
    c, _ = client(monkeypatch, [], tools=[])
    assert c.tools() == {}


def test_a_list_reply_that_is_not_rows_is_an_error_not_an_empty_table(monkeypatch):
    c, _ = client(monkeypatch, [ok({"content": [{"type": "text", "text": '""'}]})])
    with pytest.raises(McpError, match="no rows list"):
        c.list_all("Party")
