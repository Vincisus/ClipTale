<<<<<<< HEAD

import asyncio
from pathlib import Path
import shutil
import subprocess
import tempfile
=======
import asyncio
from pathlib import Path
<<<<<<< HEAD
import shutil
import subprocess
import tempfile
=======
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36

import pyttsx3


class TTSService:
    def __init__(self, voice: str | None = None):
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        self.voice = voice or self._default_voice()

    @staticmethod
    def _default_voice() -> str | None:
        cfg = Path("config/settings.yaml")
        if cfg.is_file():
            for line in cfg.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("voice:"):
                    return stripped.split(":", 1)[1].strip().strip('"\'')
        return None

    async def generate_narration(self, script: str, output_path: Path):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text = self._clean_script(script)
        await asyncio.to_thread(self._synthesize, text, output_path)
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Sintesi vocale fallita: file audio vuoto o non creato.")
<<<<<<< HEAD
=======
=======
        self.voice = voice

    async def generate_narration(self, script: str, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text = self._clean_script(script)
        await asyncio.to_thread(self._synthesize, text, output_path)
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
        return output_path

    def _synthesize(self, text: str, output_path: Path):
        engine = pyttsx3.init()
        engine.setProperty("rate", 165)
        engine.setProperty("volume", 1.0)

        if self.voice:
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
            voices = engine.getProperty("voices") or []
            for voice in voices:
                if (
                    self.voice.lower() in str(voice.id).lower()
                    or self.voice.lower() in str(voice.name).lower()
                    or "italian" in str(voice.name).lower()
                    or "it-it" in str(voice.id).lower()
                ):
                    engine.setProperty("voice", voice.id)
                    break

        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "narration_raw.wav"
            engine.save_to_file(text, str(wav_path))
            engine.runAndWait()

            if output_path.suffix.lower() == ".mp3" and shutil.which("ffmpeg"):
                res = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-qscale:a", "4", str(output_path)],
                    capture_output=True,
                    text=True,
                )
                if res.returncode != 0:
                    raise RuntimeError(f"Conversione MP3 FFmpeg fallita: {res.stderr[:300]}")
            else:
                shutil.copyfile(wav_path, output_path)
<<<<<<< HEAD
=======
=======
            voices = engine.getProperty("voices")
            for voice in voices:
                if self.voice.lower() in voice.id.lower() or self.voice.lower() in voice.name.lower():
                    engine.setProperty("voice", voice.id)
                    break

        engine.save_to_file(text, str(output_path))
        engine.runAndWait()
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36

    @staticmethod
    def _clean_script(script: str):
        return "\n".join(
            line.strip()
            for line in script.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
