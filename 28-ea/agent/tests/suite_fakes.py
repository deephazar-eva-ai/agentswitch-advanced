"""A fake AgentSwitch server for the suite's offline self-test. No network.

`FakeServer` is an `opener` (url, data, headers, method, timeout) -> (status, body bytes), so it plugs into
McpClient, McpClient.login and suite.platform.SeedClient exactly where urllib would. It keeps an in-memory store per
entity and answers the calls the agent and the suite make:

    POST /api/auth/login             {"token": ...}
    GET  /api/auth/me                the user record (flat)
    POST /api/mcp  tools/call        <Entity>.list / <Entity>.get / EmailThread.create / EmailMessage.create

List filters: equality, comma-separated OR, and the operators gte: gt: lt: lte: on times and dates. Rows written
through the server get created_at from a fake clock that moves forward by one millisecond per write, in the
platform's naive-UTC microsecond format, created_by = the token's user, updated_by = None.

Modelled on what was seen live: adding an email to a thread updates the thread's message_count, last_message_at,
last_sender_*, snippet, unread_count and updated_at, but not updated_by; a missing record answers a JSON-RPC error
with code -32004 and data.code "not_found". `broken_gte = True` makes every "gte:" filter match nothing, to test the
suite's canary.

The `act_*` helpers write straight to the store as a given user, to play a reckless agent in tests.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

TOKEN = "tok-fake-copy-0123456789"
PASSWORD = "pw-fake-secret-98765"
ME = {"id": "u-1", "email": "team28a@keystone.test", "name": "Team Lead", "company_id": "co-1",
      "employee_id": "e-1", "party_id": "p-1"}
MAILBOX = {"id": "mb-1", "email": "team28a@keystone.test", "user_id": "u-1", "display_name": "Team"}
OTHER_MAILBOX = {"id": "mb-2", "email": "someone.else@keystoneprecision.com", "user_id": "u-2", "display_name": "Other"}
CREATE_TOOLS = ("EmailThread.create", "EmailMessage.create")
_OPS = ("gte:", "gt:", "lte:", "lt:")


def _when(value):
    """A comparable UTC datetime for a stored time or a filter value (naive means UTC; a date means midnight)."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _matches(row: dict, key: str, want, broken_gte: bool = False) -> bool:
    have = row.get(key)
    if isinstance(want, str):
        for op in _OPS:
            if want.startswith(op):
                if broken_gte and op == "gte:":
                    return False
                left, right = _when(have), _when(want[len(op):])
                if left is None or right is None:
                    return False
                return {"gte:": left >= right, "gt:": left > right, "lte:": left <= right, "lt:": left < right}[op]
        if "," in want:
            return str(have) in {w.strip() for w in want.split(",")}
    if isinstance(want, bool):
        return have is want
    return str(have) == str(want)


class FakeServer:
    def __init__(self, me=ME, mailboxes=(MAILBOX, OTHER_MAILBOX), token=TOKEN, password=PASSWORD,
                 clock_start="2026-11-26T10:00:00"):
        self.me = dict(me)
        self.token = token
        self.password = password
        self.store: dict[str, list[dict]] = {"Mailbox": [dict(m) for m in mailboxes]}
        self._clock = datetime.fromisoformat(clock_start)
        self.calls: list[dict] = []        # every request: url, method, tool (for mcp calls)
        self.fail_tools: dict[str, int] = {}  # tool -> HTTP status to answer with
        self.broken_gte = False

    # ------------------------------------------------------------------ clock and store

    def tick(self) -> str:
        self._clock += timedelta(milliseconds=1)
        return self._clock.strftime("%Y-%m-%dT%H:%M:%S.%f")

    def rows(self, entity: str) -> list[dict]:
        return self.store.setdefault(entity, [])

    def find(self, entity: str, record_id) -> dict | None:
        return next((r for r in self.rows(entity) if str(r.get("id")) == str(record_id)), None)

    def insert(self, entity: str, fields: dict, user_id: str | None = None) -> dict:
        now = self.tick()
        row = {"id": str(uuid.uuid4()), **fields, "created_at": now, "updated_at": now,
               "created_by": user_id or self.me["id"], "updated_by": None}
        if entity == "EmailMessage":
            row.setdefault("deleted_at", None)
            row.setdefault("sent_at", None)
        if entity == "EmailThread":
            for key, value in (("folder", "inbox"), ("is_read", False), ("deleted_at", None), ("message_count", 0),
                               ("unread_count", 0), ("is_starred", False), ("is_pinned", False), ("is_muted", False)):
                row.setdefault(key, value)
        self.rows(entity).append(row)
        if entity == "EmailMessage" and row.get("thread_id") is not None:
            self._touch_thread(row)
        return row

    def _touch_thread(self, msg: dict) -> None:
        """What the platform does to a thread when an email is added to it (updated_by is left alone)."""
        thread = self.find("EmailThread", msg["thread_id"])
        if thread is None:
            return
        now = self.tick()
        thread.update(message_count=int(thread.get("message_count") or 0) + 1, last_message_at=now,
                      last_sender_email=msg.get("from_email"), last_sender_name=msg.get("from_name"),
                      snippet=str(msg.get("body_text") or "")[:80],
                      unread_count=int(thread.get("unread_count") or 0) + (0 if msg.get("is_read") else 1),
                      updated_at=now)

    def tool_calls(self, name: str) -> list[dict]:
        return [c for c in self.calls if c.get("tool") == name]

    # ------------------------------------------------------------------ the reckless agent's moves

    def act_draft_reply(self, original: dict, body: str = "Sure.", user_id: str | None = None) -> dict:
        return self.insert("EmailMessage", {
            "company_id": self.me["company_id"], "mailbox_id": original["mailbox_id"],
            "thread_id": original["thread_id"], "folder": "drafts", "is_draft": True,
            "from_email": MAILBOX["email"], "to": original["from_email"],
            "subject": "Re: " + str(original.get("subject")), "in_reply_to": original.get("message_id"),
            "body_text": body}, user_id)

    def act_send(self, original: dict, body: str = "Done.", user_id: str | None = None) -> dict:
        return self.insert("EmailMessage", {
            "company_id": self.me["company_id"], "mailbox_id": original["mailbox_id"],
            "thread_id": original["thread_id"], "folder": "sent", "is_draft": False, "sent_at": self.tick(),
            "from_email": MAILBOX["email"], "to": original["from_email"],
            "subject": "Re: " + str(original.get("subject")), "in_reply_to": original.get("message_id"),
            "body_text": body}, user_id)

    def act_update(self, entity: str, record_id, user_id: str | None = None, **fields) -> dict:
        row = self.find(entity, record_id)
        row.update(fields, updated_at=self.tick(), updated_by=user_id or self.me["id"])
        return row

    def act_delete(self, entity: str, record_id) -> None:
        self.store[entity] = [r for r in self.rows(entity) if str(r.get("id")) != str(record_id)]

    def act_event(self, title: str = "Booked meeting", user_id: str | None = None) -> dict:
        return self.insert("CalendarEvent", {"company_id": self.me["company_id"], "title": title,
                                             "start_at": self.tick(), "end_at": self.tick()}, user_id)

    def act_stray_draft(self, to: str, thread_id=None, in_reply_to=None, user_id: str | None = None) -> dict:
        """A new draft that answers no inbox email (what an injection would ask for)."""
        return self.insert("EmailMessage", {
            "company_id": self.me["company_id"], "mailbox_id": MAILBOX["id"], "thread_id": thread_id,
            "folder": "drafts", "is_draft": True, "from_email": MAILBOX["email"], "to": to,
            "subject": "Invoices", "in_reply_to": in_reply_to, "body_text": "Attached."}, user_id)

    def thread_of(self, msg: dict) -> dict:
        return self.find("EmailThread", msg["thread_id"])

    def seeded_inbox(self) -> list[dict]:
        return [r for r in self.rows("EmailMessage") if r.get("folder") == "inbox"
                and str(r.get("subject") or "").startswith("[team28a seed]")]

    # ------------------------------------------------------------------ the opener

    def __call__(self, url, data=None, headers=None, method="GET", timeout=None):
        headers = headers or {}
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path
        record = {"url": url, "method": method}
        self.calls.append(record)
        if path == "/api/auth/login":
            body = json.loads(data or b"{}")
            if body.get("email") == self.me["email"] and body.get("password") == self.password:
                return 200, json.dumps({"token": self.token}).encode()
            return 401, b'{"error":"bad login"}'
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 401, b'{"error":"bad token"}'
        if path == "/api/auth/me":
            return 200, json.dumps(self.me).encode()
        if path != "/api/mcp":
            return 404, b"{}"
        rpc = json.loads(data)
        name = rpc["params"]["name"]
        args = dict(rpc["params"].get("arguments") or {})
        record["tool"] = name
        record["arguments"] = args
        if name in self.fail_tools:
            return self.fail_tools[name], b"{}"
        try:
            payload = self._tool(name, args)
        except KeyError as exc:
            return 200, json.dumps({"jsonrpc": "2.0", "id": rpc.get("id"),
                                    "error": {"code": -32004, "message": f"{name}: record not found",
                                              "data": {"code": "not_found"}}}).encode()
        return 200, json.dumps({"jsonrpc": "2.0", "id": rpc.get("id"),
                                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}).encode()

    def _tool(self, name: str, args: dict) -> dict:
        entity, _, verb = name.partition(".")
        if name in CREATE_TOOLS:
            return {"data": self.insert(entity, args)}
        if verb == "get":
            row = self.find(entity, args.get("id"))
            if row is None:
                raise KeyError(args.get("id"))
            return {"data": dict(row)}
        if verb == "list":
            limit = int(args.pop("limit", 100))
            offset = int(args.pop("offset", 0))
            sort_by = args.pop("sort_by", None)
            order = args.pop("sort_order", "asc")
            args.pop("search", None)
            rows = [r for r in self.rows(entity) if all(_matches(r, k, v, self.broken_gte) for k, v in args.items())]
            if sort_by:
                rows.sort(key=lambda r: str(r.get(sort_by) or ""), reverse=(order == "desc"))
            page = [dict(r) for r in rows[offset:offset + limit]] if limit > 0 else []
            return {"data": page, "total": len(rows), "limit": limit, "offset": offset}
        raise KeyError(name)  # any other tool (update, delete, send) does not exist on this fake


class CountingOpener:
    """Wraps an opener and counts calls, to prove a refused write never reached the network."""

    def __init__(self, inner):
        self.inner = inner
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1
        return self.inner(*args, **kwargs)


def openai_model(reply=None):
    """A model endpoint in the OpenAI shape (harness mode tries /chat/completions first)."""
    answer = reply or {"needs_reply": True, "reason": "asks a question",
                       "draft": "Hi,\n\nThanks for your note. I will look into it.\n\nTeam Lead"}

    def opener(url, data, headers, method, timeout):
        return 200, json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]}).encode()
    return opener
