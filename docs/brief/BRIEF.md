# The morning brief

Written every day at 07:35 ET by the Claude scheduled task "quizbot morning brief", after the 02:40 ET analytics
job. It replaces Ben asking "any new updates?": one panel at the top of bencrystal.me/quizbot.

## Procedure (the task follows this exactly)
1. `add_repo` bencrystal/quizbot (push) and bencrystal/portfolio_website (push); clone quizbot to /home/claude/quizbot.
2. `pip install -q pyyaml` if needed; `python scripts/brief_facts.py > /tmp/facts.json` — everything you need is in
   that file. Do not browse further unless a fact is missing; never run the pipeline, never edit code or settings.
3. Read it all, then write `/tmp/brief.json` in the schema below. The panel is read in under a minute, so:
   - **Brevity is the format.** Headline ≤ 14 words. Every bullet is one line, ≤ 16 words, one fact each, no
     parentheses, no semicolons, no chains of numbers. If a bullet needs a second clause, it is two bullets or
     it is not worth saying. Section caps: happened ≤ 4, ideas ≤ 2, watch ≤ 3, changes ≤ 3.
   - **Numbers go in `metrics` and `tests`, never in prose.** `metrics` is 4–6 figures (subs, views on the
     latest Analytics day, uploads, failures, IG views, credits). `tests` has one row per running or
     just-decided test: the two leading arms with their score and n, and a status
     (`early` under min_n · `leading` · `tie` · `decided`). Do not repeat a figure that is in either.
   - Say what happened, not what you did. No praise, no filler, nothing unchanged from yesterday.
   - Numbers come from facts.json; never estimate. A missing figure is omitted, not explained.
   - A test that is leading is "leading", never "winning", until `experiments.decided` has it.
   - Decisions: copy every `status: open` item from `open_decisions` (same id), with `why` cut to one sentence.
     Add a new decision only when something genuinely needs Ben (a format change, money, his taste rules vs
     data); append it to docs/brief/open_decisions.yaml in a commit on main (the only commit the task makes).
     Never invent decisions; an empty list is a fine outcome. An open PR awaiting his merge is a decision.
   - Ideas: each one sentence — the idea, then "because" and the evidence. Skip the section if none.
4. Publish: in a fresh clone of portfolio_website, `git checkout quizbot-data`, copy /tmp/brief.json to brief.json,
   commit as "brief: <date>" (user.email benjamincrystal8@gmail.com) and push. Nothing else on that branch changes.
5. Finish with the headline as your one-line summary. Do not message Ben separately.

## Schema (brief.json)
```json
{
  "date": "2026-11-20", "written_at": "2026-11-20T12:35:00Z",
  "headline": "≤ 14 words: the single most important thing today",
  "metrics": [{"label": "Subscribers", "value": "55", "delta": "+0"}, {"label": "Views · Nov 15", "value": "3,966"}],
  "happened": ["≤ 4 one-line bullets"],
  "decisions": [{"id": "…", "title": "…", "why": "one sentence", "options": ["…"], "recommendation": "…", "how": "Reply in chat"}],
  "tests": [{"name": "Cry", "a": "original 73.4 (16)", "b": "modern 71.8 (20)", "status": "decided", "note": "modern kept on a tie"}],
  "ideas": ["≤ 2 one-line bullets"],
  "watch": ["≤ 3 one-line bullets"],
  "changes": ["≤ 3 one-line bullets"]
}
```
`metrics[].delta` and `tests[].note` are optional; keep `note` under 8 words.
