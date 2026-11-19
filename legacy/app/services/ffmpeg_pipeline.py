<<<<<<< HEAD
"""Pipeline FFmpeg con progressi, watchdog e validazione."""
import asyncio
import os
import subprocess
from pathlib import Path

class FFMpegPipeline:
    """Gestisce operazioni FFmpeg con retry e monitoring."""
    
    def __init__(self):
        self.ffmpeg_available = self._check_ffmpeg()
    
    def _check_ffmpeg(self):
        """Verifica disponibilità FFmpeg."""
        return subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        ).returncode == 0
    
    async def concat_video(self, inputs, output_path, audio_path=None):
        """Concatena video e opzionalmente unisce audio."""
        cmd = ["ffmpeg", "-y"]
        
        for input_file in inputs:
            cmd.extend(["-i", str(input_file)])
        
        cmd.extend(["-c", "copy"])
        if audio_path:
            cmd.extend(["-i", str(audio_path)])
            cmd.extend(["-map", "0:v:0"])
            cmd.extend(["-map", "1:a:0"])
        
        cmd.append(str(output_path))
        
        return await self._run(cmd)
    
    async def extract_audio(self, video_path, output_path):
        """Estrae audio da un video."""
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-c:a", "aac", str(output_path)]
        return await self._run(cmd)
    
    async def create_clip(self, input_path, start, end, output_path):
        """Crea una clip da un video."""
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(input_path),
            "-t", str(end - start),
            "-c", "copy",
            str(output_path)
        ]
        return await self._run(cmd)
    
    async def _run(self, cmd):
        """Esegue comando FFmpeg con gestione errori."""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode()[:500]
                raise RuntimeError(f"FFmpeg error: {error_msg}")
            
            return True
        except FileNotFoundError:
            raise RuntimeError("FFmpeg non trovato")
        except Exception as e:
            raise RuntimeError(f"Pipeline FFmpeg fallita: {e}")
=======
pipeline FFmpeg con progressi, watchdog e validazione
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
