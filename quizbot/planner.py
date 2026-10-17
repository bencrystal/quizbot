"""Turn a pack + theme into an Episode. Two modes:
  template — deterministic strings, zero cost, always works.
  claude   — Claude writes the title, description, intro/outro, per-round host banter, and rewrites each
             fact to sound good read aloud. Falls back to template on any error so a run never dies here.
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
    print(f"[plan] format={settings.format} mechanic={settings.mechanic} theme={theme['label']}")

    pool = pack.candidates(theme)
    items = pack.select(pool, state.used_ids(pack.name), settings.rounds, rng,
                        famous_only=(settings.format == "short" and settings.shorts_famous_only))
    if not items:
        raise SystemExit(f"No candidates for theme {theme['label']}")
    if settings.mechanic == "choice":
        for it in items:
            it.choices = pack.make_choices(it, pool, rng)

    today = dt.date.today().isoformat()
    slug = f"{pack.name}-{settings.format}-{slugify(theme['label'])}-{today}"
    if settings.format == "short":
        slug += f"-{slugify(items[0].answer)}"
    if settings.mechanic != "ladder":
        slug += f"-{settings.mechanic}"
    ep = _template_episode(pack, theme, items, slug, settings)

    if settings.planner == "claude":
        try:
            ep = _claude_polish(ep, pack, settings)
        except Exception as e:
            print(f"[plan] Claude planner failed ({e}); using template copy")
    return ep


def _template_episode(pack: Pack, theme: dict, items: list[QuizItem], slug: str, settings: Settings) -> Episode:
    n = len(items)
    short = settings.format == "short"
    answers = "\n".join(f"{i+1}. {it.answer}" for i, it in enumerate(items))
    hashtags = f"#quiz #audioquiz #{slugify(pack.name)}" + (" #shorts" if short else "")

    if short:
        title = f"Can you name this {pack.noun} from its sound? ({theme['label']}) #shorts"
        desc = (f"{pack.channel_blurb}\n\nOne round. Three hints. How fast did you get it?\n"
                f"Full {n if n > 1 else 10}-round quizzes every week.\n\nAnswer: {items[0].answer}\n\n{hashtags}")
        intro = pack.opening_line
        outro = "How many points did you get?"
    else:
        title = f"{pack.display_name}: {theme['label']} ({n} Rounds)"
        desc = (f"{pack.channel_blurb}\n\n{n} rounds of {theme['label']}. Guess after the sound alone for the most "
                f"points, then one less for every hint you need. Play with your eyes closed if you like — "
                f"every hint is spoken.\n\nAnswers:\n{answers}\n\n{hashtags}")
        maxp = 1 + len(items[0].hints) if settings.mechanic == "ladder" else 1
        words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
        intro = (f"Welcome to {pack.display_name}. {n} rounds, easy to hard. "
                 + (f"{words.get(maxp, maxp)} points if you get it from the sound alone, then one less for every hint you need. "
                    if settings.mechanic == "ladder" else "One point per round. ")
                 + f"Today's theme: {theme['label']}.")
        outro = (f"That's {n} rounds, {maxp * n} points possible. "
                 f"Drop your score in the comments, and subscribe for next week's quiz.")
    tags = ["quiz", "audio quiz", "trivia", "guessing game", pack.name, theme["label"]]
    return Episode(slug=slug, pack=pack.name, title=title[:100], description=desc, tags=tags,
                   intro=intro, outro=outro, items=items, theme=theme["label"],
                   format=settings.format, mechanic=settings.mechanic, stage_seconds=settings.stage)


def _claude_polish(ep: Episode, pack: Pack, settings: Settings) -> Episode:
    import anthropic  # optional dependency

    client = anthropic.Anthropic()
    payload = {
        "show": pack.display_name, "format": ep.format, "theme": ep.theme, "mechanic": ep.mechanic,
        "rounds": [{"n": i + 1, "answer": it.answer, "hints": it.hints, "fact": it.fact} for i, it in enumerate(ep.items)],
    }
    short = ep.format == "short"
    prompt = f"""You write narration for a YouTube audio-quiz channel called "{settings.channel_name}".
The host plays a sound; viewers guess; then hints are read one at a time ({ep.stage_seconds}s of thinking time
after each), then the host reveals the answer and reads one fact. Many viewers listen without watching.

{"This is a 9:16 SHORT: one round, ~40 seconds total. Hook in the first sentence." if short else
 f"This is a long-form episode of {len(ep.items)} rounds, ordered easy to hard."}

Return JSON with keys:
  title        - YouTube title, under {60 if short else 70} chars, specific and curiosity-driven, no ALL CAPS{", ends with #shorts" if short else ""}
  description  - 2 sentences (we append the answer list ourselves)
  intro        - {"1 short spoken sentence" if short else "2-3 short spoken sentences; explain the 3/2/1 scoring in one clause"}
  outro        - 1-2 short spoken sentences asking for a score in the comments
  banter       - list of {{"n": int, "line": str}}: an optional 3-8 word spoken reaction the host says right after
                 revealing the answer for that round (e.g. "A classic. Everyone got that one."). Vary them; at most
                 half the rounds get one; never spoil a later answer.
  facts        - list of {{"n": int, "fact": str}}: rewrite each fact into 1-2 spoken sentences, accurate to the
                 source text, starting with the answer's name, no invented facts.
Plain text only, no emoji, keep names exactly as given.

Episode plan:
{json.dumps(payload, ensure_ascii=False, indent=1)}
"""
    msg = client.messages.create(model=settings.claude_model, max_tokens=4000,
                                 messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])

    ep.title = data.get("title", ep.title)[:100]
    answers = "\n".join(f"{i+1}. {it.answer}" for i, it in enumerate(ep.items))
    hashtags = f"#quiz #audioquiz #{slugify(pack.name)}" + (" #shorts" if short else "")
    label = "Answer" if short else "Answers"
    ep.description = f"{data.get('description', '').strip()}\n\n{label}:\n{answers}\n\n{hashtags}"
    ep.intro = data.get("intro", ep.intro)
    ep.outro = data.get("outro", ep.outro)
    ep.banter = {int(b["n"]): b["line"].strip() for b in data.get("banter", []) if b.get("line")}
    facts = {int(f["n"]): f["fact"] for f in data.get("facts", [])}
    for i, it in enumerate(ep.items, 1):
        if facts.get(i, "").strip():
            it.fact = facts[i].strip()
    print(f"[plan] Claude title: {ep.title}")
    return ep
