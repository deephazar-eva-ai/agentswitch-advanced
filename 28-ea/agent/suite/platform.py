"""Platform access for the suite.

Reads go through the agent's own client (`ea_agent.mcp.McpClient`), which can call only read tools, plus
`ThreadReader` for the two thread reads the agent never needs (`EmailThread.get`, `EmailThread.list`). The agent's
allow-list is not changed.

Test data is written by `SeedClient`, which can do two things and nothing else: create an email thread and create an
email. It never updates, sends or deletes. Every write is checked here before anything goes over the network:

- only our own mailbox and company;
- the subject starts with SEED_TAG, so a person can always tell test mail apart;
- plain ASCII text only, so no look-alike letters can hide a real domain;
- every address, link and domain name, in the fields and in the text, is our own mailbox or a reserved test domain
  (RFC 2606);
- inbox mail is not a draft and comes from a test address to our mailbox; a seeded draft is an unsent draft from
  our own mailbox; nothing is ever put in sent, outbox or any other folder;
- mail goes only in a thread this run created;
- at most MAX_SEED_WRITES writes per run.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from ea_agent import config
from ea_agent.mcp import McpClient, NETWORK_ERRORS, list_capped, safe_id, urllib_opener

SEED_TAG = "[team28a seed]"
MAX_SEED_WRITES = 80
MAX_THREAD_READS = 200
LIVE_URLS = frozenset(u.rstrip("/") for u in config.INSTANCES.values())
LIVE_HOSTS = frozenset((urlsplit(u).hostname or "").lower() for u in LIVE_URLS)
KEYSTONE_URL = config.INSTANCES["keystone"].rstrip("/")

# Reserved for testing by RFC 2606 / RFC 6761: mail to these can never reach a real person.
RESERVED_SUFFIXES = (".example", ".test", ".invalid")
RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})

THREAD_ARGS = frozenset({"company_id", "mailbox_id", "subject", "participant_emails", "participant_names"})
MESSAGE_ARGS = frozenset({"company_id", "mailbox_id", "thread_id", "folder", "is_draft", "is_read", "is_answered",
                          "message_id", "in_reply_to", "from_email", "from_name", "to", "subject", "body_text",
                          "received_at", "list_unsubscribe", "split_category"})
SEED_TOOLS = {"EmailThread.create": THREAD_ARGS, "EmailMessage.create": MESSAGE_ARGS}
THREAD_READS = frozenset({"EmailThread.get", "EmailThread.list"})

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")
# Any scheme ("https://", "ftp://") or a scheme-less "//host" or "www.host".
_URL = re.compile(r"(?:\b[A-Za-z][A-Za-z0-9+.-]*:)?//([A-Za-z0-9.-]+)|\bwww\.([A-Za-z0-9.-]+)", re.I)
# A bare domain name such as "evil-bank.com" or "pay.evil-bank.com/login": labels, a dot, a 2-24 letter ending.
_HOST = re.compile(r"(?<![\w.-])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24})(?![\w-])")


class InfraError(Exception):
    """The suite could not set up or check a task. Reported as an infrastructure failure, never as a pass."""


class SeedRefused(InfraError):
    """A task tried to write test data that breaks the rules above. Nothing was sent."""


def is_live(base_url: str | None) -> bool:
    """True for any URL on a live system's host, whatever its case, port, path or trailing slash."""
    try:
        host = (urlsplit((base_url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return True  # a URL we cannot read is treated as live: the safe side
    return host in LIVE_HOSTS


def reserved(domain: str) -> bool:
    domain = domain.strip().strip(".").lower()
    return domain.endswith(RESERVED_SUFFIXES) or any(domain == d or domain.endswith("." + d) for d in RESERVED_DOMAINS)


def row_of(payload) -> dict | None:
    """The record in a create/get reply: either {"data": {...}} or the record itself."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else None


def _rpc(base_url: str, token: str, opener, timeout: float, rpc_id: int, tool: str, args: dict, parser) -> dict:
    """One MCP tool call, never retried. `parser` is the agent client's reply parser (same envelope rules)."""
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "Authorization": f"Bearer {token}"}
    try:
        status, raw = opener(base_url + "/api/mcp", body, headers, "POST", timeout)
    except NETWORK_ERRORS as exc:
        raise InfraError(f"{tool}: network error ({type(exc).__name__}); not retried") from None
    return parser(tool, status, raw, paged="limit" in args)


class Account:
    """Who the suite acts as: the login, its company and its own mailboxes (owned through `user_id`)."""

    def __init__(self, reader: McpClient):
        me = reader.me()
        me = me.get("user", me) if isinstance(me, dict) else None
        if not isinstance(me, dict) or safe_id(me.get("id")) is None or safe_id(me.get("company_id")) is None:
            raise InfraError("could not read the logged-in user")
        self.reader = reader
        self.me = me
        self.user_id = str(me["id"])
        self.company_id = str(me["company_id"])
        try:
            boxes = list_capped(reader, "Mailbox.list", {}, cap=50)["rows"]
        except Exception as exc:
            raise InfraError(f"could not list mailboxes ({type(exc).__name__})") from None
        owned = {str(b["id"]): b["email"].strip().lower() for b in boxes
                 if isinstance(b, dict) and str(b.get("user_id")) == self.user_id and safe_id(b.get("id")) is not None
                 and isinstance(b.get("email"), str) and "@" in b["email"]}
        if not owned:
            raise InfraError("this login owns no mailbox, so no test mail can be placed")
        # The first owned mailbox, by id, so every run picks the same one.
        self.mailbox_id = sorted(owned)[0]
        self.mailbox_email = owned[self.mailbox_id]
        self.mailboxes = owned


class ThreadReader:
    """Reads threads (EmailThread.get / EmailThread.list) and nothing else. The agent never needs these reads, so
    they stay out of the agent's allow-list."""

    def __init__(self, base_url: str, token: str, parser, opener=None, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._parser = parser
        self.opener = opener or urllib_opener
        self.timeout = timeout
        self.calls = 0
        self._id = 0

    def __repr__(self) -> str:  # never show the token
        return f"ThreadReader({self.base_url!r}, calls={self.calls})"

    def call(self, tool: str, arguments: dict | None = None) -> dict:
        if tool not in THREAD_READS:
            raise InfraError(f"{tool} is not a thread read")
        if self.calls >= MAX_THREAD_READS:
            raise InfraError(f"thread read limit of {MAX_THREAD_READS} per run reached")
        self.calls += 1
        self._id += 1
        try:
            return _rpc(self.base_url, self._token, self.opener, self.timeout, self._id, tool, dict(arguments or {}),
                        self._parser)
        except InfraError:
            raise
        except Exception as exc:
            if getattr(exc, "kind", None) == "not_found":
                raise
            raise InfraError(f"{tool}: {getattr(exc, 'message', type(exc).__name__)}") from None


class SeedClient:
    """Creates test threads and emails. It has no update, send or delete method, and checks every write first."""

    def __init__(self, base_url: str, token: str, account: Account, opener=None, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.account = account
        self.opener = opener or urllib_opener
        self.timeout = timeout
        self.writes = 0
        self.created: list[str] = []  # ids of everything this run created, in order
        self.threads: set[str] = set()  # ids of the threads this run created
        self._id = 0

    def __repr__(self) -> str:  # never show the token
        return f"SeedClient({self.base_url!r}, writes={self.writes})"

    def check(self, tool: str, args: dict) -> None:
        """Raise SeedRefused when a write breaks a rule. Called before any network I/O."""
        allowed = SEED_TOOLS.get(tool)
        if allowed is None:
            raise SeedRefused(f"{tool} is not a seed tool; the suite only creates threads and emails")
        if not isinstance(args, dict):
            raise SeedRefused(f"{tool}: arguments must be an object")
        extra = sorted(set(args) - allowed)
        if extra:
            raise SeedRefused(f"{tool}: argument(s) not allowed: {', '.join(extra)}")
        acct = self.account
        if str(args.get("company_id")) != acct.company_id:
            raise SeedRefused(f"{tool}: company_id must be the login's own company")
        if str(args.get("mailbox_id")) != acct.mailbox_id:
            raise SeedRefused(f"{tool}: mailbox_id must be the login's own mailbox")
        subject = args.get("subject")
        if not isinstance(subject, str) or not subject.startswith(SEED_TAG):
            raise SeedRefused(f"{tool}: subject must start with {SEED_TAG!r}")
        for key, value in args.items():
            if isinstance(value, str):
                self._check_text(tool, key, value)
            elif not isinstance(value, (bool, int, float)) and value is not None:
                raise SeedRefused(f"{tool}: {key} must be text, a number or true/false")
        if tool == "EmailThread.create":
            return
        folder = args.get("folder")
        if folder == "inbox":
            # is_answered may be true: task 7 needs mail that was already answered. It is a flag, it sends nothing.
            if args.get("is_draft") is not False or not isinstance(args.get("is_answered"), bool):
                raise SeedRefused("inbox test mail must set is_draft=false and is_answered to true or false")
            sender = str(args.get("from_email") or "").strip().lower()
            if sender in acct.mailboxes.values():
                raise SeedRefused("inbox test mail must come from a test address, not our own mailbox")
            if str(args.get("to") or "").strip().lower() != acct.mailbox_email:
                raise SeedRefused("inbox test mail must be addressed to our own mailbox")
        elif folder == "drafts":
            if args.get("is_draft") is not True:
                raise SeedRefused("a test draft must set is_draft=true")
            if str(args.get("from_email") or "").strip().lower() != acct.mailbox_email:
                raise SeedRefused("a test draft must be from our own mailbox")
        else:
            raise SeedRefused("test mail goes in the inbox or drafts folder only")
        if safe_id(args.get("thread_id")) is None or str(args["thread_id"]) not in self.threads:
            raise SeedRefused("test mail must go in a thread this run created")

    def _check_text(self, tool: str, key: str, value: str) -> None:
        if not value.isascii():
            raise SeedRefused(f"{tool}: {key} must be plain ASCII text (no look-alike letters)")
        own = set(self.account.mailboxes.values())
        if key in ("from_email", "to", "participant_emails"):
            for part in re.split(r"[,;\s]+", value):
                if part and not _EMAIL.fullmatch(part):
                    raise SeedRefused(f"{tool}: {key} has a value that is not a plain address")
        # Every address: a reserved test domain, or exactly our own mailbox. Then remove addresses from the text, so
        # their local parts are not read as domain names below.
        for m in _EMAIL.finditer(value):
            if not reserved(m.group(1)) and m.group(0).lower() not in own:
                raise SeedRefused(f"{tool}: {key} names {m.group(1)!r}; test data may only use reserved test domains")
        rest = _EMAIL.sub(" ", value)
        # Every link (any scheme) and every bare domain name: a reserved test domain only.
        hosts = [a or b for a, b in _URL.findall(rest)] + _HOST.findall(rest)
        for host in hosts:
            if not reserved(host):
                raise SeedRefused(f"{tool}: {key} names {host!r}; test data may only use reserved test domains")

    def create(self, tool: str, args: dict) -> dict:
        self.check(tool, args)
        if self.writes >= MAX_SEED_WRITES:
            raise SeedRefused(f"seed limit of {MAX_SEED_WRITES} writes per run reached")
        self.writes += 1  # counted before sending: a write that fails midway still counts
        self._id += 1
        try:
            payload = _rpc(self.base_url, self._token, self.opener, self.timeout, self._id, tool, args,
                           self.account.reader._parse)  # the agent's reply parser: same envelope rules
        except InfraError:
            raise
        except Exception as exc:
            raise InfraError(f"{tool}: {getattr(exc, 'message', type(exc).__name__)}") from None
        row = row_of(payload)
        if row is None or safe_id(row.get("id")) is None:
            raise InfraError(f"{tool}: the reply has no record id")
        self.created.append(str(row["id"]))
        if tool == "EmailThread.create":
            self.threads.add(str(row["id"]))
        return row
