# Supervisor playbook

This file is the standing brief for the scheduled Claude task that watches the channel. The pipeline itself runs
on GitHub Actions (`.github/workflows/daily.yml`); the supervisor never renders video. It reads, decides, commits,
and reports. Every run is a fresh session: start by cloning this repo and reading this file.

## Each run

1. `gh run list --workflow episodes --limit 10 --json conclusion,createdAt,displayTitle,databaseId,url`
   Note any run since the last supervisor report that is not `success`.
2. For each failed run: `gh run view <id> --log-failed | tail -80`. Classify:
   - **YouTube auth** ("invalid_grant", "Token has been expired or revoked") → cannot self-heal; tell Ben to
     re-run `python -m quizbot auth` locally and update the `YT_TOKEN_JSON` secret. Highest priority.
   - **YouTube quota** ("quotaExceeded") → uploads retry automatically next run; just note it.
   - **PokéAPI / network** (timeouts, 5xx) → transient; re-dispatch once: `gh workflow run episodes -f format=short`.
   - **TTS budget** ("TTS budget exceeded") → a bug or a runaway prompt; inspect `episode.json` in the run's
     artifact, fix the cause if it is in the planner, otherwise report.
   - **Code error** (traceback in quizbot/) → read the traceback, fix it with a minimal commit on `main`
     (run `python -m quizbot test` locally first; it needs only ffmpeg), re-dispatch, report what changed.
3. Download the latest successful run's artifact (`gh run download <id>`) and read `episode.json`:
   - Check `hints` and `fact` text for anything that spoils the answer early (an answer name inside hint 1 or 2).
     If found, fix the hint rule in `quizbot/packs/pokemon.py`, not the single episode.
   - Add any proper noun that a TTS would plausibly mangle to `PRONUNCIATIONS` in `quizbot/packs/pokemon.py`
     (commit; do not guess wildly — only names with a well-known pronunciation).
4. Check the ledger for health: how many episodes are `rendered` but not `uploaded` (stuck)? Any day with 0 runs?
5. Post one short report (SendUserMessage): runs since last time (✅/❌ counts), what you fixed, what needs Ben.
   If nothing happened and nothing is wrong, say so in one line.

## Rules

- Never change `youtube_privacy`, `upload`, `max_episodes_per_day` or `made_for_kids`. Those are Ben's.
- Never add API keys or tokens to the repo.
- Commits are small and explained; push straight to `main` only for fixes of the kinds listed above.
  Anything larger (new pack, new mechanic, layout change) → open a PR and describe it in the report.
- Do not re-dispatch more than once per run; the daily cap exists so you cannot flood the channel.
- You cannot listen to the audio. Do not claim a voice or mix "sounds good"; report what you verified.

## Monthly (first run of the month)

- Report the used-items count per pack vs. pool size; if a theme is >80% used, add themes in `settings.yaml`
  (more generations/types) and mention it.
- Suggest one concrete format improvement based on the last month's titles/themes. Do not implement it unasked.
