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


def _pluck(f: float, secs: float, bright: float = 1.0) -> np.ndarray:
    """Marimba/kalimba-ish: fundamental + soft 4th harmonic, fast decay."""
    n = int(secs * SR)
    t = np.arange(n) / SR
    tone = np.sin(2 * math.pi * f * t) + 0.25 * bright * np.sin(2 * math.pi * f * 4 * t) * np.exp(-t * 18)
    return tone * np.exp(-t * 5) * 0.5


def procedural_bed(seed: str, out: Path, bars: int = 8) -> Path:
    """Cozy, major-key loop: soft pad chords + a gentle pentatonic pluck pattern. No drums. ~40 s."""
    rng = random.Random(int(hashlib.sha1(seed.encode()).hexdigest(), 16))
    bpm = rng.choice([88, 92, 96, 100])
    key = rng.randint(0, 11)
    prog = rng.choice([[0, 3, 4, 0], [0, 5, 3, 4], [0, 3, 5, 4], [3, 4, 0, 0]])     # bright major progressions
    beat = 60 / bpm
    bar = beat * 4
    total = int(bars * bar * SR)
    mix = np.zeros(total)

    def add(sig, at):
        i = int(at * SR); j = min(total, i + len(sig))
        if i < total:
            mix[i:j] += sig[: j - i]

    penta = [0, 1, 2, 4, 5]      # scale degrees of the major pentatonic
    for b in range(bars):
        deg = prog[b % 4]
        chord = [_note(deg, 3, key), _note(deg + 2, 3, key), _note(deg + 4, 3, key), _note(deg, 4, key)]
        n = int((bar + 0.8) * SR)
        t = np.arange(n) / SR
        pad = np.zeros(n)
        for f in chord:
            for det, amp in ((1.0, 1.0), (1.004, 0.5), (0.996, 0.5)):
                pad += amp * np.sin(2 * math.pi * f * det * t + rng.random() * 6.28)
        env = np.minimum(1, t / 0.6) * np.minimum(1, (n / SR - t) / 0.8)
        add(pad * env / (len(chord) * 3.2), b * bar)
        # pluck pattern: 8th notes, mostly on, wandering through the pentatonic near the chord root
        cur = deg
        for s8 in range(8):
            if rng.random() < 0.7:
                cur = max(deg - 2, min(deg + 9, cur + rng.choice([-2, -1, 0, 1, 1, 2, 3])))
                # snap to pentatonic relative to the key
                step = min(penta, key=lambda p_: abs(((cur - p_) % 7)))
                f = _note(cur - ((cur - step) % 7) + (0 if rng.random() < 0.8 else 7), 5, key)
                add(_pluck(f, 0.7, bright=0.8) * (0.5 if s8 % 2 else 0.7), b * bar + s8 * beat / 2)
        # soft bass note on 1 and 3
        for k in (0, 2):
            add(_pluck(_note(deg, 2, key), 0.9, bright=0.2) * 0.6, b * bar + k * beat)

    k = 10
    mix = np.convolve(mix, np.ones(k) / k, mode="same")
    mix /= max(1e-6, np.abs(mix).max())
    fade = int(0.08 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    left = mix + 0.08 * np.roll(mix, int(0.0004 * SR))
    right = mix + 0.08 * np.roll(mix, -int(0.0004 * SR))
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-6)
    stereo = np.stack([left, right], axis=1) / peak * 0.6
    import soundfile as sf
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


def build_loop(settings: Settings, src: Path, work: Path, need_seconds: float = 900) -> Path:
    """intro (0..loop_start) + body (loop_start..loop_end) repeated with short crossfades, long enough for any
    episode. Corrective EQ: high-pass, low shelf, notch on `music_notch_hz` — all set in settings.yaml."""
    import soundfile as sf
    from scipy.signal import butter, sosfilt, iirnotch, lfilter
    data, sr = sf.read(str(src), dtype="float32")
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    ls, le = settings.music_loop_start, settings.music_loop_end
    if le <= ls:
        ls, le = 0.0, len(data) / sr
    intro = data[: int(ls * sr)]
    body = data[int(ls * sr): int(le * sr)]
    xf = int(0.04 * sr)
    reps = max(1, int(np.ceil((need_seconds - ls) / max(1e-3, le - ls))))
    out = [intro]
    ramp = np.linspace(0, 1, xf)[:, None]
    for i in range(reps):
        seg = body.copy()
        if i > 0 and len(out[-1]) > xf:
            tail = out[-1][-xf:]
            out[-1] = out[-1][:-xf]
            seg[:xf] = tail * (1 - ramp) + seg[:xf] * ramp
        out.append(seg)
    y = np.concatenate(out)
    # corrective EQ
    if settings.music_highpass_hz > 0:
        y = sosfilt(butter(2, settings.music_highpass_hz / (sr / 2), "high", output="sos"), y, axis=0)
    if settings.music_notch_hz > 0:
        b, a = iirnotch(settings.music_notch_hz / (sr / 2), 8)
        y = lfilter(b, a, y, axis=0)
    if settings.music_lowpass_hz > 0:
        y = sosfilt(butter(2, settings.music_lowpass_hz / (sr / 2), "low", output="sos"), y, axis=0)
    y = y / max(1e-6, np.abs(y).max()) * 0.9
    out_path = work / "bed.wav"
    sf.write(str(out_path), y.astype(np.float32), sr)
    return out_path


def bed_for(settings: Settings, slug: str, work: Path) -> Path | None:
    prov = settings.music_provider
    if prov == "none":
        return None
    if prov == "file":
        src = settings.asset("music_bed")
        if not src:
            return None
        return build_loop(settings, src, work)
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
