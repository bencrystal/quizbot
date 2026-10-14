"""Turn a pack + theme into an Episode. Two modes:
  template — deterministic strings, zero cost, always works.
  claude   — Claude writes the title, description, intro/outro and rewrites each fact to be
             punchier for narration. Falls back to template on any error so a run never dies here.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re

from .config import Settings
from .models import Episode, QuizItem
from .packs import Pack
from .state import State


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def plan_episode(pack: Pack, settings: Settings, state: State, seed: int | None = None,
                 theme_id: str | None = None) -> Episode:
    rng = random.Random(seed)
    themes = pack.themes()
    theme = next((t for t in themes if t["id"] == theme_id), None) if theme_id else None
    theme = theme or pack.pick_theme(state.recent_themes(pack.name), rng)
    print(f"[plan] theme: {theme['label']}")

    pool = pack.candidates(theme)
    items = pack.select(pool, state.used_ids(pack.name), settings.rounds_per_episode, rng)
    if not items:
        raise SystemExit(f"No candidates for theme {theme['label']}")

    today = dt.date.today().isoformat()
    slug = f"{pack.name}-{slugify(theme['label'])}-{today}"
    ep = _template_episode(pack, theme, items, slug, settings)

    if settings.planner == "claude":
        try:
            ep = _claude_polish(ep, pack, settings)
        except Exception as e:
            print(f"[plan] Claude planner failed ({e}); using template copy")
    return ep


def _template_episode(pack: Pack, theme: dict, items: list[QuizItem], slug: str, settings: Settings) -> Episode:
    n = len(items)
    title = f"{pack.display_name}: {theme['label']} ({n} Rounds)"
    desc = (f"{pack.channel_blurb}\n\n{n} rounds of {theme['label']}. Listen, guess before the timer runs out, "
            f"then learn something about each one.\n\nAnswers:\n" +
            "\n".join(f"{i+1}. {it.answer}" for i, it in enumerate(items)) +
            "\n\n#quiz #audioquiz #" + slugify(pack.name))
    intro = (f"Welcome to {pack.display_name}. {n} rounds, {settings.countdown_seconds} seconds each. "
             f"Today's theme: {theme['label']}. Let's go.")
    outro = "That's all for today. How many did you get? Tell me in the comments, and subscribe for more."
    tags = ["quiz", "audio quiz", "trivia", pack.name, theme["label"]]
    return Episode(slug=slug, pack=pack.name, title=title, description=desc, tags=tags,
                   intro=intro, outro=outro, items=items, theme=theme["label"],
                   countdown_seconds=settings.countdown_seconds)


def _claude_polish(ep: Episode, pack: Pack, settings: Settings) -> Episode:
    import anthropic  # optional dependency

    client = anthropic.Anthropic()
    payload = {
        "show": pack.display_name,
        "theme": ep.theme,
        "rounds": [{"n": i + 1, "answer": it.answer, "fact": it.fact} for i, it in enumerate(ep.items)],
    }
    prompt = f"""You write narration for a YouTube audio-quiz channel. The host plays a sound, viewers guess
within {ep.countdown_seconds} seconds, then the host reveals the answer and reads one interesting fact.

Given this episode plan, return JSON with keys:
  title        - YouTube title, under 70 chars, curiosity-driven, no clickbait caps
  description  - 2-3 sentence description, then a timestamps-free answer list is appended by us (don't include)
  intro        - 2 short spoken sentences welcoming viewers and naming the theme
  outro        - 1-2 short spoken sentences; ask for a comment with their score
  facts        - list of {{"n": int, "fact": str}}: rewrite each fact into 1-2 spoken sentences that are
                 accurate to the source text, start with the answer's name, and sound good read aloud.
Rules: never invent facts not in the source; keep names exactly as given; plain text, no emoji.

Episode plan:
{json.dumps(payload, ensure_ascii=False, indent=1)}
"""
    msg = client.messages.create(model=settings.claude_model, max_tokens=4000,
                                 messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    text = text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(text)

    ep.title = data.get("title", ep.title)[:100]
    answers = "\n".join(f"{i+1}. {it.answer}" for i, it in enumerate(ep.items))
    ep.description = f"{data.get('description', '').strip()}\n\nAnswers:\n{answers}\n\n#quiz #audioquiz #{slugify(pack.name)}"
    ep.intro = data.get("intro", ep.intro)
    ep.outro = data.get("outro", ep.outro)
    facts = {f["n"]: f["fact"] for f in data.get("facts", [])}
    for i, it in enumerate(ep.items, 1):
        if i in facts and facts[i].strip():
            it.fact = facts[i].strip()
    print(f"[plan] Claude title: {ep.title}")
    return ep
