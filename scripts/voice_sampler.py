"""Render the same quiz script in several OpenAI voices so Ben can pick one. Output: out/voices/<voice>.mp3"""
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quizbot.config import Settings
from quizbot.tts import TTS
from quizbot.packs import get_pack

VOICES = (os.environ.get("VOICES") or "ash,coral,nova,echo,sage,ballad").split(",")
LINES = [
    ("Welcome to Guess That Pokémon. 10 rounds, easy to hard. Three points if you get it from the sound alone, then one less for every hint you need. Today's theme: Generation 2.", ""),
    ("Round 1.", ""),
    ("It's a Psychic and Flying type.", ""),
    ("Last hint, from the Pokédex: It is said to be the guardian of the seas. It is rumored to have been seen on the night of a storm.", ""),
    ("It's Lugia.", "This is the reveal: a little brighter and more delighted, like sharing the answer with a friend."),
    ("Lugia, the Diving Pokémon.", ""),
    ("Round 2.", ""),
    ("It's Arceus.", "This is the reveal: a little brighter and more delighted, like sharing the answer with a friend."),
    ("Arceus, the Alpha Pokémon.", ""),
    ("That's 10 rounds, 30 points possible. Drop your score in the comments, and subscribe for next week's quiz.", ""),
]
s = Settings.load(); s.tts_provider = "openai"
pack = get_pack("pokemon", s)
out = s.out_dir / "voices"; out.mkdir(parents=True, exist_ok=True)
pack.spoken_replacements.update({"Lugia": pack.guide.get(249, "Lugia"), "Arceus": pack.guide.get(493, "Arceus")})
for v in VOICES:
  for variant in ("", "-plain"):
    s.tts_voice = v
    tts = TTS(s)
    if variant:
        tts.SPOKEN = {}            # no "POH-kee-mon" respelling either
    parts = []
    for text, style in LINES:
        parts.append(tts.say(text if variant else pack.spoken(text), style))
    v = v + variant
    lst = out / f"{v}.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\nfile '{(out / 'gap.wav').resolve()}'\n" for p in parts))
    if not (out / "gap.wav").exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.6", (out / "gap.wav")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-b:a", "160k", out / f"{v}.mp3"], check=True)
    print("voice", v, "done")
