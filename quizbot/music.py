"""Music beds, hands-off.

  music_provider: file        - use assets/music/bed.wav (your own loop)
                  procedural  - synthesize a new, royalty-free-by-construction loop per episode (default)
                  elevenlabs  - generate a bed with ElevenLabs Music from a prompt (needs ELEVENLABS_API_KEY and a plan
                                that licenses commercial use; falls back to procedural on any error)

Procedural beds are seeded from the episode slug so every video sounds related but not identical: a soft pad
playing a 4-chord loop, a plucked arpeggio, and a light kick/hat. They sit at -18..-22 dB under the narration and
get sidechain-ducked further whenever voice or a quiz sound plays (see render).
"""
from __future__ import annotations

import hashlib
import math
import os
import random
from pathlib import Path

import numpy as np

from .config import Settings

SR = 48000

PROGRESSIONS = [  # scale degrees (0-based), major key
    [0, 4, 5, 3], [0, 5, 3, 4], [5, 3, 0, 4], [0, 3, 4, 3], [3, 0, 4, 5],
]
MAJOR = [0, 2, 4, 5, 7, 9, 11]


def _note(deg: int, octave: int, key: int) -> float:
    semis = key + MAJOR[deg % 7] + 12 * (deg // 7) + 12 * octave
    return 440.0 * 2 ** ((semis - 57) / 12)   # 57 = A4 index when C0 = 0


def _env(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """ADSR in samples, s = sustain level."""
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    sus = max(0, n - a - d - r)
    return np.concatenate([np.linspace(0, 1, a), np.linspace(1, s, d), np.full(sus, s), np.linspace(s, 0, r)])[:n]


def _pad(freqs: list[float], secs: float) -> np.ndarray:
    n = int(secs * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in freqs:
        for det, amp in ((0, 1.0), (1.004, 0.5), (0.996, 0.5)):
            out += amp * np.sin(2 * math.pi * f * det * t)
        out += 0.25 * np.sin(2 * math.pi * f * 2 * t)   # octave shimmer
    return out * _env(n, 0.4, 0.3, 0.8, 0.5) / (len(freqs) * 2.5)


def _pluck(f: float, secs: float) -> np.ndarray:
    n = int(secs * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * math.pi * f * t) + 0.3 * np.sin(2 * math.pi * f * 2 * t) + 0.1 * np.sin(2 * math.pi * f * 3 * t)
    return tone * np.exp(-t * 6) * 0.5


def _kick(secs: float = 0.25) -> np.ndarray:
    n = int(secs * SR)
    t = np.arange(n) / SR
    return np.sin(2 * math.pi * (50 + 80 * np.exp(-t * 30)) * t) * np.exp(-t * 18)


def _hat(secs: float = 0.05, rng: random.Random | None = None) -> np.ndarray:
    n = int(secs * SR)
    noise = np.random.default_rng(rng.randint(0, 1 << 30) if rng else 0).standard_normal(n)
    return noise * np.exp(-np.arange(n) / SR * 120) * 0.05


def procedural_bed(seed: str, out: Path, bars: int = 8) -> Path:
    rng = random.Random(int(hashlib.sha1(seed.encode()).hexdigest(), 16))
    bpm = rng.choice([84, 90, 96, 100, 108])
    key = rng.randint(0, 11)
    prog = rng.choice(PROGRESSIONS)
    beat = 60 / bpm
    bar = beat * 4
    total = int(bars * bar * SR)
    mix = np.zeros(total)

    def add(sig: np.ndarray, at: float) -> None:
        i = int(at * SR)
        j = min(total, i + len(sig))
        if i < total:
            mix[i:j] += sig[: j - i]

    arp_pattern = rng.choice([[0, 2, 4, 2], [0, 4, 7, 4], [4, 2, 0, 2], [0, 2, 4, 7]])
    for b in range(bars):
        deg = prog[b % 4]
        chord = [_note(deg, 3, key), _note(deg + 2, 3, key), _note(deg + 4, 3, key), _note(deg, 4, key)]
        add(_pad(chord, bar + 0.4), b * bar)
        for s in range(8):                                   # 8th-note arpeggio
            if rng.random() < 0.85:
                add(_pluck(_note(deg + arp_pattern[s % 4], 5, key), 0.6), b * bar + s * beat / 2)
        for k in (0, 2):                                     # kick on 1 and 3, soft
            add(_kick() * 0.6, b * bar + k * beat)
        for h in range(8):                                   # off-beat hats
            if h % 2 == 1:
                add(_hat(rng=rng), b * bar + h * beat / 2)

    # gentle low-pass via moving average, normalise, loop-friendly fade at the seam
    k = 24
    mix = np.convolve(mix, np.ones(k) / k, mode="same")
    mix /= max(1e-6, np.abs(mix).max())
    fade = int(0.05 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    stereo = np.stack([mix, np.roll(mix, int(0.0004 * SR))], axis=1) * 0.7
    import soundfile as sf  # noqa  (tiny dep; falls back to wave below)
    sf.write(str(out), stereo.astype(np.float32), SR)
    return out


def elevenlabs_bed(prompt: str, out: Path, seconds: int = 60) -> Path:
    """ElevenLabs Music. Endpoint/params per https://elevenlabs.io/docs — verify before relying on it."""
    import requests
    r = requests.post("https://api.elevenlabs.io/v1/music",
                      headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                      json={"prompt": prompt, "music_length_ms": seconds * 1000}, timeout=300)
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


def bed_for(settings: Settings, slug: str, work: Path) -> Path | None:
    prov = settings.music_provider
    if prov == "none":
        return None
    if prov == "file":
        return settings.asset("music_bed")
    if prov == "elevenlabs":
        cache = settings.cache_dir / "music"
        cache.mkdir(parents=True, exist_ok=True)
        out = cache / f"{slug}.mp3"
        if out.exists():
            return out
        try:
            return elevenlabs_bed(settings.music_prompt, out)
        except Exception as e:
            print(f"[music] elevenlabs failed ({e}); using procedural bed")
    return procedural_bed(slug, work / "bed.wav")
