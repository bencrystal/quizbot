"""What the hourly schedule should render right now, from the ledger: `short`, `long`, or `none`.

GitHub's cron is best-effort (runs are delayed for hours or dropped under load — 2026-10-19 lost three), so
daily.yml runs every hour and asks this instead of trusting the firing time. Slots (UTC): Shorts at 11, 15, 19,
23; long-form from 12:30. Anything missed earlier in the day catches up on the next hourly run; the daily cap
in settings still applies. A day is UTC.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quizbot.config import Settings  # noqa: E402
from quizbot.state import State  # noqa: E402

DAY_START = 10                       # the broadcast day runs 10:00 UTC → 10:00 UTC, so the 23:00 slot can still
SHORT_SLOTS = (11, 15, 19, 23)       # catch up after midnight instead of being forgotten at the UTC date change
LONG_FROM = 12.5


def main() -> None:
    s = Settings.load()
    now = dt.datetime.now(dt.timezone.utc)
    start = now.replace(hour=DAY_START, minute=0, second=0, microsecond=0)
    if now < start:
        start -= dt.timedelta(days=1)
    hours = (now - start).total_seconds() / 3600
    day0 = start.timestamp()
    st = State(s.state_dir / "quizbot.db")
    shorts_today = st.count_episodes("short", since=day0)
    longs_today = st.count_episodes("long", since=day0)
    slots_passed = sum(1 for h in SHORT_SLOTS if hours >= h - DAY_START)
    long_due = hours >= LONG_FROM - DAY_START and longs_today == 0
    # long-form first when both are due: it is the one with a weekly quota to meet
    if long_due:
        print("long")
    elif shorts_today < slots_passed:
        print("short")
    else:
        print("none")
    print(f"[due] {now:%H:%M}Z shorts {shorts_today}/{slots_passed} slots, longs today {longs_today}", file=sys.stderr)


if __name__ == "__main__":
    main()
