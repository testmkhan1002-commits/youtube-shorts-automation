
"""
modules/subtitle_generator.py

Generates readable, time-estimated subtitles and FFmpeg drawtext filters.
Compatible with the existing VideoEditor interface.
"""

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)


class SubtitleGenerator:
    """Creates short caption chunks with estimated timestamps."""

    FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def __init__(self, wpm: int = 150):
        self.wpm = max(1, wpm)

    def generate(
        self, script_text: str, duration: float
    ) -> List[Tuple[float, float, str]]:
        """
        Return (start, end, caption) tuples.

        Timing is estimated from text length, not actual audio alignment.
        """
        if not script_text or not script_text.strip() or duration <= 0:
            return []

        clean_text = re.sub(r"\s+", " ", script_text).strip()
        chunks = self._split_into_chunks(clean_text)

        if not chunks:
            return []

        return self._assign_timestamps(chunks, duration)

    def to_ffmpeg_drawtext(
        self,
        subtitles: List[Tuple[float, float, str]],
        video_width: int = 1080,
        video_height: int = 1920,
        font_size: int = 52,
    ) -> str:
        """Build an FFmpeg drawtext filter with readable caption styling."""
        if not subtitles:
            return ""

        # Keep captions above the bottom interface overlay area.
        y_pos = int(video_height * 0.72)

        parts = []

        for start, end, caption in subtitles:
            if end <= start:
                continue

            wrapped = self._wrap_text(caption, max_chars=25)
            escaped_lines = []

            for line in wrapped.split("\n"):
                escaped_lines.append(self._escape_drawtext(line))

            safe_text = r"\n".join(escaped_lines)

            entry = (
                "drawtext="
                f"fontfile={self.FONT_PATH}:"
                f"text='{safe_text}':"
                f"expansion=none:"
                f"fontsize={font_size}:"
                "fontcolor=white:"
                "borderw=2:"
                "bordercolor=black:"
                "box=1:"
                "boxcolor=black@0.60:"
                "boxborderw=14:"
                "fix_bounds=1:"
                "x=(w-text_w)/2:"
                f"y={y_pos}:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )

            parts.append(entry)

        logger.info("Created %d caption filters.", len(parts))
        return ",".join(parts)

    @staticmethod
    def _split_into_chunks(text: str) -> List[str]:
        """
        Split narration into short, readable phrases.
        Prefer sentence boundaries and keep captions to about 3–6 words.
        """
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: List[str] = []

        for sentence in sentences:
            words = sentence.strip().split()

            if not words:
                continue

            current: List[str] = []

            for word in words:
                current.append(word)

                # End a phrase after 4 words, or earlier at punctuation.
                has_punctuation = bool(re.search(r"[,;:]$", word))

                if len(current) >= 4 or has_punctuation:
                    chunks.append(" ".join(current))
                    current = []

            if current:
                chunks.append(" ".join(current))

        return chunks

    @staticmethod
    def _assign_timestamps(
        chunks: List[str], duration: float
    ) -> List[Tuple[float, float, str]]:
        """Estimate phrase timing in proportion to the number of characters."""
        weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
        total_weight = sum(weights)

        if total_weight <= 0 or duration <= 0:
            return []

        subtitles: List[Tuple[float, float, str]] = []
        current_time = 0.0

        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            start = current_time

            if index == len(chunks) - 1:
                end = duration
            else:
                end = start + duration * weight / total_weight

            if end > start:
                subtitles.append((start, end, chunk))
                current_time = end

        return subtitles

    @staticmethod
    def _wrap_text(text: str, max_chars: int = 25) -> str:
        """Wrap captions into balanced lines at word boundaries."""
        words = text.split()
        lines: List[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip()

            if current and len(candidate) > max_chars:
                lines.append(current)
                current = word
            else:
                current = candidate

        if current:
            lines.append(current)

        # Limit each caption to two lines where practical.
        if len(lines) > 2:
            lines = [
                " ".join(words[: len(words) // 2]),
                " ".join(words[len(words) // 2 :]),
            ]

        return "\n".join(lines)

    @staticmethod
    def _escape_drawtext(text: str) -> str:
        """Escape characters that can break an FFmpeg filter expression."""
        replacements = {
            "\\": r"\\",
            "'": "’",
            ":": r"\:",
            "%": r"\%",
            ",": r"\,",
            "[": r"\[",
            "]": r"\]",
            ";": r"\;",
        }

        return "".join(replacements.get(char, char) for char in text)
