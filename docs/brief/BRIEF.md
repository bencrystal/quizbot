# The morning brief

Written every day at 07:35 ET by the Claude scheduled task "quizbot morning brief", after the 02:40 ET analytics
job. It replaces Ben asking "any new updates?": one panel at the top of bencrystal.me/quizbot.

## Procedure (the task follows this exactly)
1. `add_repo` bencrystal/quizbot (push) and bencrystal/portfolio_website (push); clone quizbot to /home/claude/quizbot.
2. `pip install -q pyyaml` if needed; `python scripts/brief_facts.py > /tmp/facts.json` — everything you need is in
   that file. Do not browse further unless a fact is missing; never run the pipeline, never edit code or settings.
3. Read it all, then write `/tmp/brief.json` in the schema below. Honesty rules:
   - Say what happened, not what you did. No praise, no filler, no restating yesterday unless it changed.
   - Numbers come from facts.json; never estimate. Analytics lags 2–3 days — say so when it matters.
   - A test that is leading is "leading", never "winning", until `experiments.decided` has it.
   - Decisions: copy every `status: open` item from `open_decisions` verbatim (same id). Add a new decision only
     when something genuinely needs Ben (a format change, money, a trade-off between his taste rules and data).
     New decisions also get appended to docs/brief/open_decisions.yaml in a commit on main (that is the only
     commit the task makes). Never invent decisions to fill the panel; an empty list is a fine outcome.
   - Ideas: at most 3, concrete, each one sentence with the evidence that prompted it. Skip the section if none.
   - Watch: tests close to a decision, budget, anything that failed once.
   - `changes`: what is different from `previous_brief` (new decision, test decided, run failures started/stopped).
4. Publish: in a fresh clone of portfolio_website, `git checkout quizbot-data`, copy /tmp/brief.json to brief.json,
   commit as "brief: <date>" (user.email benjamincrystal8@gmail.com) and push. Nothing else on that branch changes.
5. Finish with the headline as your one-line summary. Do not message Ben separately.

## Schema (brief.json)
```json
{
  "date": "2026-11-17", "written_at": "2026-11-17T12:35:00Z",
  "headline": "one sentence: the single most important thing today",
  "happened": ["3–6 bullets: uploads, failures, decisions the judge made, notable numbers (with the day they refer to)"],
  "decisions": [{"id": "…", "title": "…", "why": "…", "options": ["…"], "recommendation": "…", "how": "Reply in chat / Decisions panel"}],
  "ideas": ["≤3 bullets"],
  "watch": ["2–5 bullets"],
  "changes": ["what differs from yesterday's brief"]
}
```
