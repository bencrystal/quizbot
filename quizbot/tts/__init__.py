"""TTS providers. All return a path to a 48 kHz stereo WAV (normalised by the renderer later).

  dev        - no network: estimates duration and renders a soft tone so the pipeline can be tested
  openai     - OpenAI TTS (OPENAI_API_KEY). Cheap, good quality, no setup.
  elevenlabs - ElevenLabs (ELEVENLABS_API_KEY, QUIZBOT_TTS_VOICE = voice id). Best quality.
  piper      - local, free, offline: `pip install piper-tts` and set QUIZBOT_TTS_VOICE to a .onnx model path.

Every line is cached by (provider, voice, text) hash so re-runs are free.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from ..config import Settings


class TTS:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.tts_provider
        self.voice = settings.tts_voice
        self.cache = settings.cache_dir / "tts" / self.provider
        self.cache.mkdir(parents=True, exist_ok=True)

    def say(self, text: str) -> Path:
        key = hashlib.sha1(f"{self.provider}|{self.voice}|{text}".encode()).hexdigest()[:20]
        out = self.cache / f"{key}.wav"
        if out.exists():
            return out
        raw = self.cache / f"{key}.raw"
        getattr(self, f"_{self.provider}")(text, raw)
        # normalise container/rate so ffmpeg concat never has to think
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], check=True)
        raw.unlink(missing_ok=True)
        return out

    # ---- providers -------------------------------------------------------------
    def _dev(self, text: str, out: Path) -> None:
        # ~2.6 words/sec + a beat; quiet tone so you can hear timing in test renders
        secs = max(0.8, len(text.split()) / 2.6 + 0.3)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"sine=frequency=300:duration={secs:.2f}",
                        "-af", "volume=0.05,afade=t=in:d=0.05,afade=t=out:st={:.2f}:d=0.1".format(secs - 0.1),
                        "-f", "wav", str(out)], check=True)

    def _openai(self, text: str, out: Path) -> None:
        from openai import OpenAI
        client = OpenAI()
        with client.audio.speech.with_streaming_response.create(
            model=os.environ.get("QUIZBOT_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=self.voice or "alloy", input=text, response_format="wav",
        ) as r:
            r.stream_to_file(out)

    def _elevenlabs(self, text: str, out: Path) -> None:
        import requests
        voice = self.voice or "21m00Tcm4TlvDq8ikWAM"
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
            json={"text": text, "model_id": os.environ.get("QUIZBOT_ELEVENLABS_MODEL", "eleven_multilingual_v2")},
            timeout=120,
        )
        r.raise_for_status()
        out.write_bytes(r.content)

    def _piper(self, text: str, out: Path) -> None:
        if not self.voice:
            raise SystemExit("piper needs QUIZBOT_TTS_VOICE=/path/to/voice.onnx")
        subprocess.run(["piper", "--model", self.voice, "--output_file", str(out)],
                       input=text.encode(), check=True)
