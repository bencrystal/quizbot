# quizbot

Unattended pipeline for an educational audio-quiz YouTube channel. Each run:

```
plan (pick theme + items)  →  TTS narration  →  render MP4 + thumbnail  →  upload  →  record in SQLite
```

Quiz types are **packs**. `pokemon` (cries + Pokédex entries via PokéAPI) ships first; `fixture` is an
offline test pack. Adding bird calls / national anthems / film scores / instrument sounds is one new file.

## Quick start (any machine with Python 3.11+ and ffmpeg)

```bash
cd quizbot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m quizbot test                 # offline smoke test → out/fixture-*/*.mp4 (~1 min)
python -m quizbot run --pack pokemon   # first real episode, silent "dev" voice, no upload
```

Open the MP4. The layout, timing and silhouette logic are what you'll see on YouTube; only the voice is a placeholder.

## Turn on a real voice

Copy `.env.example` → `.env`, add a key, and set the provider (in `.env` or `config/settings.yaml`):

| provider     | setup                                                        | notes                                   |
|--------------|--------------------------------------------------------------|-----------------------------------------|
| `openai`     | `OPENAI_API_KEY`, optional `QUIZBOT_TTS_VOICE=onyx`          | cheap, zero friction — start here       |
| `elevenlabs` | `ELEVENLABS_API_KEY`, `QUIZBOT_TTS_VOICE=<voice id>`         | best quality, costs more                |
| `piper`      | `pip install piper-tts`, `QUIZBOT_TTS_VOICE=/path/voice.onnx` | free, offline, robotic-ish              |

Every spoken line is cached by text hash, so re-renders don't re-bill.

**Pronunciation:** TTS mangles proper nouns. `PRONUNCIATIONS` in `quizbot/packs/pokemon.py` maps
names → phonetic spellings; add to it whenever you hear a bad one. The on-screen name stays correct.

## Turn on Claude copywriting (optional)

`planner: claude` + `ANTHROPIC_API_KEY`. Claude writes the title, description, intro/outro, and rewrites
each raw fact into something that sounds good read aloud, constrained to the source text. Falls back to
template copy on any error so a scheduled run never dies here.

## YouTube upload (one-time setup, ~10 min)

1. Google Cloud Console → new project → enable **YouTube Data API v3**.
2. OAuth consent screen: External, add your Google account as a test user, then **publish** the app
   (otherwise refresh tokens expire after 7 days and automation breaks).
3. Credentials → OAuth client ID → **Desktop app** → download JSON → save as `config/client_secret.json`.
4. `python -m quizbot auth` — opens a browser once; token lands in `state/youtube_token.json` and
   refreshes itself forever after.
5. In `config/settings.yaml`: `upload: true`, `youtube_privacy: private` to start.

Quota: 10,000 units/day by default; an upload is ~1,600, so ~6 uploads/day. Fine for a daily channel.
`schedule_hours_ahead: 24` lets you batch-render ahead and have YouTube publish on a drip.

## Run it on its own

### Mac Mini (launchd — recommended, it's already on)

```bash
sed "s#__REPO__#$PWD#g" scripts/com.quizbot.daily.plist > ~/Library/LaunchAgents/com.quizbot.daily.plist
launchctl load ~/Library/LaunchAgents/com.quizbot.daily.plist
launchctl start com.quizbot.daily        # fire one now to prove it works
tail -f state/quizbot.log
```

`scripts/run.sh` is the entry point: takes a lock (no double renders), drains failed uploads from the
previous run, renders one episode, uploads, logs. launchd replays missed runs after sleep.
Change the hour in the plist; for several per day add more `StartCalendarInterval` dicts.

### GitHub Actions (no machine needed)

`.github/workflows/daily.yml` — add the secrets listed at the top of the file. The used-items DB is kept in
the Actions cache so Pokémon don't repeat.

### Notifications

Set `notify_webhook` to a Slack/Discord incoming webhook. Each run posts one line: rendered/uploaded +
link, or the error. Reading that line once a day is your entire operating burden.

### Operating loop

```
python -m quizbot status          # every episode and its state
python -m quizbot retry-uploads   # anything stuck at "rendered"
python -m quizbot themes --pack pokemon
python -m quizbot run --pack pokemon --theme legendaries --upload
```

Suggested rollout: week 1 render-only and watch them; week 2 `upload: true` + `private`; week 3 `public`.

## Adding a quiz type

Create `quizbot/packs/<name>.py`:

```python
class BirdsPack(Pack):
    name = "birds"
    display_name = "Guess That Bird"
    question_line = "Number {n}. Which bird is calling?"
    def themes(self): return [{"id": "backyard-us", "label": "Backyard birds of the US", ...}]
    def candidates(self, theme): return [QuizItem(id=..., answer=..., prompt_audio=..., fact=..., reveal_image=...)]
```

Register it in `get_pack()` in `quizbot/packs/__init__.py`. That's it — narration, rendering, dedup,
upload, scheduling all just work. Good next packs with free audio: xeno-canto (bird calls, CC-licensed),
Wikimedia Commons (national anthems, instruments, animal sounds), Freesound (CC0 filter).

## Layout

```
quizbot/
  models.py        QuizItem / Episode — the contract between packs and everything else
  config.py        settings.yaml + .env + QUIZBOT_* env overrides
  state.py         SQLite: used items, episodes, upload status (makes re-runs idempotent)
  planner.py       theme/item selection; template or Claude copywriting
  packs/           pokemon.py, fixture.py, + yours
  tts/             dev | openai | elevenlabs | piper, cached
  render/          cards.py (Pillow stills) + ffmpeg assembly, loudnorm, optional music bed
  upload/          YouTube Data API v3 (resumable upload, thumbnail, scheduled publish)
  notify.py        log + webhook
  __main__.py      CLI
scripts/           run.sh (launchd/cron entry), com.quizbot.daily.plist
.github/workflows  daily.yml
assets/music/      drop a royalty-free loop here and set music_bed in settings
```

## Things to know before going public

- Pokémon cries, artwork and Pokédex text are Nintendo/Game Freak IP. Many channels do this; it's
  takedown-exposed and YouTube's reused-content policy can block monetisation on purely templated output.
  Your own voice/commentary, an original music bed, and the branded card design all help. Packs built on
  CC-licensed audio (birds, anthems) are safer long-term.
- Keep `made_for_kids: false` unless the channel is actually for kids — it disables comments and
  notifications, which kills quiz engagement.
- Watch the first few videos end to end. The pronunciation map is the most common fix.
