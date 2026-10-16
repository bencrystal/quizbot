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
import subprocess
from pathlib import Path

from ..config import Settings


class TTSBudgetExceeded(RuntimeError):
    pass


class TTS:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.tts_provider
        self.voice = settings.tts_voice
        self.cache = settings.cache_dir / "tts" / self.provider
        self.cache.mkdir(parents=True, exist_ok=True)
        self.chars_this_run = 0

    # Provider-specific spoken-form fixes. On-screen text is never touched.
    SPOKEN = {
        "kokoro": {"Pokémon": "Pokeymon", "Pokédex": "Pokeydex"},
        "piper": {"Pokémon": "Pokeymon", "Pokédex": "Pokeydex"},
    }

    def say(self, text: str) -> Path:
        text = " ".join(text.split())
        for a, b in self.SPOKEN.get(self.provider, {}).items():
            text = text.replace(a, b)
        key = hashlib.sha1(f"{self.provider}|{self.voice}|{text}".encode()).hexdigest()[:20]
        out = self.cache / f"{key}.wav"
        if out.exists():
            return out
        self.chars_this_run += len(text)
        if self.chars_this_run > self.settings.max_tts_chars_per_run:
            raise TTSBudgetExceeded(f"TTS budget exceeded ({self.chars_this_run} chars > max_tts_chars_per_run)")
        raw = self.cache / f"{key}.raw"
        getattr(self, f"_{self.provider}")(text, raw)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], check=True)
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

    def _openai(self, text: str, out: Path) -> None:
        from openai import OpenAI
        client = OpenAI()
        with client.audio.speech.with_streaming_response.create(
            model=os.environ.get("QUIZBOT_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=self.voice or "ash", input=text, response_format="wav",
            instructions=os.environ.get("QUIZBOT_TTS_STYLE",
                "Warm, friendly quiz-show host. Clear and natural, lightly playful, never shouty. "
                "Pronounce Pokémon as 'POH-kay-mon'. Short pauses between sentences."),
        ) as r:
            r.stream_to_file(out)

    def _elevenlabs(self, text: str, out: Path) -> None:
        import requests
        voice = self.voice or "21m00Tcm4TlvDq8ikWAM"
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
            json={"text": text, "model_id": os.environ.get("QUIZBOT_ELEVENLABS_MODEL", "eleven_multilingual_v2"),
                  "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35}},
            timeout=120,
        )
        r.raise_for_status()
        out.write_bytes(r.content)

    def _piper(self, text: str, out: Path) -> None:
        if not self.voice:
            raise SystemExit("piper needs QUIZBOT_TTS_VOICE=/path/to/voice.onnx")
        subprocess.run(["piper", "--model", self.voice, "--output_file", str(out)],
                       input=text.encode(), check=True)
