<<<<<<< HEAD

=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
from pathlib import Path
import re
import shutil
import tempfile

from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip
from PIL import Image
<<<<<<< HEAD
=======
=======
import shutil
import subprocess
from pathlib import Path

from moviepy import ImageClip, AudioFileClip, TextClip, CompositeVideoClip
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36


class RenderService:
    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 30):
        self.width = width
        self.height = height
        self.fps = fps

    def ffmpeg_available(self):
        return shutil.which("ffmpeg") is not None

<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
    @staticmethod
    def _parse_srt(subtitle_path: Path) -> list[tuple[float, float, str]]:
        if not subtitle_path or not Path(subtitle_path).is_file():
            return []
        raw = Path(subtitle_path).read_text(encoding="utf-8")
        entries = []
        pattern = re.compile(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)",
            re.DOTALL,
        )
        for m in pattern.finditer(raw):
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
            end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000.0
            text = re.sub(r"\s+", " ", m.group(9)).strip()
            if text and end > start:
                entries.append((start, end, text))
        return entries

<<<<<<< HEAD
=======
=======
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
    async def render_story_video(
        self,
        storyboard: dict,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
        duration_seconds: int,
    ):
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            bg_path = Path(tmp_dir) / "background.png"
            Image.new("RGB", (self.width, self.height), (11, 11, 18)).save(bg_path)

            audio = AudioFileClip(str(audio_path))
            to_close = [audio]
            try:
                effective_duration = max(1.0, min(float(duration_seconds), float(audio.duration or duration_seconds)))
                base_bg = ImageClip(str(bg_path)).with_duration(effective_duration)
                to_close.append(base_bg)
                layers = [base_bg]

                srt_entries = self._parse_srt(subtitle_path)
                if srt_entries:
                    for start, end, text in srt_entries:
                        if start >= effective_duration:
                            break
                        clip_end = min(effective_duration, end)
                        dur = max(0.2, clip_end - start)
                        txt = (
                            TextClip(
                                text=text,
                                font_size=48,
                                color="white",
                                size=(self.width - 140, None),
                                method="caption",
                            )
                            .with_start(start)
                            .with_duration(dur)
                            .with_position(("center", int(self.height * 0.72)))
                        )
                        to_close.append(txt)
                        layers.append(txt)
                else:
                    for scene in storyboard.get("scenes", []):
                        start = float(scene.get("start", 0))
                        if start >= effective_duration:
                            break
                        end = min(effective_duration, float(scene.get("end", start + 6)))
                        dur = max(0.5, end - start)
                        caption = (scene.get("on_screen_text") or scene.get("visual") or "")[:180]
                        if caption:
                            txt = (
                                TextClip(
                                    text=caption,
                                    font_size=48,
                                    color="white",
                                    size=(self.width - 140, None),
                                    method="caption",
                                )
                                .with_start(start)
                                .with_duration(dur)
                                .with_position(("center", int(self.height * 0.72)))
                            )
                            to_close.append(txt)
                            layers.append(txt)

                final = CompositeVideoClip(layers, size=(self.width, self.height)).with_audio(audio).with_duration(effective_duration)
                to_close.append(final)
                final.write_videofile(
                    str(output_path),
                    fps=self.fps,
                    codec="libx264",
                    audio_codec="aac",
                    logger=None,
                )
            finally:
                for item in reversed(to_close):
                    try:
                        item.close()
                    except Exception:
                        pass

        return output_path
<<<<<<< HEAD
=======
=======
        output_path.parent.mkdir(parents=True, exist_ok=True)
        scenes = storyboard.get("scenes", [])

        if not scenes:
            background = ImageClip(self._solid_frame()).with_duration(duration_seconds)
            audio = AudioFileClip(str(audio_path))
            final = background.with_audio(audio)
            final.write_videofile(
                str(output_path),
                fps=self.fps,
                codec="libx264",
                audio_codec="aac",
                logger=None,
            )
            return output_path

        clips = []
        for scene in scenes:
            start = float(scene.get("start", 0))
            end = float(scene.get("end", start + 6))
            duration = max(1, end - start)

            background = ImageClip(self._solid_frame()).with_duration(duration)
            text = scene.get("on_screen_text") or scene.get("narration", "")

            if text:
                text_clip = (
                    TextClip(
                        text=text,
                        font_size=54,
                        color="white",
                        size=(self.width - 120, None),
                        method="caption",
                    )
                    .with_duration(duration)
                    .with_position(("center", "center"))
                )
                background = CompositeVideoClip([background, text_clip])

            clips.append(background.with_start(start))

        audio = AudioFileClip(str(audio_path))
        final = CompositeVideoClip(clips, size=(self.width, self.height)).with_audio(audio)
        final = final.with_duration(min(duration_seconds, audio.duration))

        final.write_videofile(
            str(output_path),
            fps=self.fps,
            codec="libx264",
            audio_codec="aac",
            logger=None,
        )
        return output_path

    def _solid_frame(self):
        from PIL import Image

        image = Image.new("RGB", (self.width, self.height), (11, 11, 18))
        temp_path = Path("temp_background.png")
        image.save(temp_path)
        return str(temp_path)
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
