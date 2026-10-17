"""Assemble an Episode into an MP4.

Each round is ONE timeline: a list of audio parts (narration, the quiz sound, silence) laid end to end,
with events (hint lands, thinking starts, shape morph, reveal) at known offsets. Frames are drawn from
that state at `fps` and piped raw into ffmpeg, so motion is per-frame and cheap.

Ladder (default):  sound ×2 → think (4 pts) → hint 1 + tint → think (3) → hint 2 (Pokédex) → think (2)
                   → "last chance" + silhouette morph → think (1) → sting → reveal → reaction → sound → fact
No ticks: the bed swells in the silences (sidechain release) and a soft chime closes each thinking stage.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import Settings
from ..models import Episode, QuizItem, RenderResult
from ..music import bed_for
from ..packs import Pack
from ..tts import TTS
from .scene import Scene, Event, ease

VID = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1"]
AUD = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
SR = 48000


def run(cmd):
    subprocess.run(cmd, check=True)


def ff(*args):
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


class Timeline:
    """Audio parts + events for one video segment (a round, or an intro/outro card)."""

    def __init__(self):
        self.parts: list[tuple[Path | None, float]] = []   # (file or None=silence, seconds)
        self.events: list[Event] = []
        self.t = 0.0

    def add(self, path: Path | None, secs: float, kind: str | None = None, **data) -> float:
        start = self.t
        if kind:
            self.events.append(Event(start, kind, secs, data))
        self.parts.append((path, secs))
        self.t += secs
        return start

    def mark(self, kind: str, dur: float = 0.0, **data):
        self.events.append(Event(self.t, kind, dur, data))


class Renderer:
    def __init__(self, settings: Settings, pack: Pack, tts: TTS):
        self.s = settings
        self.pack = pack
        self.tts = tts
        self.scene = Scene(settings, settings.channel_name)
        self._dur: dict[str, float] = {}
        self._norm: dict[str, Path] = {}
        self._env: dict[str, np.ndarray] = {}

    # ---- audio helpers --------------------------------------------------------
    TARGET_RMS_DB = -20.0   # every voice line and quiz sound is brought to the same loudness

    def norm(self, src: Path, work: Path) -> Path:
        k = str(src)
        if k not in self._norm:
            tmp = work / f"t{len(self._norm):03d}.wav"
            run(ff("-i", str(src), "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(tmp)))
            data, _ = sf.read(str(tmp), dtype="float32")
            mono = data.mean(axis=1)
            # RMS over the louder 60% of 20 ms windows (ignores leading/trailing silence)
            win = SR // 50
            n = max(1, len(mono) // win)
            w = np.sqrt((mono[: n * win].reshape(n, win) ** 2).mean(axis=1))
            w = np.sort(w)[int(n * 0.4):]
            rms = float(np.sqrt((w ** 2).mean())) if len(w) else 1e-4
            gain = min(10 ** (self.TARGET_RMS_DB / 20) / max(rms, 1e-5), 0.97 / max(1e-5, float(np.abs(data).max())))
            out = work / f"n{len(self._norm):03d}.wav"
            sf.write(str(out), (data * gain).astype(np.float32), SR)
            tmp.unlink(missing_ok=True)
            self._norm[k] = out
        return self._norm[k]

    def dur(self, p: Path) -> float:
        k = str(p)
        if k not in self._dur:
            self._dur[k] = duration(p)
        return self._dur[k]

    def envelope(self, p: Path, work: Path) -> np.ndarray:
        """RMS per 20 ms of the (normalised) file, scaled 0..1."""
        k = str(p)
        if k not in self._env:
            data, _ = sf.read(str(self.norm(p, work)), dtype="float32")
            mono = data.mean(axis=1) if data.ndim > 1 else data
            win = SR // 50
            n = max(1, len(mono) // win)
            rms = np.sqrt((mono[: n * win].reshape(n, win) ** 2).mean(axis=1))
            self._env[k] = rms / (rms.max() + 1e-6)
        return self._env[k]

    def synth(self, name: str, freqs: list[float], secs: float, vol: float, work: Path) -> Path:
        out = work / f"{name}.wav"
        if not out.exists():
            t = np.arange(int(secs * SR)) / SR
            sig = sum(np.sin(2 * np.pi * f * t) * np.exp(-t * 5) for f in freqs) / len(freqs)
            n = len(t)
            env = np.ones(n)
            fi, fo = int(0.001 * SR), int(0.015 * SR)          # 1 ms in, 15 ms out — no transient click
            env[:fi] = np.linspace(0, 1, fi)
            env[-fo:] = np.linspace(1, 0, fo)
            sig *= vol * env
            sf.write(str(out), np.stack([sig, sig], axis=1).astype(np.float32), SR)
        return out

    def chime(self, work: Path) -> Path:
        return self.synth("chime", [880, 1318.5], 0.6, 0.09, work)

    def sting(self, work: Path) -> Path:
        custom = self.s.asset("sting")
        if custom:
            return custom
        return self.synth("sting", [523.25, 659.25, 783.99, 1046.5], 0.9, 0.3, work)

    def say(self, text: str, style: str = "") -> Path:
        if self.tts.provider == "openai" and self.tts.PRON_MODE == "instruct":
            return self.tts.say(text, style, notes=self.pack.pronunciation_notes(text))
        return self.tts.say(self.pack.spoken(text), style)

    @staticmethod
    def loudnorm_linear(audio: Path) -> str:
        """Measure once, then return a loudnorm filter that applies a fixed gain (linear=true)."""
        import json as _json
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af",
                            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True)
        blob = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
        try:
            m = _json.loads(blob)
            return ("loudnorm=I=-16:TP=-1.5:LRA=11:linear=true"
                    f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                    f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}")
        except Exception as e:
            print(f"[mix] loudnorm measure failed ({e}); using one-pass")
            return "loudnorm=I=-16:TP=-1.5:LRA=11"

    # ---- timeline builders ----------------------------------------------------
    def build_round(self, i: int, total: int, it: QuizItem, ep: Episode, rng: random.Random, work: Path) -> Timeline:
        T = Timeline()
        S = ep.stage_seconds
        short = ep.format == "short"
        snd = self.norm(it.prompt_audio, work)
        snd_d = self.dur(snd)
        n_hints = len(it.hints) if ep.mechanic == "ladder" else 0
        pts = n_hints + 1

        def say_part(text, kind=None, style="", **data):
            p = self.say(text, style)
            return T.add(p, self.dur(p), kind, **data)

        T.mark("status", text="Listen")
        cold = bool(ep.meta.get("cold_open"))
        if short and cold:       # hook: the sound itself is the first thing you hear
            T.add(None, 0.15)
            T.add(snd, snd_d, "sound")
            T.add(None, 0.3)
        if short:
            T.mark("caption", text=self.pack.opening_line)
            say_part(self.pack.opening_line)
            T.mark("caption", text="")
        else:
            say_part(self.pack.question_line.format(n=i))
        T.add(None, 0.3)
        T.add(snd, snd_d, "sound")
        if not (short and cold):   # cold-open shorts already had it once; keep it to two plays
            T.add(None, 0.4)
            T.add(snd, snd_d, "sound")
        T.mark("points", value=pts)
        T.mark("stage", index=0, count=n_hints + 1)

        if ep.mechanic == "ladder":
            T.add(None, 0.3)
            T.mark("status", text="Your guess?")
            T.add(None, S, "think")
            T.add(None, 0.25)
            for h_i, hint in enumerate(it.hints):
                pts -= 1
                last = h_i == len(it.hints) - 1
                T.mark("hint", text=hint, index=h_i)
                if h_i == 0:
                    T.mark("tint")
                if last and it.shape_hint and it.reveal_image:
                    T.mark("shape", dur=1.2)
                say_part(hint)
                T.mark("points", value=pts)
                T.mark("stage", index=h_i + 1, count=n_hints + 1)
                if h_i == 0:
                    T.add(None, 0.2)
                    T.add(snd, snd_d, "sound")
                T.add(None, 0.25)
                T.add(None, S, "think")
                T.add(None, 0.25)
        elif ep.mechanic == "choice":
            spoken = ", ".join(f"{'ABCD'[k]}, {c}" for k, c in enumerate(it.choices[:-1])) + f", or {'ABCD'[len(it.choices)-1]}, {it.choices[-1]}"
            T.mark("choices", items=it.choices)
            say_part(self.pack.choice_line.format(choices=spoken))
            T.add(None, S + 1, "think")
            T.add(None, 0.25)
        else:
            T.mark("status", text="Your guess?")
            T.add(None, S + 1, "think")
            T.add(None, 0.25)

        T.mark("reveal")
        T.add(self.sting(work), 0.9)
        T.mark("caption", text=self.pack.reveal_line.format(answer=it.answer))
        say_part(self.pack.reveal_line.format(answer=it.answer),
                 style="This is the reveal: a little brighter and more delighted, like sharing the answer with a friend.")
        reaction = ep.banter.get(i) or (rng.choice(self.pack.reactions) if total > 1 and self.pack.reactions else "")
        if reaction:
            T.add(None, 0.15)
            say_part(reaction)
        T.add(None, 0.3)
        T.add(snd, snd_d, "sound")
        T.add(None, 0.3)
        say_part(it.fact)
        if short:
            T.add(None, 0.3)
            say_part(ep.outro)
            T.add(None, 1.5)
        else:
            T.add(None, 0.9)
        return T

    def build_card(self, text: str, title: str, sub: str, foot: str, lead: float, tail: float) -> Timeline:
        T = Timeline()
        T.mark("card", title=title, sub=sub, foot=foot)
        T.add(None, lead)
        p = self.say(text)
        T.add(p, self.dur(p))
        T.add(None, tail)
        return T

    # ---- state at time t ------------------------------------------------------
    def state(self, T: Timeline, t: float, it: QuizItem | None, i: int, total: int, work: Path) -> dict:
        st = dict(t=t, card=None, header=f"ROUND {i} / {total}" if total > 1 else self.s.channel_name.upper(),
                  points=None, points_age=99.0, tint=it.tint if it else None, tint_p=0.0, stage=0, stages=1, arc=0.0,
                  art=it.reveal_image if it else None, shape_p=0.0, reveal_p=0.0, wave=None, think=None,
                  status="", caption="", hints=[], answer=it.answer if it else "", subtitle=it.subtitle if it else "",
                  fact=it.fact if it else "", entry=(it.meta.get("entry", "") if it else ""), choices=None)
        wave = np.zeros(28)
        for ev in T.events:
            if ev.at > t:
                break
            age = t - ev.at
            if ev.kind == "card":
                st["card"] = (ev.data["title"], ev.data["sub"], ev.data["foot"])
            elif ev.kind == "status":
                st["status"] = ev.data["text"]
            elif ev.kind == "caption":
                st["caption"] = ev.data["text"]
            elif ev.kind == "points":
                st["points"], st["points_age"] = ev.data["value"], age
            elif ev.kind == "tint":
                st["tint_p"] = ease(age / 0.8)
            elif ev.kind == "hint":
                st["hints"].append((ev.data["text"], age))
                st["status"] = ""
            elif ev.kind == "choices":
                st["choices"] = ev.data["items"]
            elif ev.kind == "stage":
                st["stage"], st["stages"] = ev.data["index"], ev.data["count"]
            elif ev.kind == "think":
                st["think"] = min(1.0, age / ev.dur) if age < ev.dur else None
            elif ev.kind == "shape":
                st["shape_p"] = min(1.0, age / ev.dur)
            elif ev.kind == "reveal":
                st["reveal_p"] = min(1.0, age)
                st["status"] = ""
                st["points"] = None
        # waveform: scrolling envelope of whichever sound is playing (or decaying silence)
        for ev in T.events:
            if ev.kind == "sound" and ev.at - 0.6 <= t <= ev.at + ev.dur + 0.6:
                env = self.envelope(it.prompt_audio, work)
                for k in range(28):
                    tt = t - ev.at - abs(k - 13.5) * 0.03     # radiates outward from the centre
                    idx = int(tt * 50)
                    if 0 <= idx < len(env):
                        wave[k] = max(wave[k], env[idx])
        playing = any(ev.kind == "sound" and ev.at - 0.2 <= t <= ev.at + ev.dur + 0.7 for ev in T.events)
        if st["reveal_p"] == 0 and playing:
            st["wave"] = wave
        # progress arc around the disc: each thinking stage sweeps one segment; between stages it holds
        done, cur = 0, 0.0
        for ev in T.events:
            if ev.kind != "think" or ev.at > t:
                continue
            if t >= ev.at + ev.dur:
                done += 1
            else:
                cur = (t - ev.at) / ev.dur
        st["arc"] = min(1.0, (done + cur) / st["stages"]) if st["points"] is not None else 0.0
        if st["choices"]:
            st["hints"] = [(f"{'ABCD'[k]}   {c}", 9) for k, c in enumerate(st["choices"])]
        return st

    # ---- encode one timeline --------------------------------------------------
    def encode(self, T: Timeline, it: QuizItem | None, i: int, total: int, work: Path, out: Path) -> Path:
        # audio
        lst = work / f"{out.stem}.txt"
        files = []
        for p, secs in T.parts:
            if p is None:
                sil = work / f"sil-{secs:.3f}.wav"
                if not sil.exists():
                    run(ff("-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo", "-t", f"{secs:.3f}", "-c:a", "pcm_s16le", str(sil)))
                files.append(sil)
            else:
                files.append(self.norm(p, work))
        lst.write_text("".join(f"file '{f.resolve()}'\n" for f in files))
        audio = work / f"{out.stem}.wav"
        run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "pcm_s16le", str(audio)))
        total_secs = self.dur(audio)

        # video: raw frames → ffmpeg
        fps = self.s.fps
        n_frames = int(total_secs * fps)
        cmd = ff("-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.s.width}x{self.s.height}", "-r", str(fps),
                 "-i", "-", "-i", str(audio), "-shortest", *VID, *AUD, str(out))
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in range(n_frames):
            st = self.state(T, f / fps, it, i, total, work)
            proc.stdin.write(self.scene.draw(st).tobytes())
        proc.stdin.close()
        proc.wait()
        if proc.returncode:
            raise RuntimeError(f"ffmpeg failed encoding {out}")
        return out

    # ---- main -----------------------------------------------------------------
    def render(self, ep: Episode) -> RenderResult:
        work = self.s.out_dir / ep.slug / "work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        rng = random.Random(ep.slug)
        segs: list[Path] = []
        total = len(ep.items)
        short = ep.format == "short"

        if not short:
            T = self.build_card(ep.intro, self.pack.display_name, ep.theme, self.s.channel_name, 0.4, 0.5)
            segs.append(self.encode(T, None, 0, total, work, work / "00-intro.mp4"))
        for i, it in enumerate(ep.items, 1):
            print(f"[render] round {i}/{total}: {it.answer}")
            T = self.build_round(i, total, it, ep, rng, work)
            segs.append(self.encode(T, it, i, total, work, work / f"{i:02d}.mp4"))
        if not short:
            maxp = total * (1 + len(ep.items[0].hints)) if ep.mechanic == "ladder" else total
            T = self.build_card(ep.outro, "How did you do?", f"{maxp} points possible", "Drop your score in the comments", 0.3, 1.2)
            segs.append(self.encode(T, None, 0, total, work, work / "99-outro.mp4"))

        lst = work / "segments.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))
        final = self.s.out_dir / ep.slug / f"{ep.slug}.mp4"
        bed = bed_for(self.s, ep.slug, work)
        mix = work / "mix.wav"
        if bed:
            # Gentle sidechain: slow attack/release so gaps between words don't pump the bed up and down.
            ratio = 10 ** (-self.s.music_duck_db / 20)
            graph = ("[0:a]asplit=2[v][sc];"
                     f"[1:a]volume={self.s.music_bed_db}dB[b];"
                     f"[b][sc]sidechaincompress=threshold=0.01:ratio={ratio:.1f}:attack=60:release=1600:makeup=1[bd];"
                     "[v][bd]amix=inputs=2:duration=first:normalize=0[a]")
            run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-stream_loop", "-1", "-i", str(bed),
                   "-filter_complex", graph, "-map", "[a]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(mix)))
        else:
            run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(mix)))
        # Two-pass loudness: measure, then apply a single linear gain. (One-pass loudnorm is a dynamic
        # normaliser and audibly rides the gain on the music between lines.)
        run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(mix), "-map", "0:v", "-map", "1:a",
               "-af", self.loudnorm_linear(mix), "-c:v", "copy", *AUD, "-movflags", "+faststart", str(final)))

        thumb = self.s.out_dir / ep.slug / "thumbnail.jpg"
        first = ep.items[0]
        self.scene.thumbnail("Name it from the sound" if short else ep.theme, first.tint,
                             self.envelope(first.prompt_audio, work), thumb)

        (self.s.out_dir / ep.slug / "episode.json").write_text(json.dumps({
            "slug": ep.slug, "pack": ep.pack, "format": ep.format, "mechanic": ep.mechanic,
            "title": ep.title, "description": ep.description, "tags": ep.tags, "theme": ep.theme,
            "items": [{"id": it.id, "answer": it.answer, "hints": it.hints, "fact": it.fact} for it in ep.items],
        }, indent=2, ensure_ascii=False))
        shutil.rmtree(work)
        return RenderResult(video=final, thumbnail=thumb, duration_seconds=duration(final))
