<<<<<<< HEAD

=======
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
from pathlib import Path

from moviepy import VideoFileClip


class TikTokService:
    async def create_clips(
        self,
        render_path: Path,
        structure: dict,
        output_dir: Path,
        clips_requested: int = 4,
    ):
<<<<<<< HEAD
        output_dir = Path(output_dir)
=======
<<<<<<< HEAD
        output_dir = Path(output_dir)
=======
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        output_dir.mkdir(parents=True, exist_ok=True)
        parts = structure.get("parts", [])

        if not parts:
            return []

<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        created = []
        with VideoFileClip(str(render_path)) as source:
            total_duration = source.duration
            interval = total_duration / len(parts)
            selected_indices = self._select_parts(len(parts), clips_requested)

            for index in selected_indices:
<<<<<<< HEAD
=======
=======
        with VideoFileClip(str(render_path)) as source:
            total_duration = source.duration
            interval = total_duration / len(parts)

            selected_indices = self._select_parts(len(parts), clips_requested)
            created = []

            for index in selected_indices:
                part = parts[index]
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
                start = index * interval
                end = min(total_duration, start + interval)
                duration = end - start

                if duration < 5:
                    continue

                output_path = output_dir / f"tiktok_part_{index + 1:02}.mp4"
                clip = source.subclipped(start, end)
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
                try:
                    clip.write_videofile(
                        str(output_path),
                        codec="libx264",
                        audio_codec="aac",
                        logger=None,
                    )
                finally:
                    clip.close()
<<<<<<< HEAD
=======
=======
                clip.write_videofile(
                    str(output_path),
                    codec="libx264",
                    audio_codec="aac",
                    logger=None,
                )
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
                created.append(output_path)

        return created

    @staticmethod
    def _select_parts(total_parts: int, requested: int):
        if total_parts <= requested:
            return list(range(total_parts))

        step = total_parts / requested
        return sorted({min(total_parts - 1, int(index * step)) for index in range(requested)})
