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
    "long":  {"width": 1920, "height": 1080, "rounds": 10, "stage_seconds": 3.5},
    "short": {"width": 1080, "height": 1920, "rounds": 1,  "stage_seconds": 2.5},
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
    mechanic: str = "ladder"             # ladder | choice | cold | rapid (Shorts: five cries, no hints)
    rapid_rounds: int = 5                # rapid-fire Shorts: sounds per video
    rapid_per_day: int = 1               # rapid-fire Shorts per day (takes one of the four Short slots)
    rapid_reuse_days: int = 90           # rapid-fire may repeat an item after this many days (its own list)
    rounds_per_episode: int = 0          # 0 = format default (10 long / 1 short)
    stage_seconds: float = 0             # 0 = format default
    fps: int = 24

    # Music
    music_provider: str = "procedural"   # procedural | file | elevenlabs | none
    music_bed: str = ""                  # for provider=file: loopable bed
    music_bed_db: float = -26.0          # bed level before ducking
    music_loop_start: float = 0.0        # file provider: seconds where the loop body starts (intro before it)
    music_loop_end: float = 0.0          # file provider: seconds where the loop body ends (0 = whole file)
    music_highpass_hz: float = 0.0       # corrective EQ on the bed
    music_lowpass_hz: float = 0.0
    music_notch_hz: float = 0.0
    music_duck_db: float = -12.0         # extra ducking while voice / quiz sounds play (sidechain)
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

    # Content selection
    shorts_famous_only: bool = True      # Shorts draw from the well-known subset; long-form can go deep
    shorts_cold_open: str = "auto"       # auto (alternate per Short, for A/B) | true | false
    opening_style: str = "question"      # question ("How fast can you name…?") | punch ("Name this Pokémon!")
    title_style: str = "question"        # Shorts title: question | challenge (A/B)
    tts_pace: str = "brisk"              # brisk | quick   (how fast the host talks; A/B)
    tts_energy: str = "host"             # host | showman  (game-show energy level; A/B)
    captions: str = "kinetic"            # kinetic (rounded display font, current word lit as it is spoken) | static
    thumb_style: str = "sil"             # sil (silhouettes + ?) | art (full colour); A/B on long-form (exp `thumb`)
    tts_speed: float = 1.05              # post-synthesis tempo on every line, pitch preserved (1.0 = as spoken)
    tts_speed_long: float = 1.08         # tempo for long read-outs (Pokédex / field-guide hint, the fact)
    short_hints: int = 0                 # Shorts: keep only the last N hints (0 = all)
    short_replay: bool = False           # Shorts: play the sound again after hint 1 (off: cry ×2 → hints → cry on the silhouette → reveal)
    short_outro: str = "spoken"          # Shorts: "drop your score" spoken | caption (on screen during the fact)
    short_cta: str = "score"             # the one closing ask: score ("How many points did you get?") | subscribe
    packs_enabled: list = field(default_factory=lambda: ["pokemon"])   # rotation order for --pack auto
    longs_per_pack_per_week: int = 1     # long-form cadence per pack (the long cron runs daily and skips when met)
    track_used: str = "short"            # which formats never repeat a Pokémon: short | long | all | none

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
        if self.mechanic == "rapid":
            return self.rounds_per_episode or self.rapid_rounds
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
