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
        key = hashlib.sha1(f"{self.provider}|{self.voice}|{style}|{text}".encode()).hexdigest()[:20]
        out = self.cache / f"{key}.wav"
        if out.exists():
            return out
        self.chars_this_run += len(text)
        if self.chars_this_run > self.settings.max_tts_chars_per_run:
            raise TTSBudgetExceeded(f"TTS budget exceeded ({self.chars_this_run} chars > max_tts_chars_per_run)")
        raw = self.cache / f"{key}.raw"
        try:
            getattr(self, f"_{self.provider}")(text, raw)
        except Exception as e:
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
            voice=self.voice or "coral", input=text, response_format="wav",
            speed=float(os.environ.get("QUIZBOT_TTS_SPEED", "1.22")),
            instructions=os.environ.get("QUIZBOT_TTS_STYLE",
                "Game-show host energy: bright, upbeat and quick, with a smile in the voice and a little showman "
                "flair, like announcing the next round to a live audience. Natural and conversational, never cheesy, "
                "never shouting. Keep a steady, flowing pace with no long pauses mid-sentence. "
                "Pronounce Pokémon as 'POH-kee-mon' with the stress on POH. Hyphenated words are single names "
                "written phonetically (capitals mark the stressed syllable), e.g. 'LOO-gee-uh' is one name said "
                "smoothly — never spell letters out, never pause before them.")
                + (" " + getattr(self, "_style", "") if getattr(self, "_style", "") else ""),
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
