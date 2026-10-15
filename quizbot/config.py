"""Settings: environment variables + config/settings.yaml. Env (QUIZBOT_<KEY>) wins."""
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

FORMATS = {
    "long":  {"width": 1920, "height": 1080, "rounds": 10, "stage_seconds": 4},
    "short": {"width": 1080, "height": 1920, "rounds": 1,  "stage_seconds": 3},
}


@dataclass
class Settings:
    root: Path = ROOT
    out_dir: Path = ROOT / "out"        # point these at an external drive if you like
    state_dir: Path = ROOT / "state"
    cache_dir: Path = ROOT / "state" / "cache"
    assets_dir: Path = ROOT / "assets"

    # Game
    format: str = "long"                 # long | short  (overridden by --format)
    mechanic: str = "ladder"             # ladder | choice | cold
    rounds_per_episode: int = 0          # 0 = format default (10 long / 1 short)
    stage_seconds: int = 0               # 0 = format default
    fps: int = 30

    # Music
    music_provider: str = "procedural"   # procedural | file | elevenlabs | none
    music_bed: str = ""                  # for provider=file: loopable bed
    music_bed_db: float = -18.0          # bed level before ducking
    music_duck_db: float = -14.0         # extra ducking while voice / quiz sounds play (sidechain)
    music_prompt: str = ("calm lo-fi hip hop instrumental, soft electric piano, light drums, warm, "
                         "loopable, no vocals, background music for a quiz show")
    sting: str = ""                      # plays on reveal
    tick: str = ""                       # one-shot per second of thinking time

    # Providers
    tts_provider: str = "dev"            # dev | kokoro | openai | elevenlabs | piper
    tts_voice: str = ""
    kokoro_url: str = ""                 # optional kokoro-fastapi base URL; empty = in-process
    planner: str = "template"            # template | claude
    claude_model: str = "claude-sonnet-4-5"

    # Safety rails for unattended runs
    max_episodes_per_day: int = 2
    max_tts_chars_per_run: int = 20000

    # Upload
    upload: bool = False
    youtube_privacy: str = "private"     # private | unlisted | public
    youtube_category_id: str = "27"      # Education
    publish_time: str = ""               # "HH:MM" local; episodes are scheduled to the next such time (≥1h out)
    schedule_hours_ahead: int = 0        # alternative: fixed offset. publish_time wins if both set.
    made_for_kids: bool = False
    channel_name: str = "Audio Quiz"

    notify_webhook: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        cfg_path = ROOT / "config" / "settings.yaml"
        data = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
        s.raw = data or {}
        fields = {f for f in vars(s) if f != "raw"}
        for k, v in (data or {}).items():
            if k in fields:
                setattr(s, k, Path(v) if isinstance(getattr(s, k), Path) else v)
        for k in fields:
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

    # ---- derived ---------------------------------------------------------------
    @property
    def width(self) -> int:
        return FORMATS[self.format]["width"]

    @property
    def height(self) -> int:
        return FORMATS[self.format]["height"]

    @property
    def rounds(self) -> int:
        return self.rounds_per_episode or FORMATS[self.format]["rounds"]

    @property
    def stage(self) -> int:
        return self.stage_seconds or FORMATS[self.format]["stage_seconds"]

    def asset(self, name: str) -> Path | None:
        v = getattr(self, name)
        if not v:
            return None
        p = Path(v)
        if not p.is_absolute():
            p = self.root / p
        return p if p.exists() else None

    def pack_config(self, name: str) -> dict:
        return (self.raw.get("packs") or {}).get(name) or {}
