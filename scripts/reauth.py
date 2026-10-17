"""YouTube OAuth without a local machine. Runs in GitHub Actions (reauth.yml), where the client secret lives.

  python scripts/reauth.py url            → prints the consent URL (open it, approve, you land on the redirect page)
  python scripts/reauth.py exchange <code-or-redirect-url>
                                          → trades the code for a token; writes state/youtube_token.json

Scopes: upload + channel management (playlists) + Analytics read (dashboard).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
SECRET = ROOT / "config" / "client_secret.json"
TOKEN = ROOT / "state" / "youtube_token.json"
REDIRECT = "https://github.com/bencrystal/quizbot/oauth-callback"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]


def flow():
    from google_auth_oauthlib.flow import Flow
    f = Flow.from_client_secrets_file(str(SECRET), scopes=SCOPES, redirect_uri=REDIRECT)
    return f


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "url":
        f = flow()
        url, _ = f.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        print(url)
    elif cmd == "exchange":
        arg = sys.argv[2].strip()
        code = parse_qs(urlparse(arg).query).get("code", [arg])[0] if arg.startswith("http") else arg
        f = flow()
        f.fetch_token(code=code)
        c = f.credentials
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(json.dumps({"token": c.token, "refresh_token": c.refresh_token, "token_uri": c.token_uri,
                                     "client_id": c.client_id, "client_secret": c.client_secret,
                                     "scopes": list(c.scopes or SCOPES)}))
        print(f"token written; scopes: {c.scopes}; refresh_token present: {bool(c.refresh_token)}")
    else:
        raise SystemExit(__doc__)
