"""Assemble an Episode into an MP4 with ffmpeg.

Structure per round:
  [question card]  narrator asks → sound plays twice
  [countdown]      ring timer, ticks each second, sound replays once at the start
  [reveal card]    narrator: "It's X!" → reads the fact
Plus intro and outro cards. Every segment is an MP4 with identical encoding params so the final
concat is a plain -c copy of video; audio is re-mixed once at the end (loudnorm + optional music bed).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import Settings
from ..models import Episode, RenderResult
from ..packs import Pack
from ..tts import TTS
from .cards import Cards

VID = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-profile:v", "high", "-level", "4.1"]
AUD = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


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

    # ---- audio helpers --------------------------------------------------------
    def silence(self, secs: float, out: Path) -> Path:
        run(ff("-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{secs:.3f}",
               "-c:a", "pcm_s16le", str(out)))
        return out

    def audio_concat(self, parts: list, out: Path, work: Path) -> Path:
        """parts: Paths (audio files) or floats (seconds of silence)."""
        files = []
        for i, p in enumerate(parts):
            if isinstance(p, (int, float)):
                files.append(self.silence(float(p), work / f"sil-{out.stem}-{i}.wav"))
            else:
                # normalise everything to the same format (prompt audio may be ogg/mp3/any rate)
                norm = work / f"norm-{out.stem}-{i}.wav"
                run(ff("-i", str(p), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(norm)))
                files.append(norm)
        lst = work / f"{out.stem}.txt"
        lst.write_text("".join(f"file '{f.resolve()}'\n" for f in files))
        run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "pcm_s16le", str(out)))
        return out

    def ticks(self, seconds: int, out: Path, work: Path) -> Path:
        """One short blip per second, higher pitch on the final one."""
        parts = []
        for i in range(seconds):
            hz = 1100 if i == seconds - 1 else 700
            blip = work / f"blip-{hz}.wav"
            if not blip.exists():
                run(ff("-f", "lavfi", "-i", f"sine=frequency={hz}:duration=0.08",
                       "-af", "volume=0.25,afade=t=out:st=0.03:d=0.05", "-ar", "48000", "-ac", "2", str(blip)))
            parts += [blip, 0.92]
        return self.audio_concat(parts, out, work)

    # ---- segment builders -----------------------------------------------------
    def still_segment(self, card: Path, audio: Path, out: Path) -> Path:
        run(ff("-loop", "1", "-framerate", str(self.s.fps), "-i", str(card), "-i", str(audio),
               "-shortest", "-r", str(self.s.fps), *VID, "-tune", "stillimage", *AUD, str(out)))
        return out

    def frames_segment(self, frames_dir: Path, frame_rate: int, audio: Path, out: Path) -> Path:
        run(ff("-framerate", str(frame_rate), "-i", str(frames_dir / "%04d.png"), "-i", str(audio),
               "-shortest", "-r", str(self.s.fps), *VID, *AUD, str(out)))
        return out

    # ---- main -----------------------------------------------------------------
    def render(self, ep: Episode) -> RenderResult:
        work = self.s.out_dir / ep.slug / "work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        segs: list[Path] = []
        total = len(ep.items)

        # intro
        self.cards.title_card(self.pack.display_name, ep.theme, work / "intro.png")
        self.audio_concat([0.4, self.tts.say(ep.intro), 0.6], work / "intro.wav", work)
        segs.append(self.still_segment(work / "intro.png", work / "intro.wav", work / "00-intro.mp4"))

        for i, it in enumerate(ep.items, 1):
            print(f"[render] round {i}/{total}: {it.answer}")
            # question: ask, play twice
            self.cards.question_card(i, total, "Listen closely", it.reveal_image, work / f"q{i}.png", it.silhouette)
            q_line = self.pack.question_line.format(n=i)
            self.audio_concat([0.3, self.tts.say(q_line), 0.5, it.prompt_audio, 0.7, it.prompt_audio, 0.4],
                              work / f"q{i}.wav", work)
            segs.append(self.still_segment(work / f"q{i}.png", work / f"q{i}.wav", work / f"{i:02d}a-q.mp4"))

            # countdown: frames at 10 fps, sound once more at the start + ticks
            fdir = work / f"cd{i}"
            fdir.mkdir()
            n_frames = ep.countdown_seconds * 10
            for f in range(n_frames):
                prog = f / n_frames
                left = ep.countdown_seconds - int(prog * ep.countdown_seconds)
                self.cards.question_card(i, total, "Your guess?", it.reveal_image, fdir / f"{f:04d}.png",
                                         it.silhouette, progress=prog, seconds_left=left)
            ticks = self.ticks(ep.countdown_seconds, work / f"ticks{i}.wav", work)
            prompt_norm = work / f"cdprompt{i}.wav"
            run(ff("-i", str(it.prompt_audio), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(prompt_norm)))
            cd_audio = work / f"cd{i}.wav"
            # mix the replayed sound over the ticks, cut to countdown length
            run(ff("-i", str(ticks), "-i", str(prompt_norm), "-filter_complex",
                   "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[a]", "-map", "[a]",
                   "-t", str(ep.countdown_seconds), "-c:a", "pcm_s16le", str(cd_audio)))
            segs.append(self.frames_segment(fdir, 10, cd_audio, work / f"{i:02d}b-cd.mp4"))

            # reveal
            self.cards.reveal_card(i, total, it.answer, it.subtitle, it.fact, it.reveal_image, work / f"r{i}.png")
            spoken_name = it.pronunciation or it.answer
            reveal_line = self.pack.reveal_line.format(answer=spoken_name)
            fact = it.fact if not it.pronunciation else it.fact.replace(it.answer, it.pronunciation)
            self.audio_concat([0.2, it.prompt_audio, 0.3, self.tts.say(reveal_line), 0.5, self.tts.say(fact), 1.0],
                              work / f"r{i}.wav", work)
            segs.append(self.still_segment(work / f"r{i}.png", work / f"r{i}.wav", work / f"{i:02d}c-r.mp4"))

        # outro
        self.cards.title_card("Thanks for playing", "Subscribe for the next one", work / "outro.png")
        self.audio_concat([0.3, self.tts.say(ep.outro), 1.5], work / "outro.wav", work)
        segs.append(self.still_segment(work / "outro.png", work / "outro.wav", work / "99-outro.mp4"))

        # concat video (copy) + final audio pass (loudnorm, optional music bed)
        lst = work / "segments.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))
        final = self.s.out_dir / ep.slug / f"{ep.slug}.mp4"
        if self.s.music_bed and Path(self.s.music_bed).exists():
            run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-stream_loop", "-1", "-i", self.s.music_bed,
                   "-filter_complex",
                   f"[1:a]volume={self.s.music_bed_db}dB[bed];[0:a][bed]amix=inputs=2:duration=first:normalize=0,"
                   f"loudnorm=I=-16:TP=-1.5:LRA=11[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy", *AUD, "-movflags", "+faststart", str(final)))
        else:
            run(ff("-f", "concat", "-safe", "0", "-i", str(lst), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                   "-c:v", "copy", *AUD, "-movflags", "+faststart", str(final)))

        thumb = self.s.out_dir / ep.slug / "thumbnail.jpg"
        first = ep.items[0]
        self.cards.thumbnail(ep.title.split(":")[0] if ":" in ep.title else ep.title,
                             first.reveal_image, thumb, silhouette=first.silhouette)

        (self.s.out_dir / ep.slug / "episode.json").write_text(json.dumps({
            "slug": ep.slug, "pack": ep.pack, "title": ep.title, "description": ep.description,
            "tags": ep.tags, "theme": ep.theme,
            "items": [{"id": it.id, "answer": it.answer, "fact": it.fact} for it in ep.items],
        }, indent=2, ensure_ascii=False))
        return RenderResult(video=final, thumbnail=thumb, duration_seconds=duration(final))
