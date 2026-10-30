"""File every video on the channel into '<quiz> (Shorts)' / '<quiz> (Long Form)' playlists. Idempotent.

Pack is read from a `pack:<name>` tag (new uploads) or guessed from the title; format from '#shorts' in the title.
Run in CI (playlists.yml) where the YouTube token lives.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from googleapiclient.discovery import build  # noqa: E402

from quizbot.config import Settings  # noqa: E402
from quizbot.packs import PACK_NAMES, get_pack  # noqa: E402
from quizbot.upload import _creds, add_to_playlist  # noqa: E402

s = Settings.load()
yt = build("youtube", "v3", credentials=_creds(s, interactive=False))
packs = {n: get_pack(n, s) for n in PACK_NAMES}

# every upload on the channel
ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
ids, page = [], None
while True:
    r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page).execute()
    ids += [i["contentDetails"]["videoId"] for i in r["items"]]
    page = r.get("nextPageToken")
    if not page:
        break

# what is already filed where
filed: dict[str, set[str]] = {}
for pl in yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", []):
    page = None
    while True:
        it = yt.playlistItems().list(part="contentDetails", playlistId=pl["id"], maxResults=50, pageToken=page).execute()
        for i in it["items"]:
            filed.setdefault(i["contentDetails"]["videoId"], set()).add(pl["snippet"]["title"])
        page = it.get("nextPageToken")
        if not page:
            break

moved = 0
for k in range(0, len(ids), 50):
    for v in yt.videos().list(part="snippet", id=",".join(ids[k:k + 50])).execute()["items"]:
        sn = v["snippet"]
        tags = sn.get("tags") or []
        pack = next((t.split(":", 1)[1] for t in tags if t.startswith("pack:")), None)
        if pack is None:
            pack = next((n for n, p in packs.items()
                         if p.display_name.lower() in sn["title"].lower() or p.noun.lower() in sn["title"].lower()), None)
        if pack not in packs:
            print(f"skip {v['id']}: cannot tell the pack from '{sn['title']}'")
            continue
        short = "#shorts" in sn["title"].lower()
        fmt = 'Shorts' if short else 'Long Form'
        title = f"{packs[pack].display_name} ({fmt})"
        former = [f"{n} ({fmt})" for n in packs[pack].former_names]
        if filed.get(v["id"], set()) & {title, *former}:     # already in it (under its current or former name)
            continue
        try:
            add_to_playlist(yt, v["id"], title, former)
            moved += 1
        except Exception as e:
            print(f"could not file {v['id']} ({sn['title']}): {str(e)[:120]}")
print(f"filed {moved} video(s); {len(ids)} on the channel")
