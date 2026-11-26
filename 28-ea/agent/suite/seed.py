"""Helpers that put a task's test data on the platform. Every write goes through SeedClient's checks.

Each email gets:
- a subject that starts with SEED_TAG;
- a Message-ID unique to this run, task and key, so checks can find replies to exactly this email;
- received_at = now, so it is the newest mail in the inbox and the agent reads it first.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .platform import SEED_TAG, InfraError, row_of
from .task import Context

_SAFE = re.compile(r"[^A-Za-z0-9-]")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def message_id(ctx: Context, key: str) -> str:
    run = _SAFE.sub("-", ctx.run_id)[:40]
    task = _SAFE.sub("-", ctx.task_id)[:40]
    return f"<{run}.{task}.{_SAFE.sub('-', key)}@team28a-seed.example>"


def read_back(ctx: Context, record_id: str) -> dict:
    try:
        row = row_of(ctx.reader.call("EmailMessage.get", {"id": record_id}))
    except Exception as exc:
        raise InfraError(f"could not read back test mail ({type(exc).__name__})") from None
    if not row or str(row.get("id")) != str(record_id):
        raise InfraError("test mail could not be read back after it was created")
    return row


def inbox_mail(ctx: Context, key: str, *, sender: str, sender_name: str, subject: str, body: str,
               **extra) -> dict:
    """One unread, unanswered email in our inbox, in a thread of its own. Returns it as read back.
    `extra` may set other allowed fields, e.g. is_answered=True (task 7) or list_unsubscribe (task 5)."""
    if key in ctx.seeds:
        raise InfraError(f"seed key {key!r} used twice in one task")
    acct = ctx.account
    tagged = f"{SEED_TAG} {subject}"
    thread = ctx.seeder.create("EmailThread.create", {
        "company_id": acct.company_id, "mailbox_id": acct.mailbox_id, "subject": tagged,
        "participant_emails": f"{sender}, {acct.mailbox_email}", "participant_names": sender_name})
    msg = ctx.seeder.create("EmailMessage.create", {
        "company_id": acct.company_id, "mailbox_id": acct.mailbox_id, "thread_id": str(thread["id"]),
        "folder": "inbox", "is_draft": False, "is_read": False, "is_answered": False,
        "message_id": message_id(ctx, key), "from_email": sender, "from_name": sender_name,
        "to": acct.mailbox_email, "subject": tagged, "body_text": body, "received_at": _now(), **extra})
    ctx.seeds[key] = read_back(ctx, str(msg["id"]))
    ctx.seed_threads[key] = read_thread(ctx, str(thread["id"]))
    return ctx.seeds[key]


def read_thread(ctx: Context, thread_id: str) -> dict:
    if ctx.threads is None:
        raise InfraError("no thread reader: the runner did not set one up")
    try:
        row = row_of(ctx.threads.call("EmailThread.get", {"id": thread_id}))
    except InfraError:
        raise
    except Exception as exc:
        raise InfraError(f"could not read back a test thread ({type(exc).__name__})") from None
    if not row or str(row.get("id")) != str(thread_id):
        raise InfraError("a test thread could not be read back after it was created")
    return row


def draft_reply(ctx: Context, key: str, original: dict, body: str) -> dict:
    """An unsent reply draft to a seeded email, as if the user had saved it earlier. Returns it as read back."""
    if key in ctx.seeds:
        raise InfraError(f"seed key {key!r} used twice in one task")
    acct = ctx.account
    msg = ctx.seeder.create("EmailMessage.create", {
        "company_id": acct.company_id, "mailbox_id": acct.mailbox_id, "thread_id": str(original["thread_id"]),
        "folder": "drafts", "is_draft": True, "is_read": True,
        "from_email": acct.mailbox_email, "to": original["from_email"],
        "subject": f"{SEED_TAG} Re: {str(original.get('subject') or '').replace(SEED_TAG, '').strip()}",
        "in_reply_to": original["message_id"], "body_text": body})
    ctx.seeds[key] = read_back(ctx, str(msg["id"]))
    return ctx.seeds[key]
