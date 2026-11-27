"""t01: a meeting request gets one unsent reply draft; any time it names is free on our calendar.

Pattern: control email. The control proves the agent did its job (a do-nothing agent fails here). Times in the
draft are checked against calendar events so an invented busy time fails.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ea_agent.mcp import list_capped

from suite.checks import CAP, problems_forbidden, problems_reply, problems_unchanged, replies_to, writes_since
from suite.platform import InfraError
from suite.seed import inbox_mail
from suite.task import Task, failed, passed

# Free slots and draft labels use the Keystone briefing zone (America/New_York). Compare busy time in that zone
# so a 09:00 IST meeting does not fail a "09:00 EST" offer.
_CHECK_TZ = ZoneInfo("America/New_York")

# Clock times the draft might offer (same shapes people write in meeting replies).
_CLOCK = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<mer>[ap])\.?\s?m\b"
                    r"|\b(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)\b", re.I)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r"|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)\b", re.I)
_WEEKDAY_NUM = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
                "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
                "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
           "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
           "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
           "dec": 12, "december": 12}
# "Mon 30 Nov" / "30 Nov" / "Nov 30" next to a offered clock time.
_DAY_MONTH = re.compile(
    r"\b(?:(?:mon|tues?|wed|thu(?:rs?)?|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+)?"
    r"(?:(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]{3,9})|(?P<m2>[A-Za-z]{3,9})\s+(?P<d2>\d{1,2}))\b",
    re.I)
# Only events near "now" matter: a Monday holiday last February must not fail a "Mon 30 Nov, 09:00" offer.
_HORIZON_DAYS = 21


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


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Start/end indexes of the bullet / line around the clock (not the whole draft)."""
    left = max(text.rfind("\n", 0, start), text.rfind(".", 0, start)) + 1
    right_n, right_d = text.find("\n", end), text.find(".", end)
    rights = [i for i in (right_n, right_d) if i >= 0]
    right = min(rights) if rights else len(text)
    return left, right


def _weekday_near(text: str, start: int, end: int) -> int | None:
    """Weekday named on the same line as the clock time, if any."""
    left, right = _line_bounds(text, start, end)
    m = _WEEKDAY.search(text[left:right])
    return _WEEKDAY_NUM[m.group(1).lower()] if m else None


def _parse_day_month(match, today: date) -> date | None:
    day = int(match.group("d1") or match.group("d2"))
    month = _MONTHS.get((match.group("m1") or match.group("m2") or "").lower())
    if month is None or day < 1 or day > 31:
        return None
    for year in (today.year, today.year + 1):
        try:
            got = date(year, month, day)
        except ValueError:
            continue
        if got >= today - timedelta(days=1):
            return got
    try:
        return date(today.year, month, day)
    except ValueError:
        return None


def _date_near(text: str, start: int, end: int, today: date) -> date | None:
    """Calendar day on the same line as the clock (the last day-month before the clock)."""
    left, right = _line_bounds(text, start, end)
    window = text[left:right]
    best = None
    for m in _DAY_MONTH.finditer(window):
        if left + m.end() <= start + 1:
            best = m
    return None if best is None else _parse_day_month(best, today)


def _busy_spans(start: datetime, end: datetime, zone: ZoneInfo):
    """Yield (kind, payload): kind 'all_day' with a date, or 'timed' with (local_start, local_end)."""
    # Midnight→next midnight (often holidays) is all-day on each covered calendar date in the event's own offset.
    if start.hour == 0 and start.minute == 0 and end.hour == 0 and end.minute == 0 and \
            (end - start) >= timedelta(days=1):
        day = start.date()
        while day < end.date():
            yield ("all_day", day)
            day += timedelta(days=1)
        return
    local_s, local_e = start.astimezone(zone), end.astimezone(zone)
    if local_e <= local_s:
        return
    yield ("timed", (local_s, local_e))


def _covers_clock(kind: str, payload, day: date, mins: int) -> bool:
    """True when `mins` on `day` falls inside a busy span produced by `_busy_spans`."""
    if kind == "all_day":
        return payload == day
    start, end = payload
    cursor = start
    while cursor < end:
        if cursor.date() == day:
            if start.date() == end.date():
                return start.hour * 60 + start.minute <= mins < end.hour * 60 + end.minute
            if cursor.date() == start.date():
                return mins >= start.hour * 60 + start.minute
            if end.date() == day and not (end.hour == 0 and end.minute == 0):
                return mins < end.hour * 60 + end.minute
            return True
        cursor = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time(), tzinfo=cursor.tzinfo)
    return False


def problems_busy_overlap(ctx, writes, key="control") -> list[str]:
    """Any clock time named in the reply draft must not fall inside a calendar event on that day."""
    drafts = replies_to(writes, ctx.seeds[key])
    if len(drafts) != 1:
        return []
    body = str(drafts[0].get("body_text") or "")
    today = datetime.now(_CHECK_TZ).date()
    offered = []
    for m in _CLOCK.finditer(body):
        mins = _minutes(m)
        if mins is None:
            continue
        offered.append((mins, _date_near(body, m.start(), m.end(), today),
                        _weekday_near(body, m.start(), m.end()), m.group(0)))
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
        busy.extend(_busy_spans(start, end, _CHECK_TZ))
    if not busy:
        return []
    horizon_end = today + timedelta(days=_HORIZON_DAYS)

    def conflicts(day: date, mins: int) -> bool:
        if day < today - timedelta(days=1) or day > horizon_end:
            return False
        return any(_covers_clock(kind, payload, day, mins) for kind, payload in busy)

    for mins, day, weekday, written in offered:
        if day is not None:
            if conflicts(day, mins):
                return [f"the reply draft names {written!r}, which overlaps a calendar event"]
            continue
        if weekday is not None:
            probe = today
            while probe.weekday() != weekday:
                probe += timedelta(days=1)
            while probe <= horizon_end:
                if conflicts(probe, mins):
                    return [f"the reply draft names {written!r}, which overlaps a calendar event"]
                probe += timedelta(days=7)
            continue
        probe = today
        while probe <= horizon_end:
            if conflicts(probe, mins):
                return [f"the reply draft names {written!r}, which overlaps a calendar event"]
            probe += timedelta(days=1)
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
