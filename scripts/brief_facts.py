"""Gather everything the morning brief needs into one JSON (stdout). Read-only; no secrets needed beyond `gh`.

  python scripts/brief_facts.py > /tmp/facts.json

Covers the last ~26 h by default (--hours): episode/rapid/failure issue comments, workflow runs, commits,
experiment state (decided, log, standings), the dashboard data.json (views, subs, IG, budget, comments,
per-theme fitness), yesterday's brief (so the task can say what changed) and the open-decisions register.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "bencrystal/quizbot"
DATA = "https://raw.githubusercontent.com/bencrystal/portfolio_website/quizbot-data/data.json"
BRIEF = "https://raw.githubusercontent.com/bencrystal/portfolio_website/quizbot-data/brief.json"


def gh(path: str):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None


def fetch(url: str):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=26)
    a = ap.parse_args()
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=a.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    facts: dict = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "since": since}

    def comments(issue: int):
        rows = gh(f"repos/{REPO}/issues/{issue}/comments?since={since}&per_page=100") or []
        return [{"at": c["created_at"], "body": c["body"][:1200]} for c in rows]
    facts["episode_log"] = comments(2)
    facts["rapid_log"] = comments(4)
    facts["failures"] = comments(1)
    runs = gh(f"repos/{REPO}/actions/runs?per_page=60") or {}
    facts["runs"] = [{"at": r["created_at"], "workflow": r["name"], "event": r["event"], "conclusion": r["conclusion"]}
                     for r in runs.get("workflow_runs", []) if r["created_at"] >= since]
    log = subprocess.run(["git", "-C", str(ROOT), "log", f"--since={since}", "--format=%h|%an|%ad|%s", "--date=short"],
                         capture_output=True, text=True).stdout.strip().splitlines()
    facts["commits"] = [dict(zip(("sha", "author", "date", "subject"), l.split("|", 3))) for l in log]
    prs = gh(f"repos/{REPO}/pulls?state=open") or []
    facts["open_prs"] = [{"number": p["number"], "title": p["title"], "created": p["created_at"], "url": p["html_url"]} for p in prs]
    try:
        facts["experiments"] = json.loads((ROOT / "state" / "experiments.json").read_text())
    except Exception:
        facts["experiments"] = None
    for name in ("themes", "social"):
        try:
            facts[name] = json.loads((ROOT / "state" / f"{name}.json").read_text())
        except Exception:
            facts[name] = None
    try:
        import yaml
        facts["open_decisions"] = yaml.safe_load((ROOT / "docs" / "brief" / "open_decisions.yaml").read_text())
    except Exception:
        facts["open_decisions"] = None
    d = fetch(DATA)
    if d:
        vids = sorted(d.get("videos", []), key=lambda v: v["published"], reverse=True)
        facts["dashboard"] = {
            "generated": d.get("generated"), "analytics_through": (d.get("ypp") or {}).get("analytics_through"),
            "channel": {k: d["channel"].get(k) for k in ("subscribers", "views", "videos")},
            "daily": d["channel"].get("daily", [])[-10:],
            "platforms": d.get("platform_summary"), "budget": d.get("budget"), "ypp": d.get("ypp"),
            "recent_videos": [{k: v.get(k) for k in ("published", "format", "theme", "title", "views", "views7d", "avp", "likes", "comments", "subs", "tags")} for v in vids[:40]],
            "comments": [c for c in d.get("comments", []) if "Drop your score" not in c.get("text", "")][:40],
            "experiment_results": (d.get("experiments") or {}).get("state", {}).get("results"),
        }
    facts["previous_brief"] = fetch(BRIEF)
    json.dump(facts, sys.stdout, indent=1, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
