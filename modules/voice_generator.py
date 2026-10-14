"""
modules/voice_generator.py
Generates natural-sounding neural voiceovers using Edge TTS.
Output: output/voice.mp3
"""

import asyncio
import logging
import os

import edge_tts
import config as cfg

logger = logging.getLogger(__name__)


class VoiceGenerator:
    """Converts a script into a neural text-to-speech MP3."""

    def __init__(
        self,
        language: str = "en",
        slow: bool = False,
        voice: str = "en-US-GuyNeural",
    ):
        self.language = language
        self.slow = slow
        self.voice = voice

    async def _generate_async(self, script: str, output_path: str) -> None:
        rate = "-10%" if self.slow else "+0%"
        communicate = edge_tts.Communicate(
            text=script,
            voice=self.voice,
            rate=rate,
        )
        await communicate.save(output_path)

    def generate(self, script: str, output_path: str = cfg.VOICE_PATH) -> str:
        """Generate an MP3 voiceover and return its absolute path."""
        if not script or not script.strip():
            raise ValueError("Script text is empty. Cannot generate voice.")

        logger.info(
            "Generating neural voiceover (voice=%s, slow=%s)...",
            self.voice,
            self.slow,
        )

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        try:
            asyncio.run(self._generate_async(script.strip(), output_path))
        except Exception as exc:
            logger.error("Edge TTS voice generation failed: %s", exc)
            raise

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"Edge TTS did not create a valid file: {output_path}")

        size_kb = os.path.getsize(output_path) / 1024
        logger.info("Neural voiceover saved to '%s' (%.1f KB).", output_path, size_kb)
        return os.path.abspath(output_path)
