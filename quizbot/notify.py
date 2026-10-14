"""Tell a human what happened. Webhook if configured (Slack/Discord both accept {"text": ...} /
{"content": ...}); otherwise just print. The log file is what you grep when a scheduled run misbehaves."""
from __future__ import annotations

import datetime as dt
import json
import urllib.request

from .config import Settings


def notify(settings: Settings, text: str) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {text}"
    print(line)
    with open(settings.state_dir / "quizbot.log", "a") as f:
        f.write(line + "\n")
    if settings.notify_webhook:
        try:
            payload = json.dumps({"text": text, "content": text}).encode()
            req = urllib.request.Request(settings.notify_webhook, data=payload,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"[notify] webhook failed: {e}")
