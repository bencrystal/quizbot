"""Render the same quiz script in several OpenAI voices so Ben can pick one. Output: out/voices/<voice>.mp3"""
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quizbot.config import Settings
from quizbot.tts import TTS
from quizbot.packs import get_pack

VOICES = (os.environ.get("VOICES") or "ash,coral,nova,echo,sage,ballad").split(",")
LINES = [
    ("How fast can you name this Pokémon?", ""),
    ("It's a Normal type.", ""),
    ("It was first seen in the Sinnoh region.", ""),
    ("Last hint, from the Pokédex: It is the heavenly fount from which pours the light that shines across Hisui.", ""),
    ("It's Arceus.", "This is the reveal: a little brighter and more delighted, like sharing the answer with a friend."),
    ("Arceus, the Alpha Pokémon.", ""),
    ("How many points did you get?", ""),
]
s = Settings.load(); s.tts_provider = "openai"
pack = get_pack("pokemon", s)
out = s.out_dir / "voices"; out.mkdir(parents=True, exist_ok=True)
for v in VOICES:
    s.tts_voice = v
    tts = TTS(s)
    parts = []
    for text, style in LINES:
        parts.append(tts.say(pack.spoken(text), style))
    lst = out / f"{v}.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\nfile '{(out / 'gap.wav').resolve()}'\n" for p in parts))
    if not (out / "gap.wav").exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.6", (out / "gap.wav")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-b:a", "160k", out / f"{v}.mp3"], check=True)
    print("voice", v, "done")
