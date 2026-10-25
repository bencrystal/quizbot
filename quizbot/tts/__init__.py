"""TTS providers. All return a path to a 48 kHz stereo WAV.

  dev        - no network: silent-ish placeholder tone of the right length, for testing the pipeline
  kokoro     - free, local. Either in-process (`pip install kokoro soundfile`, voice e.g. af_heart / am_michael)
               or via a running kokoro-fastapi server (set QUIZBOT_KOKORO_URL=http://localhost:8880)
  openai     - OpenAI TTS (OPENAI_API_KEY). Cheap, decent.
  elevenlabs - ElevenLabs (ELEVENLABS_API_KEY, QUIZBOT_TTS_VOICE = voice id). Most expressive; voice cloning.
  piper      - local, free: `pip install piper-tts`, QUIZBOT_TTS_VOICE=/path/voice.onnx

Every line is cached by (provider, voice, text) hash so re-renders are free, and a per-run character cap
stops a bug from burning through a paid tier.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from ..config import Settings


class TTSBudgetExceeded(RuntimeError):
    pass


def tighten(wav: Path, gap_keep: float = 0.12, gap_min: float = 0.25, rel_db: float = -30.0, floor_db: float = -45.0) -> None:
    """Cut breaths and dead air inside a spoken line: any stretch quieter than (line level + rel_db, and below
    floor_db) lasting ≥ gap_min s is shortened to gap_keep s with 10 ms crossfades; leading/trailing quiet is
    trimmed to gap_keep too. In place. (ffmpeg's silenceremove misbehaves in 6.x, hence numpy.)"""
    import numpy as np
    import soundfile as sf
    data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
    win = sr // 50                                   # 20 ms
    n = len(data) // win
    if n < 10:
        return
    rms = np.sqrt((data[: n * win].mean(axis=1).reshape(n, win) ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    level = np.percentile(db, 90)
    quiet = db < min(level + rel_db, floor_db)
    keep = np.ones(n, dtype=bool)
    i = 0
    kmin, kkeep = int(gap_min / 0.02), int(gap_keep / 0.02)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= kmin or i == 0 or j == n:
                keep[i + kkeep // 2: j - (kkeep - kkeep // 2)] = False     # keep a little on both sides
                if i == 0:
                    keep[:j - kkeep] = False
                if j == n:
                    keep[i + kkeep:] = False
            i = j
        else:
            i += 1
    if keep.all():
        return
    pieces, fade = [], max(1, sr // 100)
    idx = np.flatnonzero(np.diff(np.concatenate([[0], keep.astype(int), [0]])))
    for a, b in zip(idx[::2], idx[1::2]):
        seg = data[a * win: b * win].copy()
        if len(seg) > 2 * fade:
            seg[:fade] *= np.linspace(0, 1, fade)[:, None]
            seg[-fade:] *= np.linspace(1, 0, fade)[:, None]
        pieces.append(seg)
    if pieces:
        sf.write(str(wav), np.concatenate(pieces), sr, subtype="PCM_16")


class TTS:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.tts_provider
        self.voice = settings.tts_voice
        self.cache = settings.cache_dir / "tts" / self.provider
        self.cache.mkdir(parents=True, exist_ok=True)
        self.chars_this_run = 0

    # Provider-specific spoken-form fixes, applied AFTER the pack's respellings. Kokoro accepts inline
    # phoneme overrides ([word](/phonemes/)), which is the only reliable way to force a pronunciation.
    SPOKEN = {
        "kokoro": {"Pokeymon": "[Pokémon](/pˈoʊkimɑn/)", "Pokeydex": "[Pokédex](/pˈoʊkidɛks/)"},
        "piper": {},
        "openai": {"Pokeymon": "POH-kee-mon", "Pokeydex": "POH-kee-dex"},
    }

    # How Pokémon names reach the OpenAI voice. "inline": respell them in the text (Plusle -> "PLUS-ull"), which
    # can be read as two words or cause a hesitation. "instruct" (default): the text stays as written and the
    # respellings go into the per-line instructions, which keeps the delivery flowing.
    PRON_MODE = os.environ.get("QUIZBOT_PRON_MODE", "instruct")

    def say(self, text: str, style: str = "", notes: list[tuple[str, str]] | None = None) -> Path:
        text = " ".join(text.split())
        if notes:
            style = (style + " " if style else "") + "Pronunciations (capitals = stressed syllable, each is one word " \
                    "said smoothly): " + "; ".join(f"{n} = '{p}'" for n, p in notes) + "."
        self._style = style
        for a, b in self.SPOKEN.get(self.provider, {}).items():
            text = text.replace(a, b)
        direction = self.instructions() if self.provider == "openai" else ""
        # Tempo: a hair faster than spoken keeps the show moving; long read-outs (Pokédex entry, the fact) a
        # little more. Applied with ffmpeg atempo (pitch preserved) so it works the same for every provider.
        speed = float(self.settings.tts_speed_long if len(text) > 90 else self.settings.tts_speed)
        speed = min(max(speed, 0.9), 1.3)
        key = hashlib.sha1(f"{self.provider}|{self.voice}|{direction}|{style}|{speed:.3f}|tight1|{text}".encode()).hexdigest()[:20]
        out = self.cache / f"{key}.wav"
        if out.exists():
            return out
        self.chars_this_run += len(text)
        if self.chars_this_run > self.settings.max_tts_chars_per_run:
            raise TTSBudgetExceeded(f"TTS budget exceeded ({self.chars_this_run} chars > max_tts_chars_per_run)")
        raw = self.cache / f"{key}.raw"
        try:
            for attempt in range(3):                     # streaming TTS calls drop occasionally; retry before any fallback
                try:
                    getattr(self, f"_{self.provider}")(text, raw)
                    break
                except Exception as e:
                    if attempt == 2 or self.provider == "dev":
                        raise
                    print(f"[tts] {self.provider} attempt {attempt + 1} failed ({str(e)[:80]}); retrying")
                    import time
                    time.sleep(3 * (attempt + 1))
        except Exception as e:
            if self.provider == "elevenlabs" and os.environ.get("OPENAI_API_KEY"):
                # paid provider down or out of credits: finish the video on OpenAI and flag it so the
                # experiment ignores this one
                print(f"[tts] elevenlabs failed ({str(e)[:120]}); falling back to openai for this run")
                self.fell_back_to = "openai"
                self.provider, self.voice = "openai", "coral"
                self.settings.tts_provider, self.settings.tts_voice = "openai", "coral"
                self.cache = self.settings.cache_dir / "tts" / "openai"
                self.cache.mkdir(parents=True, exist_ok=True)
                # the text was wrapped for ElevenLabs (<phoneme alphabet="ipa" ph="…">Gabite</phoneme>); OpenAI
                # would read the markup aloud, so hand it the bare names
                text = re.sub(r'<phoneme\b[^>]*>(.*?)</phoneme>', r"\1", text, flags=re.S)
                return self.say(text, style, notes)
            import importlib.util
            if importlib.util.find_spec("kokoro") is None:     # no local fallback installed: fail loudly, never ship a worse voice
                raise
            if self.provider in ("openai", "elevenlabs") and not getattr(self, "_fell_back", False):
                print(f"[tts] {self.provider} failed ({str(e)[:120]}); falling back to kokoro for this run")
                self._fell_back = True
            if not getattr(self, "_fell_back", False):
                raise
            self.provider, self.voice = "kokoro", "am_michael"
            self.cache = self.settings.cache_dir / "tts" / "kokoro"
            self.cache.mkdir(parents=True, exist_ok=True)
            return self.say(text)
        tempo = [] if abs(speed - 1.0) < 0.005 else ["-af", f"atempo={speed:.3f}"]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), *tempo,
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], check=True)
        if out.stat().st_size < 1000:                 # never cache an empty line
            out.unlink(missing_ok=True)
            raise RuntimeError("TTS produced an empty file")
        if self.provider != "dev":
            tighten(out)
        raw.unlink(missing_ok=True)
        return out

    # ---- providers -------------------------------------------------------------
    def _dev(self, text: str, out: Path) -> None:
        secs = max(0.8, len(text.split()) / 2.6 + 0.3)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"sine=frequency=300:duration={secs:.2f}",
                        "-af", "volume=0.05,afade=t=in:d=0.05,afade=t=out:st={:.2f}:d=0.1".format(secs - 0.1),
                        "-f", "wav", str(out)], check=True)

    def _kokoro(self, text: str, out: Path) -> None:
        voice = self.voice or "af_heart"
        if self.settings.kokoro_url:
            import requests
            r = requests.post(f"{self.settings.kokoro_url.rstrip('/')}/v1/audio/speech",
                              json={"model": "kokoro", "voice": voice, "input": text, "response_format": "wav"},
                              timeout=300)
            r.raise_for_status()
            out.write_bytes(r.content)
            return
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
        if not hasattr(self, "_kokoro_pipeline"):
            self._kokoro_pipeline = KPipeline(lang_code=os.environ.get("QUIZBOT_KOKORO_LANG", "a"))
        chunks = [audio for _, _, audio in self._kokoro_pipeline(text, voice=voice)]
        sf.write(str(out), np.concatenate(chunks), 24000, format="WAV")

    PACE = {"brisk": "Keep a steady, flowing pace with no long pauses mid-sentence.",
            "quick": "Talk noticeably quickly — a host keeping a fast show moving — crisp and clear, no long pauses, "
                     "but never rushed into mumbling."}
    ENERGY = {"host": "Game-show host energy: bright, upbeat and quick, with a smile in the voice and a little showman "
                      "flair, like announcing the next round to a live audience. Natural and conversational, never "
                      "cheesy, never shouting.",
              "showman": "Big game-show announcer energy: bright, punchy and animated, clearly having fun, with real "
                         "showman flair and emphasis on the key word of each line — still natural, never cheesy, never "
                         "shouting."}

    def instructions(self) -> str:
        """Voice direction from settings (A/B-able) unless QUIZBOT_TTS_STYLE overrides it wholesale."""
        if os.environ.get("QUIZBOT_TTS_STYLE"):
            return os.environ["QUIZBOT_TTS_STYLE"]
        s = self.settings
        return (self.ENERGY.get(s.tts_energy, self.ENERGY["host"]) + " " + self.PACE.get(s.tts_pace, self.PACE["brisk"]) + " "
                "No audible breaths, sighs or lip smacks between phrases. "
                "Pronounce Pokémon as 'POH-kee-mon' with the stress on POH. Hyphenated words are single names "
                "written phonetically (capitals mark the stressed syllable), e.g. 'LOO-gee-uh' is one name said "
                "smoothly — never spell letters out, never pause before them.")

    def _openai(self, text: str, out: Path) -> None:
        from openai import OpenAI
        client = OpenAI()
        with client.audio.speech.with_streaming_response.create(
            model=os.environ.get("QUIZBOT_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=self.voice or "coral", input=text, response_format="wav",
            speed=float(os.environ.get("QUIZBOT_TTS_SPEED", "1.22")),
            instructions=self.instructions() + (" " + getattr(self, "_style", "") if getattr(self, "_style", "") else ""),
        ) as r:
            r.stream_to_file(out)

    def _eleven_voice_id(self, name: str) -> str:
        import requests
        r = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=30)
        r.raise_for_status()
        for v in r.json()["voices"]:
            if v["name"].split(" - ")[0].strip().lower() == name.lower():
                return v["voice_id"]
        raise RuntimeError(f"ElevenLabs voice '{name}' not found")

    def _elevenlabs(self, text: str, out: Path) -> None:
        import requests
        voice = self.voice or "21m00Tcm4TlvDq8ikWAM"
        if voice.startswith("name:"):
            voice = self._eleven_voice_id(voice[5:])
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
            # eleven_turbo_v2 (English) honours <phoneme alphabet="ipa"> tags; multilingual_v2 ignores them.
            json={"text": text, "model_id": os.environ.get("QUIZBOT_ELEVENLABS_MODEL", "eleven_turbo_v2"),
                  "voice_settings": {"stability": 0.45 if self.settings.tts_energy == "host" else 0.35,
                                     "similarity_boost": 0.8,
                                     "style": 0.35 if self.settings.tts_energy == "host" else 0.6,
                                     "use_speaker_boost": True}},
            timeout=120,
        )
        r.raise_for_status()
        out.write_bytes(r.content)

    def _piper(self, text: str, out: Path) -> None:
        if not self.voice:
            raise SystemExit("piper needs QUIZBOT_TTS_VOICE=/path/to/voice.onnx")
        subprocess.run(["piper", "--model", self.voice, "--output_file", str(out)],
                       input=text.encode(), check=True)
