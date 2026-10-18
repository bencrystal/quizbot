"""Meta (Facebook Page + Instagram) authorisation without a laptop. Runs in Actions (meta_auth.yml).

  python scripts/meta_auth.py url                      → consent link (open, approve, copy the redirect URL)
  python scripts/meta_auth.py exchange <code-or-url>   → long-lived user token → Page token + IG user id
                                                         → state/meta_token.json
Needs META_APP_ID / META_APP_SECRET. The Page token derived from a long-lived user token does not expire;
if posting ever fails with error 190, run both steps again.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

GRAPH = "https://graph.facebook.com/v23.0"
REDIRECT = "https://github.com/bencrystal/quizbot/oauth-callback"
SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_manage_metadata",
          "pages_read_user_content", "read_insights", "instagram_basic", "instagram_content_publish",
          "instagram_manage_insights", "instagram_manage_comments", "business_management"]
TOKEN = Path(__file__).resolve().parent.parent / "state" / "meta_token.json"


def main() -> None:
    app_id, secret = os.environ["META_APP_ID"].strip(), os.environ["META_APP_SECRET"].strip()
    if not app_id.isdigit() or len(secret) < 20:
        raise SystemExit(f"META_APP_ID looks wrong ({len(app_id)} chars, digits={app_id.isdigit()}) or "
                         f"META_APP_SECRET too short ({len(secret)} chars). Re-paste both secrets.")
    print(f"app id ...{app_id[-4:]} ({len(app_id)} digits); secret {len(secret)} chars", file=sys.stderr)
    cmd = sys.argv[1]
    if cmd == "url":
        q = {"client_id": app_id, "redirect_uri": REDIRECT, "scope": ",".join(SCOPES), "response_type": "code",
             "state": "quizbot"}
        print("https://www.facebook.com/v23.0/dialog/oauth?" + urlencode(q))
        return
    arg = sys.argv[2].strip()
    code = parse_qs(urlparse(arg).query).get("code", [arg])[0] if arg.startswith("http") else arg
    r = requests.get(f"{GRAPH}/oauth/access_token", params={"client_id": app_id, "redirect_uri": REDIRECT,
                                                            "client_secret": secret, "code": code}, timeout=30).json()
    if "error" in r:
        raise SystemExit(f"code exchange failed: {r['error']}")
    short = r["access_token"]
    r = requests.get(f"{GRAPH}/oauth/access_token", params={"grant_type": "fb_exchange_token", "client_id": app_id,
                                                            "client_secret": secret, "fb_exchange_token": short},
                     timeout=30).json()
    if "error" in r:
        raise SystemExit(f"long-lived exchange failed: {r['error']}")
    user_token = r["access_token"]
    pages = requests.get(f"{GRAPH}/me/accounts", params={"fields": "id,name,access_token,instagram_business_account",
                                                        "access_token": user_token}, timeout=30).json()
    if "error" in pages or not pages.get("data"):
        raise SystemExit(f"no pages visible: {pages}")
    want = os.environ.get("META_PAGE_NAME", "").lower()
    page = next((p for p in pages["data"] if want and p["name"].lower() == want), pages["data"][0])
    ig = (page.get("instagram_business_account") or {}).get("id")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps({"page_id": page["id"], "page_name": page["name"], "page_token": page["access_token"],
                                 "ig_user_id": ig, "user_token": user_token}))
    print(f"page: {page['name']} ({page['id']}); instagram account: {'linked' if ig else 'NOT linked'}; "
          f"pages seen: {[p['name'] for p in pages['data']]}")


if __name__ == "__main__":
    main()
