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

SHORT_SLOTS = (11, 15, 19, 23)
LONG_FROM = (12, 30)


def main() -> None:
    s = Settings.load()
    now = dt.datetime.now(dt.timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    st = State(s.state_dir / "quizbot.db")
    shorts_today = st.count_episodes("short", since=day0)
    longs_today = st.count_episodes("long", since=day0)
    slots_passed = sum(1 for h in SHORT_SLOTS if now.hour >= h)
    long_due = (now.hour, now.minute) >= LONG_FROM and longs_today == 0
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
