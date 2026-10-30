"""Render the same quiz script in several OpenAI voices so Ben can pick one. Output: out/voices/<voice>.mp3"""
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quizbot.config import Settings
from quizbot.tts import TTS
from quizbot.packs import get_pack

VOICES = (os.environ.get("VOICES") or "ash,coral,nova,echo,sage,ballad").split(",")
LINES = [
    ("Welcome to Name That Pokémon. 10 rounds, easy to hard. Three points if you get it from the sound alone, then one less for every hint you need. Today's theme: Generation 2.", ""),
    ("Round 1.", ""),
    ("It's a Psychic and Flying type.", ""),
    ("Last hint, from the Pokédex: It is said to be the guardian of the seas. It is rumored to have been seen on the night of a storm.", ""),
    ("It's Lugia.", "This is the reveal: a little brighter and more delighted, like sharing the answer with a friend."),
    ("Lugia, the Diving Pokémon.", ""),
    ("Round 2.", ""),
    ("It's an Electric type.", ""),
    ("It's Plusle.", "This is the reveal: a little brighter and more delighted, like sharing the answer with a friend."),
    ("Plusle, the Cheering Pokémon.", ""),
    ("Round 3.", ""),
    ("It's Arceus.", "This is the reveal: a little brighter and more delighted, like sharing the answer with a friend."),
    ("Arceus, the Alpha Pokémon.", ""),
    ("That's 10 rounds, 30 points possible. Drop your score in the comments, and subscribe for next week's quiz.", ""),
]
s = Settings.load(); s.tts_provider = "openai"
pack = get_pack("pokemon", s)
out = s.out_dir / "voices"; out.mkdir(parents=True, exist_ok=True)
for n_, dex in (("Lugia", 249), ("Arceus", 493), ("Plusle", 311)):
    pack.spoken_replacements[n_] = pack.pronunciations[n_] = pack.guide.get(dex, n_)
    if pack.ipa_guide.get(dex):
        pack.ipa[n_] = pack.ipa_guide[dex]
BLIND = os.environ.get("BLIND") == "1"          # name outputs sample-1.mp3 … and write the key to a separate file
key = []
def eleven_voice_id(name: str) -> str:
    """'eleven:Brian' → voice id, resolved from the account's voice list (premade voices included)."""
    import requests
    r = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=30)
    r.raise_for_status()
    for v in r.json()["voices"]:
        if v["name"].split(" - ")[0].strip().lower() == name.lower():
            return v["voice_id"]
    raise SystemExit(f"ElevenLabs voice '{name}' not found; have: {[v['name'] for v in r.json()['voices']]}")


for voice in VOICES:
  eleven = voice.startswith("eleven:")
  for variant in (("-inline",) if eleven else ("-inline", "-instruct", "-plain")):
    s.tts_provider = "elevenlabs" if eleven else "openai"
    s.tts_voice = eleven_voice_id(voice.split(":", 1)[1]) if eleven else voice
    tts = TTS(s)
    if variant != "-inline":
        tts.SPOKEN = {}            # no "POH-kee-mon" text respelling either
    parts = []
    for text, style in LINES:
        if variant == "-inline" and eleven:
            parts.append(tts.say(pack.with_phonemes(text), style))
        elif variant == "-inline":
            parts.append(tts.say(pack.spoken(text), style))
        elif variant == "-instruct":
            parts.append(tts.say(text, style, notes=pack.pronunciation_notes(text)))
        else:
            parts.append(tts.say(text, style))
    v = voice.replace(":", "-") + variant
    if BLIND:
        key.append((f"sample-{len(key) + 1}", v))
        v = key[-1][0]
    lst = out / f"{v}.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\nfile '{(out / 'gap.wav').resolve()}'\n" for p in parts))
    if not (out / "gap.wav").exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.6", (out / "gap.wav")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-b:a", "160k", out / f"{v}.mp3"], check=True)
    print("voice", v, "done")

if BLIND:
    import random
    random.shuffle(key)
    (out / "KEY.txt").write_text("".join(f"{a}.mp3 = {b}\n" for a, b in sorted(key)))
    # shuffle the numbering so order carries no information
    tmp = {b: (out / f"{a}.mp3").read_bytes() for a, b in key}
    for i, (a, b) in enumerate(key, 1):
        (out / f"sample-{i}.mp3").write_bytes(tmp[b])
    (out / "KEY.txt").write_text("".join(f"sample-{i}.mp3 = {b}\n" for i, (a, b) in enumerate(key, 1)))
