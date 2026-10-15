"""Assemble an Episode into an MP4 with ffmpeg.

Mechanics (per round):
  ladder  question → sound ×2 → think (3 pts) → hint 1 + sound → think (2 pts) → hint 2 → think (1 pt) → reveal
  choice  question → sound ×2 → spoken A/B/C/D → think → reveal
  cold    question → sound ×2 → think → reveal
Every hint is spoken, so the whole thing works audio-only; on screen the hints appear progressively.

Each segment is an MP4 with identical encoding params so the final concat is a plain -c copy of video;
audio gets one loudnorm pass at the end, with an optional music bed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import Settings
from ..music import bed_for
from ..models import Episode, QuizItem, RenderResult
from ..packs import Pack
from ..tts import TTS
from .cards import Cards

VID = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-profile:v", "high", "-level", "4.1"]
AUD = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
THINK_FPS = 10


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def ff(*args: str) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


class Renderer:
    def __init__(self, settings: Settings, pack: Pack, tts: TTS):
        self.s = settings
        self.pack = pack
        self.tts = tts
        self.cards = Cards(settings, pack.accent_color, pack.bg_color)
        self._norm: dict[str, Path] = {}

    # ---- audio helpers --------------------------------------------------------
    def norm(self, src: Path, work: Path) -> Path:
        """Any audio → 48k stereo pcm wav, cached per render."""
        k = str(src)
        if k not in self._norm:
            out = work / f"norm-{len(self._norm):03d}.wav"
            run(ff("-i", str(src), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)))
            self._norm[k] = out
        return self._norm[k]

    def silence(self, secs: float, work: Path) -> Path:
        out = work / f"sil-{secs:.2f}.wav"
        if not out.exists():
            run(ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{secs:.3f}",
                   "-c:a", "pcm_s16le", str(out)))
        return out

    def concat(self, parts: list, out: Path, work: Path) -> Path:
        """parts: Paths (any audio) or floats (seconds of silence)."""
        files = [self.silence(float(p), work) if isinstance(p, (int, float)) else self.norm(p, work) for p in parts]
        lst = work / f"{out.stem}.txt"
        lst.write_text("".join(f"file '{f.resolve()}'\n" for f in files))
        run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "pcm_s16le", str(out)))
        return out

    def synth(self, name: str, hz: int, secs: float, vol: float, work: Path) -> Path:
        out = work / f"{name}.wav"
        if not out.exists():
            run(ff("-f", "lavfi", "-i", f"sine=frequency={hz}:duration={secs}",
                   "-af", f"volume={vol},afade=t=out:st={max(0, secs - 0.06):.2f}:d=0.06",
                   "-ar", "48000", "-ac", "2", str(out)))
        return out

    def tick(self, work: Path, final: bool) -> Path:
        custom = self.s.asset("tick")
        if custom and not final:
            return custom
        return self.synth("tick-hi" if final else "tick", 1100 if final else 700, 0.08, 0.22, work)

    def sting(self, work: Path) -> Path:
        custom = self.s.asset("sting")
        if custom:
            return custom
        # cheap two-note rise as a placeholder reveal sting
        out = work / "sting.wav"
        if not out.exists():
            a = self.synth("sting-a", 523, 0.12, 0.3, work)
            b = self.synth("sting-b", 784, 0.3, 0.3, work)
            self.concat([a, b], out, work)
        return out

    def think_audio(self, secs: int, replay: Path | None, work: Path, tag: str) -> Path:
        """Ticks each second (+ optional replay of the sound mixed on top), exactly `secs` long."""
        ticks = self.concat(sum(([self.tick(work, i == secs - 1), 0.92] for i in range(secs)), []),
                            work / f"ticks-{tag}.wav", work)
        out = work / f"think-{tag}.wav"
        if replay:
            run(ff("-i", str(ticks), "-i", str(self.norm(replay, work)), "-filter_complex",
                   "[1:a]volume=0.8[r];[0:a][r]amix=inputs=2:duration=first:normalize=0[a]", "-map", "[a]",
                   "-t", str(secs), "-c:a", "pcm_s16le", str(out)))
        else:
            run(ff("-i", str(ticks), "-t", str(secs), "-c:a", "pcm_s16le", str(out)))
        return out

    # ---- video helpers --------------------------------------------------------
    def still(self, card: Path, audio: Path, out: Path) -> Path:
        run(ff("-loop", "1", "-framerate", str(self.s.fps), "-i", str(card), "-i", str(audio),
               "-shortest", "-r", str(self.s.fps), *VID, "-tune", "stillimage", *AUD, str(out)))
        return out

    def frames(self, frames_dir: Path, audio: Path, out: Path) -> Path:
        run(ff("-framerate", str(THINK_FPS), "-i", str(frames_dir / "%04d.png"), "-i", str(audio),
               "-shortest", "-r", str(self.s.fps), *VID, *AUD, str(out)))
        return out

    def think_segment(self, i: int, total: int, it: QuizItem, secs: int, *, points: int,
                      hints_shown: list[str], hints_total: int, choices, replay: bool,
                      work: Path, tag: str) -> Path:
        fdir = work / f"think-{tag}"
        fdir.mkdir()
        n_frames = secs * THINK_FPS
        for f in range(n_frames):
            prog = f / n_frames
            self.cards.question_card(i, total, it.reveal_image, it.silhouette, fdir / f"{f:04d}.png",
                                     points=points, hints_shown=hints_shown, hints_total=hints_total,
                                     choices=choices, progress=prog, seconds_left=secs - int(prog * secs),
                                     status="Your guess?")
        audio = self.think_audio(secs, it.prompt_audio if replay else None, work, tag)
        return self.frames(fdir, audio, work / f"{tag}-think.mp4")

    def say_segment(self, card: Path, parts: list, work: Path, tag: str) -> Path:
        return self.still(card, self.concat(parts, work / f"{tag}.wav", work), work / f"{tag}.mp4")

    # ---- main -----------------------------------------------------------------
    def render(self, ep: Episode) -> RenderResult:
        work = self.s.out_dir / ep.slug / "work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        segs: list[Path] = []
        total = len(ep.items)
        short = ep.format == "short"
        S = ep.stage_seconds
        say = self.tts.say

        # intro
        if short:
            # Shorts: no title card — go straight into the question with the intro line over it
            first = ep.items[0]
            self.cards.question_card(1, total, first.reveal_image, first.silhouette, work / "intro.png",
                                     points=3, hints_shown=[], hints_total=len(first.hints), status="Listen")
            segs.append(self.say_segment(work / "intro.png", [0.2, say(ep.intro), 0.3], work, "00-intro"))
        else:
            self.cards.title_card(self.pack.display_name, ep.theme, work / "intro.png", footer=self.s.channel_name)
            segs.append(self.say_segment(work / "intro.png", [0.4, say(ep.intro), 0.6], work, "00-intro"))

        for i, it in enumerate(ep.items, 1):
            print(f"[render] round {i}/{total}: {it.answer} ({ep.mechanic})")
            tag = f"{i:02d}"
            hints_total = len(it.hints) if ep.mechanic == "ladder" else 0
            choices = None
            if ep.mechanic == "choice":
                choices = sorted([it.answer, *it.choices[:3]], key=lambda s: hash((s, ep.slug)))
            card = lambda name, **kw: self.cards.question_card(   # noqa: E731
                i, total, it.reveal_image, it.silhouette, work / f"{tag}-{name}.png",
                hints_total=hints_total, **kw)

            # question + sound ×2
            top_points = 3 if ep.mechanic == "ladder" else 1
            parts = [0.3] if short else [0.3, say(self.pack.question_line.format(n=i)), 0.4]
            parts += [it.prompt_audio, 0.6, it.prompt_audio, 0.3]
            card("q", points=top_points, hints_shown=[], status="Listen")
            segs.append(self.say_segment(work / f"{tag}-q.png", parts, work, f"{tag}a-q"))

            if ep.mechanic == "ladder":
                shown: list[str] = []
                segs.append(self.think_segment(i, total, it, S, points=3, hints_shown=shown, hints_total=hints_total,
                                               choices=None, replay=False, work=work, tag=f"{tag}b"))
                for h_i, hint in enumerate(it.hints):
                    points = max(1, 2 - h_i)
                    shown = shown + [hint]
                    card(f"h{h_i}", points=points, hints_shown=shown, status="")
                    segs.append(self.say_segment(work / f"{tag}-h{h_i}.png",
                                                 [0.2, say(self.pack.hint_line.format(hint=hint)), 0.3],
                                                 work, f"{tag}c{h_i}-hint"))
                    segs.append(self.think_segment(i, total, it, S, points=points, hints_shown=shown,
                                                   hints_total=hints_total, choices=None, replay=(h_i == 0),
                                                   work=work, tag=f"{tag}d{h_i}"))
            elif ep.mechanic == "choice":
                spoken = ", ".join(f"{'ABCD'[k]}, {c}" for k, c in enumerate(choices[:-1])) + f", or {'ABCD'[len(choices)-1]}, {choices[-1]}"
                card("mc", points=1, hints_shown=[], choices=choices, status="")
                segs.append(self.say_segment(work / f"{tag}-mc.png",
                                             [0.2, say(self.pack.choice_line.format(choices=spoken)), 0.2],
                                             work, f"{tag}c-mc"))
                segs.append(self.think_segment(i, total, it, S + 1, points=1, hints_shown=[], hints_total=0,
                                               choices=choices, replay=True, work=work, tag=f"{tag}d"))
            else:  # cold
                segs.append(self.think_segment(i, total, it, S + 1, points=1, hints_shown=[], hints_total=0,
                                               choices=None, replay=True, work=work, tag=f"{tag}d"))

            # reveal
            self.cards.reveal_card(i, total, it.answer, it.subtitle, it.fact, it.reveal_image, work / f"{tag}-r.png")
            spoken_name = it.pronunciation or it.answer
            fact = it.fact if not it.pronunciation else it.fact.replace(it.answer, it.pronunciation)
            parts = [self.sting(work), 0.1, say(self.pack.reveal_line.format(answer=spoken_name)), 0.3]
            if ep.banter.get(i):
                parts += [say(ep.banter[i]), 0.3]
            parts += [it.prompt_audio, 0.4, say(fact), 0.6 if short else 1.0]
            segs.append(self.say_segment(work / f"{tag}-r.png", parts, work, f"{tag}e-reveal"))

        # outro
        if short:
            segs.append(self.say_segment(work / f"{total:02d}-r.png", [say(ep.outro), 0.8], work, "99-outro"))
        else:
            self.cards.score_card(total * (3 if ep.mechanic == "ladder" else 1), work / "outro.png")
            segs.append(self.say_segment(work / "outro.png", [0.3, say(ep.outro), 1.5], work, "99-outro"))

        # concat video (copy) + final audio pass (loudnorm, optional music bed)
        lst = work / "segments.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))
        final = self.s.out_dir / ep.slug / f"{ep.slug}.mp4"
        bed = bed_for(self.s, ep.slug, work)
        if bed:
            # Sidechain: the bed is compressed by the voice/quiz-sound track, so it dips hard whenever anything
            # else is playing and swells back in the silences between rounds.
            ratio = 10 ** (-self.s.music_duck_db / 20)   # -14 dB → ratio ≈ 5
            graph = (
                "[0:a]asplit=2[v][sc];"
                f"[1:a]volume={self.s.music_bed_db}dB[b];"
                f"[b][sc]sidechaincompress=threshold=0.01:ratio={ratio:.1f}:attack=15:release=600:makeup=1[bd];"
                "[v][bd]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
            )
            run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-stream_loop", "-1", "-i", str(bed),
                   "-filter_complex", graph,
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy", *AUD, "-movflags", "+faststart", str(final)))
        else:
            run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                   "-c:v", "copy", *AUD, "-movflags", "+faststart", str(final)))

        thumb = self.s.out_dir / ep.slug / "thumbnail.jpg"
        first = ep.items[0]
        self.cards.thumbnail(ep.theme if not short else "Name it from the sound", first.reveal_image, thumb,
                             silhouette=first.silhouette)

        (self.s.out_dir / ep.slug / "episode.json").write_text(json.dumps({
            "slug": ep.slug, "pack": ep.pack, "format": ep.format, "mechanic": ep.mechanic,
            "title": ep.title, "description": ep.description, "tags": ep.tags, "theme": ep.theme,
            "items": [{"id": it.id, "answer": it.answer, "hints": it.hints, "fact": it.fact} for it in ep.items],
        }, indent=2, ensure_ascii=False))
        shutil.rmtree(work)   # keep the output folder small; cards are cheap to regenerate
        return RenderResult(video=final, thumbnail=thumb, duration_seconds=duration(final))
