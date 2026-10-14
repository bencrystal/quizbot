"""Settings: environment variables + config/settings.yaml. Env wins."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Tiny .env loader so launchd/cron runs don't need a shell profile."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(ROOT / ".env")


@dataclass
class Settings:
    root: Path = ROOT
    out_dir: Path = ROOT / "out"
    state_dir: Path = ROOT / "state"
    cache_dir: Path = ROOT / "state" / "cache"
    assets_dir: Path = ROOT / "assets"

    # Video
    width: int = 1920
    height: int = 1080
    fps: int = 30
    countdown_seconds: int = 5
    rounds_per_episode: int = 10
    music_bed: str = ""              # path to a loopable mp3/wav; empty = silence
    music_bed_db: float = -18.0      # how far to duck the bed under narration

    # Providers
    tts_provider: str = "dev"        # dev | openai | elevenlabs | piper
    tts_voice: str = ""
    planner: str = "template"        # template | claude
    claude_model: str = "claude-sonnet-4-5"

    # Upload
    upload: bool = False             # False = render only (safe default)
    youtube_privacy: str = "private" # private | unlisted | public
    youtube_category_id: str = "27"  # 27 = Education
    schedule_hours_ahead: int = 0    # >0 = set publishAt N hours from now
    made_for_kids: bool = False

    # Notifications
    notify_webhook: str = ""         # Slack/Discord-style webhook; empty = log only

    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        cfg_path = ROOT / "config" / "settings.yaml"
        data = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
        s.raw = data or {}
        for k, v in (data or {}).items():
            if hasattr(s, k) and k != "raw":
                setattr(s, k, v)
        # env overrides, QUIZBOT_<UPPER>
        for k in list(vars(s)):
            env = os.environ.get(f"QUIZBOT_{k.upper()}")
            if env is None:
                continue
            cur = getattr(s, k)
            if isinstance(cur, bool):
                setattr(s, k, env.lower() in ("1", "true", "yes"))
            elif isinstance(cur, int):
                setattr(s, k, int(env))
            elif isinstance(cur, float):
                setattr(s, k, float(env))
            elif isinstance(cur, Path):
                setattr(s, k, Path(env))
            else:
                setattr(s, k, env)
        for d in (s.out_dir, s.state_dir, s.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        return s

    def pack_config(self, name: str) -> dict:
        return (self.raw.get("packs") or {}).get(name) or {}
