# quizbot

Unattended pipeline for an educational audio-quiz YouTube channel. Each run:

```
plan (theme + items, easy → hard)  →  narration (TTS)  →  render MP4 + thumbnail  →  upload (scheduled)  →  ledger
```

Two formats, one codebase:

| format | shape | rounds | length | cadence |
|--------|-------|--------|--------|---------|
| `short` | 9:16 | 1 | ~35–45 s | daily |
| `long`  | 16:9 | 10 | ~8–10 min | weekly |

Three mechanics (`mechanic:` in settings, or `--mechanic`):

- **ladder** (default) — sound ×2 → think (3 pts) → *"Hint: it's a Water type"* + sound → think (2 pts) →
  *"Hint: it's known as the Tiny Turtle Pokémon"* → think (1 pt) → sting → reveal → fact.
  Every hint is spoken, so it plays audio-only; on screen the hints slide in one at a time and the points badge counts down.
- **choice** — sound ×2 → spoken "Is it A, Squirtle, B, …" → think → reveal. On-screen A–D list.
- **cold** — sound ×2 → think → reveal. For hardcore episodes.

Quiz types are **packs**: `pokemon` (cries + Pokédex entries via PokéAPI, cached forever) ships first; `fixture` is an
offline test pack. A new pack (bird calls, anthems, instruments, film scores) is one file.

## Quick start

```bash
cd quizbot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # + `pip install kokoro soundfile` for local narration
python -m quizbot test                     # offline: renders a Short, a 2-round ladder, a 1-round choice (~2 min)
python -m quizbot run --format short       # first real Pokémon Short (placeholder voice, no upload)
python -m quizbot run --format long --rounds 3
```

Open the MP4s in `out/`. Layout, timing and mechanics are final; only the voice is a placeholder until you pick a provider.

## 1. Voice

`tts_provider` in `config/settings.yaml` (or `QUIZBOT_TTS_PROVIDER` in `.env`):

| provider | setup | character |
|----------|-------|-----------|
| `kokoro` | `pip install kokoro soundfile` (+ `brew install espeak-ng`), `tts_voice: am_michael` / `af_heart` / `bf_emma`… or point `kokoro_url` at a running kokoro-fastapi | free, local, clear narrator |
| `elevenlabs` | `ELEVENLABS_API_KEY`, `tts_voice: <voice id>` | most expressive; can clone your voice |
| `openai` | `OPENAI_API_KEY`, `tts_voice: onyx` | cheap, fine |
| `piper` | `pip install piper-tts`, `tts_voice: /path/voice.onnx` | free, offline, flatter |

Lines are cached by text hash, so re-renders don't re-bill, and `max_tts_chars_per_run` aborts any run that would
blow through a paid tier. To A/B: render the same episode twice with `--seed 1` and two providers.

**Pronunciation.** `PRONUNCIATIONS` in `quizbot/packs/pokemon.py` maps names → phonetic spellings for the narrator;
the on-screen name stays correct. Add to it whenever you hear a bad one — this is the #1 recurring fix.

## 2. Copy (optional): `planner: claude`

With `ANTHROPIC_API_KEY`, Claude writes the title, description, intro/outro, a few short host reactions after reveals
("A classic. Everyone got that one."), and rewrites each Pokédex entry to sound good read aloud, constrained to the source
text. Any failure falls back to template copy so a scheduled run never dies here.

## 3. Music — hands-off by default

`music_provider: procedural` synthesizes a new royalty-free-by-construction bed for every episode (seeded from the
episode, so the channel has a consistent sound without repeating): soft pad on a 4-chord loop, plucked arpeggio,
light kick/hat, 84–108 BPM, random key. It sits at `music_bed_db` and is **sidechain-ducked** a further
`music_duck_db` whenever the narrator or a quiz sound plays (measured: ~15 dB dip within 15 ms, ~0.6 s recovery),
so cries are never masked.

Other options: `file` (your own `assets/music/bed.wav`), `elevenlabs` (generated from `music_prompt`; needs a plan
that licenses commercial use — verify the endpoint in their docs; falls back to procedural), `none`.

`sting.wav` (reveal hit) and `tick.wav` (per-second blip) in `assets/music/` are optional; synthesized if missing.

## 4. YouTube (one-time, ~10 min)

1. Google Cloud Console → new project → enable **YouTube Data API v3**.
2. OAuth consent screen: External; add your Google account as a test user; then **Publish** the app
   (unpublished apps expire refresh tokens after 7 days and your automation silently stops).
3. Credentials → OAuth client ID → **Desktop app** → download JSON → `config/client_secret.json`.
4. `python -m quizbot auth` — browser opens once; token lands in `state/youtube_token.json` and self-refreshes.
5. In `settings.yaml`: `upload: true`, `youtube_privacy: private` to start.

`publish_time: "12:00"` means every public upload is scheduled to go live at noon local — so it doesn't matter when
your laptop actually got around to rendering it. Quota is 10,000 units/day; an upload is ~1,600 → ~6/day, plenty.

## 5. Run it on its own

### Your MacBook (launchd)

```bash
scripts/install-launchd.sh        # daily Short 07:00, weekly long Saturday 07:30
launchctl start com.quizbot.short # fire one now
tail -f state/quizbot.log
```

launchd runs a missed job the next time the Mac is awake, and `publish_time` fixes when it goes live, so sleeping
through 07:00 is fine. To render while the lid is closed, plug in power and set
System Settings → Battery → Options → "Prevent automatic sleeping on power adapter when the display is off".

Safety rails for leaving it alone: `max_episodes_per_day` (default 2) is a hard cap the scheduler can't exceed;
`max_tts_chars_per_run` caps spend; a lock file prevents overlapping runs; failed uploads are retried on the next run,
never re-rendered; every run posts one line to `notify_webhook` (Slack/Discord) or `state/quizbot.log`.
Point `out_dir`/`cache_dir` at an external drive if you want: `QUIZBOT_OUT_DIR=/Volumes/Drive/quizbot/out`.

### GitHub Actions + Claude supervisor (recommended: fully hands-off)

The pipeline runs on GitHub Actions; a scheduled Claude task reads `SUPERVISOR.md` and checks on it weekly —
failed runs, stuck uploads, pronunciation map, spoiler-y hints — fixing what it can by commit and reporting the rest.
Claude's own cloud environment can't reach PokéAPI or YouTube directly (network allowlist), which is why the
rendering lives in Actions and Claude supervises through GitHub.

### GitHub Actions (details)

`.github/workflows/daily.yml` — add the secrets listed at the top of the file. Kokoro runs on the free CPU runners.
The ledger lives in the Actions cache so items don't repeat. Set repo *variables* `QUIZBOT_TTS_PROVIDER` /
`QUIZBOT_PLANNER` to switch providers without editing the workflow.

### Operating loop

```
python -m quizbot status                               # every episode and its state
python -m quizbot retry-uploads                        # anything stuck at "rendered"
python -m quizbot themes --pack pokemon
python -m quizbot run --format long --theme legendaries --mechanic choice --upload --force
```

Rollout: week 1 render-only, watch them; week 2 `upload: true` + `private`; week 3 `public`.

## Adding a quiz type

`quizbot/packs/birds.py`:

```python
class BirdsPack(Pack):
    name = "birds"
    display_name = "Guess That Bird"
    question_line = "Round {n}. Which bird is calling?"
    def themes(self): return [{"id": "backyard-us", "label": "Backyard birds of the US"}]
    def candidates(self, theme):
        return [QuizItem(id=..., answer=..., prompt_audio=..., reveal_image=..., fact=...,
                         hints=["it's a songbird", "it's named after its colour"], difficulty=1.0)]
```

Register it in `get_pack()`. Narration, both layouts, all three mechanics, dedup, upload and scheduling just work.
Free CC-licensed sources: xeno-canto (bird calls), Wikimedia Commons (anthems, instruments), Freesound (CC0 filter).

## Layout

```
quizbot/
  models.py        QuizItem / Episode — the contract between packs and everything else
  config.py        settings.yaml + .env + QUIZBOT_* env; format presets
  state.py         SQLite: used items, episodes, upload status, daily cap
  planner.py       theme/item selection, difficulty ordering, template or Claude copy
  packs/           pokemon.py, fixture.py, + yours
  tts/             dev | kokoro | openai | elevenlabs | piper — cached, budget-capped
  render/          cards.py (Pillow, 16:9 + 9:16) and ffmpeg assembly, loudnorm, music bed
  upload/          YouTube Data API v3: resumable upload, thumbnail, publish_time scheduling
  notify.py        log + webhook
scripts/           run.sh, install-launchd.sh, two launchd plists
.github/workflows  daily.yml
assets/music/      bed.wav, sting.wav, tick.wav (yours)
```

## Before going public

- Pokémon cries, artwork and Pokédex text are Nintendo/Game Freak IP. Many channels do this; it's takedown-exposed,
  and YouTube's reused-content policy can block monetisation on purely templated output. Your own voice (ElevenLabs
  clone), your own music, the hint-ladder commentary and the branded design all help. Packs built on CC audio are
  the safer long-term base for the channel.
- Keep `made_for_kids: false` unless it really is — it disables comments, which kills the "post your score" loop.
- Watch the first few end to end; fix pronunciations; then stop watching.
