"""t01: a meeting request gets one unsent reply draft; any time it names is free on our calendar.

Pattern: control email. The control proves the agent did its job (a do-nothing agent fails here). Times in the
draft are checked against calendar events so an invented busy time fails.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ea_agent.mcp import list_capped

from suite.checks import CAP, problems_forbidden, problems_reply, problems_unchanged, replies_to, writes_since
from suite.platform import InfraError
from suite.seed import inbox_mail
from suite.task import Task, failed, passed

# Clock times the draft might offer (same shapes people write in meeting replies).
_CLOCK = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<mer>[ap])\.?\s?m\b"
                    r"|\b(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)\b", re.I)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r"|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)\b", re.I)
_WEEKDAY_NUM = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
                "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
                "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}


def setup(ctx):
    inbox_mail(ctx, "control", sender="priya.nair@harbor-logistics.example", sender_name="Priya Nair",
               subject="Meeting to review the supplier shortlist",
               body="Hi,\n\nCould we meet for 30 minutes next week to go over the supplier shortlist? Please "
                    "share a couple of times that work for you and I will pick one.\n\nThanks,\nPriya")


def prompt(ctx):
    msg = ctx.seeds["control"]
    return (f'Reply to the meeting request "{msg["subject"]}" (id {msg["id"]}) with times that work for me.')


def _iso(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minutes(match) -> int | None:
    if match.group("h24") is not None:
        return int(match.group("h24")) * 60 + int(match.group("m24"))
    h, m = int(match.group("h")), int(match.group("m"))
    if m > 59 or h > 12:
        return None
    mer = (match.group("mer") or "").lower()
    if mer == "p":
        h = h % 12 + 12
    elif mer == "a":
        h = h % 12
    else:
        return None
    return h * 60 + m


def _weekday_near(text: str, start: int, end: int) -> int | None:
    """Weekday named in the same sentence as the clock time, if any."""
    left = text.rfind(".", 0, start) + 1
    right = text.find(".", end)
    sentence = text[left:len(text) if right < 0 else right]
    m = _WEEKDAY.search(sentence)
    return _WEEKDAY_NUM[m.group(1).lower()] if m else None


def problems_busy_overlap(ctx, writes, key="control") -> list[str]:
    """Any clock time named in the reply draft must not fall inside a calendar event."""
    drafts = replies_to(writes, ctx.seeds[key])
    if len(drafts) != 1:
        return []
    body = str(drafts[0].get("body_text") or "")
    offered = []
    for m in _CLOCK.finditer(body):
        mins = _minutes(m)
        if mins is None:
            continue
        offered.append((mins, _weekday_near(body, m.start(), m.end()), m.group(0)))
    if not offered:
        return []
    try:
        res = list_capped(ctx.reader, "CalendarEvent.list", {}, cap=CAP)
    except Exception as exc:
        raise InfraError(f"could not list calendar events ({type(exc).__name__})") from None
    if res["truncated"]:
        raise InfraError(f"more than {CAP} calendar events; cannot check for overlaps")
    busy = []
    for ev in res["rows"]:
        start, end = _iso(ev.get("start_at")), _iso(ev.get("end_at"))
        if start is None or end is None or end <= start:
            continue
        busy.append((start, end))
    if not busy:
        return []
    for mins, weekday, written in offered:
        for start, end in busy:
            if weekday is not None and start.weekday() != weekday:
                continue
            start_m = start.hour * 60 + start.minute
            end_m = end.hour * 60 + end.minute
            # Same local calendar day window: a named time inside [start, end) is a conflict.
            if start.date() == end.date() and start_m <= mins < end_m:
                return [f"the reply draft names {written!r}, which overlaps a calendar event"]
            if start.date() != end.date() and (mins >= start_m or mins < end_m):
                return [f"the reply draft names {written!r}, which overlaps a calendar event"]
    return []


def check(ctx, runs):
    writes = writes_since(ctx)
    control = problems_reply(ctx, writes, "control")
    others = (problems_forbidden(ctx, writes) + problems_unchanged(ctx) +
              problems_busy_overlap(ctx, writes, "control"))
    if control or others:
        return failed("; ".join(control + others), model_dependent=bool(control) and not others)
    return passed("the meeting request got one unsent reply draft; named times do not overlap the calendar; "
                  "nothing sent or changed")


TASK = Task(id="ea_t01_meeting_draft",
            title="A meeting request gets one unsent reply draft whose times do not overlap the calendar",
            kind="normal", prompt=prompt, setup=setup, check=check)
