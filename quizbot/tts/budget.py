"""ElevenLabs budget governor (Ben, 2026-11-09: "use ~100 % of the credits if it's good for the content").

Before a render, look at the live subscription and decide the voice for this episode:
- An episode that would normally use ElevenLabs (Shorts, since the voice test chose Liam) is moved to OpenAI
  *before* rendering when the credits left can't cover it — a clean switch instead of a mid-video fallback.
- An episode that would normally use OpenAI (the weekly long-form) gets Liam when the credits left exceed what
  the rest of the cycle's Shorts are projected to need, so a monthly surplus goes into the long-form rather than
  expiring. The projection is self-calibrating: this cycle's spend rate so far, times the days left, plus 10 %.
Nothing here fails a run: any error leaves the planned voice alone.
"""
from __future__ import annotations

import os
import time

LIAM = ("elevenlabs", "name:Liam")
FALLBACK = ("openai", "coral")


def spoken_chars(ep) -> int:
    """Rough character count of everything the host says in this episode (ElevenLabs bills per character)."""
    n = len(ep.intro or "") + len(ep.outro or "")
    for it in ep.items:
        n += sum(len(h) for h in (it.hints or [])) + len(it.fact or "") + len(it.answer or "") + 20
    return int(n * 1.15)


def subscription() -> dict | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    import requests
    r = requests.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key}, timeout=20)
    r.raise_for_status()
    s = r.json()
    return {"used": int(s.get("character_count") or 0), "limit": int(s.get("character_limit") or 0),
            "reset": int(s.get("next_character_count_reset_unix") or 0), "tier": s.get("tier")}


def govern(settings, ep) -> str:
    """Adjust settings.tts_provider / tts_voice for this episode. Returns a tag describing what happened ('' if nothing)."""
    try:
        sub = subscription()
    except Exception as e:
        print(f"[budget] elevenlabs subscription unavailable ({str(e)[:80]}); keeping the planned voice")
        return ""
    if not sub or not sub["limit"]:
        return ""
    now = time.time()
    remaining = sub["limit"] - sub["used"]
    days_left = max(0.5, (sub["reset"] - now) / 86400) if sub["reset"] else 15.0
    days_elapsed = max(1.0, 30.0 - days_left)
    daily_rate = sub["used"] / days_elapsed
    projected = daily_rate * days_left * 1.10          # what the rest of the cycle's regular output needs
    cost = spoken_chars(ep)
    planned_eleven = str(settings.tts_provider) == "elevenlabs"
    print(f"[budget] elevenlabs {sub['used']:,}/{sub['limit']:,} used, {days_left:.1f} d to reset, "
          f"~{daily_rate:,.0f}/day → projected need {projected:,.0f}, this episode ~{cost:,}")
    if planned_eleven and remaining < cost:
        settings.tts_provider, settings.tts_voice = FALLBACK
        print("[budget] not enough credits for this episode: using openai/coral (tagged exp:voice=fallback)")
        return "voice:fallback-budget"
    if not planned_eleven and remaining - cost >= projected and ep.format == "long":
        settings.tts_provider, settings.tts_voice = LIAM
        print("[budget] surplus covers it: long-form gets Liam this week")
        return "voice:liam-surplus"
    return ""
