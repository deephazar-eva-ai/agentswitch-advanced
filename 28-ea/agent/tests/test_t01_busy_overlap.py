"""Unit checks for t01 busy-overlap: dated free slots vs timezone / all-day false positives."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from suite.tasks import t01_meeting_draft as t01


def test_date_near_uses_same_line_not_first_in_draft():
    today = date(2026, 11, 27)
    body = ("Options:\n"
            "- Mon 30 Nov, 09:00–09:30 EST\n"
            "- Tue 1 Dec, 09:00–09:30 EST\n")
    clocks = list(t01._CLOCK.finditer(body))
    # First line → 30 Nov; second line's first 09:00 → 1 Dec (not 30 Nov).
    assert t01._date_near(body, clocks[0].start(), clocks[0].end(), today) == date(2026, 11, 30)
    assert t01._date_near(body, clocks[2].start(), clocks[2].end(), today) == date(2026, 12, 1)


def test_ist_nine_am_does_not_conflict_with_est_nine_am_offer():
    # 09:00 IST on 1 Dec is 22:30 EST on 30 Nov — must not fail a "Tue 1 Dec, 09:00 EST" offer.
    start = datetime.fromisoformat("2026-12-01T09:00:00+05:30")
    end = datetime.fromisoformat("2026-12-01T09:30:00+05:30")
    spans = list(t01._busy_spans(start, end, ZoneInfo("America/New_York")))
    assert spans and spans[0][0] == "timed"
    local_s, local_e = spans[0][1]
    assert local_s.date() == date(2026, 11, 30)
    assert not t01._covers_clock("timed", (local_s, local_e), date(2026, 12, 1), 9 * 60)


def test_all_day_holiday_blocks_that_date_only():
    start = datetime(2026, 11, 26, tzinfo=timezone.utc)
    end = datetime(2026, 11, 27, tzinfo=timezone.utc)
    spans = list(t01._busy_spans(start, end, ZoneInfo("America/New_York"))
                 )
    assert ("all_day", date(2026, 11, 26)) in spans
    assert t01._covers_clock("all_day", date(2026, 11, 26), date(2026, 11, 26), 9 * 60)
    assert not t01._covers_clock("all_day", date(2026, 11, 26), date(2026, 11, 30), 9 * 60)
