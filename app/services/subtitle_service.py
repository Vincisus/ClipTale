<<<<<<< HEAD

=======
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
import re
from pathlib import Path


class SubtitleService:
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
    def create_srt_from_script(self, script: str, output_path: Path | None, target_duration: int):
        sentences = self._split_sentences(script)
        if not sentences:
            sentences = ["Storia non disponibile."]

        total_chars = max(1, sum(len(sentence) for sentence in sentences))
        durations = [max(1.5, round(target_duration * (len(s) / total_chars), 2)) for s in sentences]
<<<<<<< HEAD
=======
=======
    def create_srt_from_script(self, script: str, output_path: Path, target_duration: int):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sentences = self._split_sentences(script)

        if not sentences:
            sentences = ["Storia non disponibile."]

        total_chars = sum(len(sentence) for sentence in sentences)
        if total_chars == 0:
            total_chars = 1

        durations = []
        remaining = target_duration
        for sentence in sentences:
            share = len(sentence) / total_chars
            duration = max(1.5, round(target_duration * share, 2))
            durations.append(duration)

>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        scale = target_duration / sum(durations)
        durations = [duration * scale for duration in durations]

        srt_blocks = []
        current = 0.0
        for index, (sentence, duration) in enumerate(zip(sentences, durations), start=1):
            start = current
            end = min(target_duration, current + duration)
            current = end
            srt_blocks.append(
                f"{index}\n"
                f"{self._format_timestamp(start)} --> {self._format_timestamp(end)}\n"
                f"{sentence}\n"
            )

<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        content = "\n".join(srt_blocks)
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            return output_path
        return content
<<<<<<< HEAD
=======
=======
        output_path.write_text("\n".join(srt_blocks), encoding="utf-8")
        return output_path
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36

    @staticmethod
    def _split_sentences(script: str):
        text = re.sub(r"\s+", " ", script).strip()
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [part.strip() for part in parts if part.strip()]

    @staticmethod
    def _format_timestamp(seconds: float):
        milliseconds = int(round(seconds * 1000))
        hours, remainder = divmod(milliseconds, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"
