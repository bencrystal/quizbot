# quizbot — "Play It By Ear" (automated audio-quiz YouTube channel)

Owner: Ben Crystal (benjamincrystal8@gmail.com). Fully hands-off pipeline: GitHub Actions renders and uploads,
a weekly Claude task supervises (see SUPERVISOR.md). Ben watches output and gives format feedback; he does not
run anything by hand.

## What it makes
Audio quizzes: play a sound → hints → reveal. Two formats from the same code:
- **Shorts** (9:16, one round, ~40 s): `How fast can you name this X?` → sound → hints → reveal. 3 per day.
- **Rapid-fire Shorts** (mechanic `rapid`): five sounds, ~3 s each, name only, no hints, ~35 s. Two a day (14:00 and
  20:00 UTC slots; themes include a random Mix, Ash's Pokémon, Kanto gym leaders); its own no-repeat list (`fmt=rapid`, reusable after `rapid_reuse_days`), its own GitHub issue
  ("Rapid-fire log"), tag `rapid`, format `rapid` in analytics; excluded from the Shorts experiments.
- **Long form** (16:9, 10 rounds, easy → hard, ~7 min): one per pack per week.
Series ("packs") share everything except the data. Live: `pokemon` (cries). Paused 2026-10-26: `birds` (xeno-canto calls; a tenth of the reach and a different audience). Plan: a second
channel for birds later (Ben's call), not back on this one — the pack and its workflow inputs stay intact.
Planned: video-game sounds, music (0.1 s → 0.5 s → 3 s excerpt ladder).

## Architecture (one repo, packs isolated)
- `quizbot/packs/<name>.py` — a pack: `themes()`, `candidates(theme)` → `QuizItem`s (audio, answer, hints,
  reveal image, fact), `display_name`, `noun`, `pronunciations` (name → respelling for the TTS). Nothing else
  knows pack specifics. Hints redundant with the theme are dropped inside the pack (gen theme → no region hint).
- `quizbot/planner.py` — picks theme/items, writes title/description/intro/outro (template; Claude optional).
- `quizbot/render/` — timeline → frames (Pillow) → ffmpeg; `scene.py` is the look. Final mix: music bed with
  sidechain ducking, two-pass loudnorm (never one-pass: it pumps).
- `quizbot/tts/` — OpenAI `gpt-4o-mini-tts` (voice `coral`, game-show-host instructions). Pronunciations go in
  the per-line *instructions*, not respelled in the text (respelled text caused pauses and "plus all").
  Pokémon names come from `assets/pokemon_pronunciations.json` (synced from the Pokémon LP Wiki).
- `quizbot/upload/` — YouTube Data API; adds each video to `"<display_name> (Shorts|Long Form)"` playlist.
- `quizbot/state.py` — SQLite ledger (cached between Actions runs): episodes, per-format used items.
- `config/settings.yaml` — all knobs; env `QUIZBOT_<KEY>` overrides. `packs_enabled` is the rotation.

## Conventions (what makes series comparable)
- Every upload has tags `pack:<name>` and, for Shorts, `coldopen`/`announce` plus `exp:<id>=<variant>` from
  `config/experiments.yaml` (sequential A/B; one active at a time, variants alternate per Short *within each
  pack* — packs rotate per Short too, so counting across packs locks variant to pack). The nightly analytics job
  judges on average % viewed stratified by pack (`quizbot/experiments.py`: per-pack relative scores averaged,
  winner must lead in every pack), writes the winner to `state/experiments.json` (committed), and the winner
  becomes the baseline. Add experiments to the yaml;
  never edit decisions by hand unless Ben asks.
- Ledger backup: every run pushes `state/quizbot.db` to the `ledger` branch; runs restore from it if the cache
  is empty. Analytics JSON goes to the portfolio repo's `quizbot-data` branch (dashboard at /quizbot).
- Ledger: Shorts never repeat an item (`track_used: short`); long-form draws freely.
- Pokémon Shorts use the famous subset; type-theme long episodes open with a Gen 1-3 Pokémon.
- Public immediately on upload; no scheduled publishAt.
- Thumbnails: long-form gets a custom one (decoy subjects from the theme, never the episode's answers; sil/art A/B).
  Shorts cannot have custom thumbnails outside the Partner Program — YouTube picks a frame at ~25/50/75 %, so the
  reveal must stay in the last ~22 % of a Short (sting → sound on the silhouette → art + name). Keep it that way.
- Rules Ben owns (never change unasked): `youtube_privacy`, `upload`, `max_episodes_per_day`, `made_for_kids`.

## Operations
- `.github/workflows/daily.yml`: runs hourly; `scripts/due.py` decides from the ledger whether a Short (slots
  11/14/17/20/23 UTC; 14 and 20 are rapid-fire), the long-form (from 12:30 UTC; skips packs that already had one this week) or nothing is due.
  GitHub cron drops/delays firings, so missed slots catch up on the next hour. 'none' runs post nothing. `workflow_dispatch` inputs: format, theme, force, cold_open, pack.
  Runs are serialized (`concurrency`); the state cache (`state/`, `out/`) is restored from the latest run.
- Results → GitHub issue "Episode log" (#2); failures with log tail → "Run failures" (#1). Actions logs are
  not readable from Claude sessions; the issues are the source of truth.
- Other workflows: `samples.yml` (voice sampler), `pronunciations.yml` (monthly guide sync),
  `playlists.yml` (file every video into its playlist; supervisor runs it weekly).
- Secrets: `OPENAI_API_KEY`, `YT_CLIENT_SECRET_JSON`, `YT_TOKEN_JSON`, `XC_API_KEY` (xeno-canto), optional
  `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`. Repo vars: `QUIZBOT_TTS_VOICE`, `QUIZBOT_TTS_PROVIDER`.
- Local check before pushing: `python -m quizbot test` (offline fixture pack, needs ffmpeg only).
- Commits: push to `main`; CI picks up the next run. The cloud Claude sandbox cannot reach PokéAPI, OpenAI,
  YouTube or xeno-canto — test data-fetching changes with a forced dispatch and read the issues.

## Adding a pack (checklist)
1. `quizbot/packs/<name>.py` subclassing `Pack`; register in `packs/__init__.py` (`_registry`, `PACK_NAMES`).
2. Themes: include some that make a hint redundant and drop that hint in `_item`.
3. Reveal image RGBA (transparent background → silhouette morph works; photo → circle-mask it and set
   `shape_hint=False`).
4. `pronunciations` for any name the TTS would get wrong. Credits for CC content in `meta["credit"]`
   (planner appends them to the description).
5. Add to `packs_enabled`, add the name to the `pack` input description in `daily.yml`, dispatch one forced
   Short and one long, check the Episode log, watch them.

## Taste notes from Ben (keep)
The verb is **name**, everywhere a viewer sees or hears it: series are "Name That X" (`display_name`; old names
in `former_names` so playlists get renamed, not duplicated), questions "name this X", thumbnails "NAME THE SOUND".
"Guess that X" survives only as a search phrase in descriptions.
Voice natural and upbeat like a game-show host, never cheesy; no countdown ticks or chimes; music is his own
bed (`assets/music/bed.wav`) kept low; captions on; hints as plain statements; reveal clean (no reactions);
engagement over algorithm tricks — one "drop your score" at the end is enough.
