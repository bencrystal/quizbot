"""SQLite ledger: which items have been used, which episodes exist, upload status.
This is what makes unattended runs safe to re-run — nothing is done twice."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS used_items (
    pack TEXT NOT NULL,
    item_id TEXT NOT NULL,
    episode_slug TEXT NOT NULL,
    used_at REAL NOT NULL,
    PRIMARY KEY (pack, item_id)
);
CREATE TABLE IF NOT EXISTS used_by_format (
    pack TEXT NOT NULL,
    fmt TEXT NOT NULL,
    item_id TEXT NOT NULL,
    episode_slug TEXT NOT NULL,
    used_at REAL NOT NULL,
    PRIMARY KEY (pack, fmt, item_id)
);
CREATE TABLE IF NOT EXISTS posts (
    episode_slug TEXT NOT NULL,
    platform TEXT NOT NULL,            -- instagram | facebook | tiktok
    post_id TEXT,
    error TEXT,
    posted_at REAL NOT NULL,
    PRIMARY KEY (episode_slug, platform)
);
CREATE TABLE IF NOT EXISTS episodes (
    slug TEXT PRIMARY KEY,
    pack TEXT NOT NULL,
    title TEXT NOT NULL,
    theme TEXT,
    status TEXT NOT NULL,           -- planned | rendered | uploaded | failed
    video_path TEXT,
    thumbnail_path TEXT,
    youtube_id TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    meta TEXT
);
"""


class State:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- items -------------------------------------------------------------
    def used_ids(self, pack: str, fmt: str, within_days: float | None = None) -> set[str]:
        """Items already featured in this format (Shorts, long-form and rapid-fire keep separate lists).
        within_days limits it to recent uses (rapid-fire may repeat an item after a quiet spell)."""
        if within_days:
            rows = self.conn.execute("SELECT item_id FROM used_by_format WHERE pack=? AND fmt=? AND used_at>=?",
                                     (pack, fmt, time.time() - within_days * 86400)).fetchall()
        else:
            rows = self.conn.execute("SELECT item_id FROM used_by_format WHERE pack=? AND fmt=?", (pack, fmt)).fetchall()
        return {r["item_id"] for r in rows}

    def mark_used(self, pack: str, fmt: str, item_ids: list[str], slug: str) -> None:
        now = time.time()
        self.conn.executemany(
            "INSERT OR REPLACE INTO used_by_format(pack,fmt,item_id,episode_slug,used_at) VALUES (?,?,?,?,?)",
            [(pack, fmt, i, slug, now) for i in item_ids],
        )
        self.conn.commit()

    def release(self, pack: str, slug: str) -> None:
        """Un-use items if an episode failed so they can be picked again."""
        self.conn.execute("DELETE FROM used_by_format WHERE pack=? AND episode_slug=?", (pack, slug))
        self.conn.commit()

    # ---- cross-posts -------------------------------------------------------
    def posts(self, slug: str) -> set[str]:
        """Platforms this episode has been successfully posted to."""
        rows = self.conn.execute("SELECT platform FROM posts WHERE episode_slug=? AND post_id IS NOT NULL", (slug,)).fetchall()
        return {r["platform"] for r in rows}

    def record_post(self, slug: str, platform: str, post_id: str | None, error: str | None = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO posts(episode_slug,platform,post_id,error,posted_at) VALUES (?,?,?,?,?)",
                          (slug, platform, post_id, error, time.time()))
        self.conn.commit()

    def all_posts(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM posts WHERE post_id IS NOT NULL").fetchall()

    def count_episodes(self, fmt: str, pack: str | None = None, since: float = 0.0) -> int:
        q = "SELECT COUNT(*) FROM episodes WHERE slug LIKE ? AND status!='failed' AND created_at>=?"
        args: list = [f"%-{fmt}-%", since]
        if pack:
            q += " AND pack=?"
            args.append(pack)
        return self.conn.execute(q, args).fetchone()[0]

    # ---- episodes ----------------------------------------------------------
    def upsert_episode(self, slug: str, pack: str, title: str, theme: str, status: str, **kw) -> None:
        now = time.time()
        row = self.conn.execute("SELECT slug FROM episodes WHERE slug=?", (slug,)).fetchone()
        meta_given = "meta" in kw
        meta = json.dumps(kw.pop("meta", {}))
        if row:
            sets = ", ".join(f"{k}=?" for k in kw) + (", " if kw else "") + "status=?, updated_at=?"
            vals = [*kw.values(), status, now]
            if meta_given:                       # keep existing meta (e.g. the manual flag) unless given
                sets += ", meta=?"
                vals.append(meta)
            self.conn.execute(f"UPDATE episodes SET {sets} WHERE slug=?", (*vals, slug))
        else:
            cols = ["slug", "pack", "title", "theme", "status", "created_at", "updated_at", "meta", *kw]
            vals = [slug, pack, title, theme, status, now, now, meta, *kw.values()]
            self.conn.execute(f"INSERT INTO episodes({','.join(cols)}) VALUES ({','.join('?'*len(vals))})", vals)
        self.conn.commit()

    def episodes(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute("SELECT * FROM episodes WHERE status=? ORDER BY created_at", (status,)).fetchall()
        return self.conn.execute("SELECT * FROM episodes ORDER BY created_at").fetchall()

    def episodes_today(self) -> int:
        start = time.time() - (time.time() % 86400)   # UTC midnight is fine for a cap
        return self.conn.execute("SELECT COUNT(*) FROM episodes WHERE created_at>=? AND status!='failed' "
                                 "AND COALESCE(meta,'') NOT LIKE '%\"manual\": true%'", (start,)).fetchone()[0]

    def recent_themes(self, pack: str, n: int = 10, fmt: str | None = None) -> list[str]:
        """Themes of the pack's last n episodes; with fmt, only episodes of that format (slug is <pack>-<fmt>-…)."""
        if fmt:
            rows = self.conn.execute(
                "SELECT theme FROM episodes WHERE pack=? AND slug LIKE ? ORDER BY created_at DESC LIMIT ?",
                (pack, f"{pack}-{fmt}-%", n)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT theme FROM episodes WHERE pack=? ORDER BY created_at DESC LIMIT ?", (pack, n)
            ).fetchall()
        return [r["theme"] for r in rows if r["theme"]]
