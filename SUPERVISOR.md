# Supervisor playbook

Standing brief for the weekly scheduled Claude task that watches and grows the channel. The pipeline runs on
GitHub Actions (`.github/workflows/daily.yml`, workflow name `episodes`); the supervisor never renders video. It
reads, decides, commits, and reports. Every run is a fresh session: clone this repo, read CLAUDE.md, then this.

Sources of truth (Actions logs are not readable from Claude sessions):
- Issue #1 "Run failures" — every failed run with a log tail.   Issue #2 "Episode log" — every render/upload,
  cross-post lines (📱) and `[meta]`/`[tts]` lines.
- `https://raw.githubusercontent.com/bencrystal/portfolio_website/quizbot-data/data.json` — the nightly analytics
  export (schema at the bottom of `scripts/analytics_export.py`): per-video retention, experiments, platforms,
  audience, YPP progress, ElevenLabs budget. The same data renders at bencrystal.com/quizbot.
- `state/experiments.json` (committed) — experiment decisions.  `ledger` branch — SQLite ledger backup.

## Each run

1. **Failures.** Read new comments on issue #1 since the last report. Classify:
   - YouTube auth (`invalid_grant`, "expired or revoked") → cannot self-heal; tell Ben to dispatch
     `reauth.yml` and paste the redirect URL into a chat. Highest priority.
   - YouTube quota / `uploadLimitExceeded` → the video stays in `out/` and is retried next run; just count them.
   - Meta error 190 (token) → tell Ben to dispatch `meta_auth.yml` (`url` step, then `exchange` with the code).
   - `[tts] elevenlabs failed` → check `budget.elevenlabs` in data.json; credits out → tell Ben (free tier
     resets monthly; Starter is $5). Those Shorts are tagged `exp:voice=fallback` and excluded from the test.
   - xeno-canto / PokéAPI / Wikipedia timeouts → transient; re-dispatch once (`gh workflow run episodes -f format=short`).
   - Traceback in `quizbot/` → fix with a minimal commit on `main` (`python -m quizbot test` first; needs ffmpeg),
     re-dispatch once, report what changed.
2. **Content quality.** Read the last week's Episode log entries. For each pack, spot-check hints and facts for
   spoilers (the answer or an alias inside a hint), broken redaction ("this bird the size of this bird"), or
   repeated items in Shorts. Fix the *rule* in the pack, never the single episode.
3. **Visual QA.** `python -m quizbot test && python scripts/contact_sheet.py` renders the offline fixture pack
   and tiles the frames viewers actually see (one `.sheet.png` per video under `out/`). Look at one Short sheet
   and the long sheet with the Read tool. You are checking for bugs, not taste: text overlapping or clipped,
   a name wider than the frame, captions unreadable against the art, hint dots when there are only two hints,
   the reveal art off-centre. Fix the rule in `quizbot/render/scene.py`, re-render, look again, then commit.
   Visual *style* changes (font sizes, positions, colours) are not for this step — they go through the
   experiment queue like everything else (step 4), one at a time, so retention decides.
4. **Experiments.** From `data.json → experiments`:
   - Report each running experiment's rows (n, % viewed, views/video) and any decision made this week.
   - If the sequential queue is down to one undecided experiment, **add two more** to `config/experiments.yaml`
     (ideas backlog below). Keep variants to settings overrides that exist in `quizbot/config.py`; add a knob
     there if needed. Never edit `state/experiments.json` by hand.
   - A decided winner that looks wrong (tiny views, n padded by fallbacks) → report it; Ben decides reverts.
5. **Platforms.** `platform_summary` — report followers and views/post for YouTube vs Instagram vs Facebook.
   Shorts missing a 📱 line in the Episode log for two days running → cross-posting is broken; check #1.
6. **Audience.** Note the top traffic source (Shorts feed vs search vs external) and whether retention differs
   by pack. If one pack's Shorts retention is >20% below the other's for ≥10 videos, say so with numbers.
7. **Playlists.** Dispatch `playlists.yml` (idempotent; files every video). Report what it moved.
8. **Ledger health.** `ledger.used` vs pool size per pack; episodes `rendered` but not `uploaded` (stuck); any
   day with 0 Shorts in the Episode log.
9. **Monetisation.** Quote `ypp` progress in one line (subs / 1,000; long-form hours / 4,000; Shorts views / 10M)
   and the week-over-week change.
10. **Report** (SendUserMessage, short): runs ✅/❌, what you fixed, experiment status, platform numbers, what
   needs Ben. Nothing wrong → one line.

## Growth loop (the point of the job)

The channel grows by (a) more series, (b) better retention, (c) more surfaces. Each week, pick the biggest lever
and act within the rules below:
- **Retention**: experiments are the mechanism. Keep the queue full. Backlog of ideas, in rough priority:
  reveal hold length; "drop your score" wording vs none; hint order (fact first vs region first); caption style;
  Shorts length (one hint fewer when retention drops after hint 2); long-form opener (cold sound vs intro);
  music bed level −24 vs −28 dB; title emoji; thumbnail for long-form (question vs answer art).
- **Series**: a pack with ≥20 Shorts and retention ≥ channel average deserves more slots; a pack 20% below for
  20+ videos deserves a format fix before more volume. New packs (game sounds, music) are PRs, not direct commits.
- **Surfaces**: TikTok once Ben's developer app is approved (see Pending in the report until then).
- **Volume**: never raise `max_episodes_per_day` or the cron schedule; propose it with numbers.

## Rules

- Never change `youtube_privacy`, `upload`, `max_episodes_per_day`, `made_for_kids`, or the cron schedule.
- Never add API keys or tokens to the repo. Never commit `state/quizbot.db`.
- Push to `main` only for fixes and experiment additions; anything bigger (new pack, mechanic, layout) → PR.
- At most one re-dispatch per run. You cannot listen to audio — report what you verified, not how it sounds.
- Commits end with the attribution lines in CLAUDE.md.

## Monthly (first run of the month)

- `pronunciations.yml` ran? (Pokémon guide sync.) Items used vs pool per theme; >80% used → add themes.
- ElevenLabs: credits used vs limit; if the voice experiment decided for `liam`, remind Ben that Starter ($5) is
  the commercial licence — free tier needs the "Voice: ElevenLabs" credit the pipeline already adds.
- One concrete format proposal with numbers from data.json. Do not implement it unasked.
