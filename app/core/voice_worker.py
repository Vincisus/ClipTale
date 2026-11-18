<<<<<<< HEAD

=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
"""Worker per generazione voce — interfaccia con TTS service."""
import asyncio
from pathlib import Path

class VoiceWorker:
    """Gestisce il processo di generazione voce con retry e monitoring."""
    
    def __init__(self, voice_name="it-IT-ElsaNeural"):
        self.voice_name = voice_name
    
    async def synthesize(self, text, output_path):
        """Sintetizza testo in audio con gestione errori."""
        from app.services.tts_service import TTSService
        tts = TTSService(voice=self.voice_name)
        return await tts.generate_narration(text, output_path)
    
    def validate_audio(self, path):
        """Valida file audio esistente."""
        if not path.exists():
            return False
        return path.stat().st_size > 100  # Minimo 100 bytes
<<<<<<< HEAD
=======
=======
voce Windows italiana isolata
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
