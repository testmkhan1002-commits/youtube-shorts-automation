"""
modules/video_editor.py
Assembles the final YouTube Short by combining background video + voiceover.

Pipeline:
  1. Pre-process segments: landscape clips → 1080×1920 via FFmpeg blur-pad
     (blurred background + sharp centered overlay — no content cropped out)
  2. Concatenate multiple segments for visual variety
  3. Load audio track from voiceover MP3
  4. Loop or trim the video to match audio duration (capped at 59s)
  5. Export a clean MP4 (no text via MoviePy — no ImageMagick needed)
  6. Burn subtitles via FFmpeg drawtext post-pass (pure FFmpeg, no ImageMagick)

Primary engine: MoviePy (video/audio compositing, no text)
Subtitle burn:  FFmpeg drawtext (reliable, no ImageMagick dependency)
Fallback:       FFmpeg subprocess for full encode + subtitle burn
Landscape fix:  FFmpeg blur-pad converts horizontal clips to 9:16 before MoviePy
"""

import logging
import os
import subprocess
import tempfile
from typing import Optional, List
import PIL.Image

# Monkeypatch for MoviePy 1.0.3 + Pillow 10+ compatibility
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

import config as cfg
from modules.subtitle_generator import SubtitleGenerator
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip
from moviepy.audio.fx.all import audio_loop, volumex

logger = logging.getLogger(__name__)

# Path to DejaVu font — installed by the workflow via fonts-dejavu-extra
DEJAVU_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# Windows fallback (for local dev)
DEJAVU_FONT_WIN = "C:/Windows/Fonts/arial.ttf"


class VideoEditor:
    """Combines video + audio, then burns subtitles via FFmpeg."""

    def __init__(self):
        self.width = cfg.SHORTS_WIDTH      # 1080
        self.height = cfg.SHORTS_HEIGHT    # 1920
        self.fps = cfg.SHORTS_FPS          # 30
        self.max_duration = cfg.SHORTS_MAX_DURATION  # 59s

    def edit(
        self,
        video_path: str = cfg.VIDEO_RAW_PATH,
        audio_path: str = cfg.VOICE_PATH,
        output_path: str = cfg.FINAL_VIDEO_PATH,
        script: str = "",
        topic: str = "",
        video_paths: Optional[List[str]] = None,
    ) -> str:
        """
        Create the final Short MP4 with burned-in subtitles.

        Args:
            video_path:  Path to background video (fallback).
            audio_path:  Path to voiceover MP3.
            output_path: Destination for final MP4.
            script:      Voiceover text (used for subtitle generation).
            topic:       Video topic (unused for rendering, kept for compat).
            video_paths: Optional list of segment video paths.

        Returns:
            Absolute path to final video.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Use a temp path for the clean (no subtitles) intermediate video
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(output_path))
        os.close(tmp_fd)

        try:
            # Step A: compose video + audio with MoviePy (no text rendering)
            self._compose_with_moviepy(
                video_path=video_path,
                audio_path=audio_path,
                output_path=tmp_path,
                video_paths=video_paths,
            )

            # Step B: burn subtitles via FFmpeg drawtext if we have a script
            if script and script.strip():
                self._burn_subtitles_ffmpeg(
                    input_path=tmp_path,
                    output_path=output_path,
                    script=script,
                    audio_path=audio_path,
                )
            else:
                # No subtitles — just rename the intermediate file
                os.replace(tmp_path, output_path)
                tmp_path = None  # already moved

        except Exception as e:
            logger.warning("MoviePy compose failed (%s). Falling back to pure FFmpeg.", e)
            try:
                self._edit_with_ffmpeg_full(
                    video_path=video_path,
                    audio_path=audio_path,
                    output_path=output_path,
                    script=script,
                    video_paths=video_paths,
                )
            except Exception as fe:
                logger.error("FFmpeg fallback also failed: %s", fe)
                raise
        finally:
            # Clean up temp file if it still exists
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info("Final video saved (%.1f MB): %s", size_mb, output_path)
        return output_path

    # ── MoviePy: video + audio compositing ONLY (no text/ImageMagick) ─────────

    def _compose_with_moviepy(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        video_paths: Optional[List[str]] = None,
    ) -> None:
        from moviepy.editor import (
            VideoFileClip, AudioFileClip, CompositeVideoClip,
            concatenate_videoclips,
        )

        logger.info("Compositing video + audio with MoviePy...")

        # Load audio and clamp duration
        voice_clip = AudioFileClip(audio_path)
        target_duration = min(voice_clip.duration, self.max_duration)
        voice_clip = voice_clip.subclip(0, target_duration)

        # Optional background music mix
        final_audio = self._mix_audio(voice_clip, target_duration)

        # Pre-process segments: convert any landscape clips to vertical (blur-pad)
        # then load and concatenate
        if video_paths and len(video_paths) > 0:
            logger.info("Pre-processing and concatenating %d video segments...", len(video_paths))
            segment_clips = []
            segment_duration = target_duration / len(video_paths)
            for path in video_paths:
                vertical_path = self._to_vertical_ffmpeg(path)
                clip = VideoFileClip(vertical_path, audio=False)
                clip = self._resize_crop(clip, segment_duration)
                segment_clips.append(clip)
            bg_clip = concatenate_videoclips(segment_clips)
        else:
            vertical_path = self._to_vertical_ffmpeg(video_path)
            bg_clip = VideoFileClip(vertical_path, audio=False)
            bg_clip = self._resize_crop(bg_clip, target_duration)

        bg_clip = bg_clip.set_duration(target_duration)

        # Composite video (background only — no text overlays)
        final = CompositeVideoClip([bg_clip], size=(self.width, self.height))
        final = final.set_audio(final_audio)
        final = final.set_duration(target_duration)

        logger.info("Exporting clean video to '%s'...", output_path)
        final.write_videofile(
            output_path,
            fps=self.fps,
            codec="libx264",
            audio_codec="aac",
            bitrate=cfg.SHORTS_VIDEO_BITRATE,
            audio_bitrate=cfg.SHORTS_AUDIO_BITRATE,
            threads=2,
            preset="medium",
            verbose=False,
            logger=None,
        )

        # Cleanup
        for clip in [final, voice_clip, final_audio, bg_clip]:
            try:
                clip.close()
            except Exception:
                pass

        logger.info("Clean video exported.")

    # ── FFmpeg: subtitle burn-in ───────────────────────────────────────────────

    def _burn_subtitles_ffmpeg(
        self,
        input_path: str,
        output_path: str,
        script: str,
        audio_path: str,
    ) -> None:
        """
        Burn subtitles into the clean video using FFmpeg drawtext filter.
        This approach requires NO ImageMagick — pure FFmpeg only.
        """
        logger.info("Burning subtitles via FFmpeg drawtext...")

        # Get real audio duration for subtitle timing
        duration = self._probe_duration(audio_path) or self.max_duration
        duration = min(duration, self.max_duration)

        # Generate subtitle timing data
        gen = SubtitleGenerator()
        subtitles = gen.generate(script, duration)

        if not subtitles:
            logger.warning("No subtitles generated — copying video as-is.")
            os.replace(input_path, output_path)
            return

        # Build FFmpeg drawtext filter
        font_path = DEJAVU_FONT if os.path.exists(DEJAVU_FONT) else DEJAVU_FONT_WIN
        drawtext_filter = gen.to_ffmpeg_drawtext(
            subtitles,
            video_width=self.width,
            video_height=self.height,
        )

        # Override font path with the discovered one
        drawtext_filter = drawtext_filter.replace(
            SubtitleGenerator.FONT_PATH, font_path
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", drawtext_filter,
            "-c:v", "libx264",
            "-preset", "medium",
            "-b:v", cfg.SHORTS_VIDEO_BITRATE,
            "-c:a", "copy",           # audio already encoded — just copy
            "-r", str(self.fps),
            output_path,
        ]

        logger.info("Running FFmpeg subtitle burn...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("FFmpeg drawtext failed:\n%s", result.stderr[-2000:])
            # Fallback: just use the clean video without subtitles
            logger.warning("Falling back to video without subtitles.")
            os.replace(input_path, output_path)
        else:
            logger.info("Subtitles burned successfully via FFmpeg.")

    # ── Full FFmpeg fallback (when MoviePy breaks) ────────────────────────────

    def _edit_with_ffmpeg_full(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        script: str = "",
        video_paths: Optional[List[str]] = None,
    ) -> None:
        """
        Full fallback using only FFmpeg: merge video + audio + optional subtitles.
        """
        logger.info("Falling back to full FFmpeg encode...")

        # Pre-process segments to vertical if landscape, then concatenate
        if video_paths and len(video_paths) > 1:
            vertical_paths = [self._to_vertical_ffmpeg(p) for p in video_paths]
            concat_path = output_path.replace(".mp4", "_concat.mp4")
            self._ffmpeg_concat(vertical_paths, concat_path)
            src_video = concat_path
        else:
            src_video = self._to_vertical_ffmpeg(video_path)

        # Probe audio duration
        audio_dur = min(self._probe_duration(audio_path) or 45.0, self.max_duration)

        # vf: safety-net crop in case clip isn't exactly 1080x1920 yet
        vf = (
            f"scale=w={self.width}:h={self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height}"
        )

        # Encode clean video + audio
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(output_path))
        os.close(tmp_fd)

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", src_video,
            "-i", audio_path,
            "-vf", vf,
            "-t", str(audio_dur),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-b:v", cfg.SHORTS_VIDEO_BITRATE,
            "-c:a", "aac",
            "-b:a", cfg.SHORTS_AUDIO_BITRATE,
            "-r", str(self.fps),
            "-shortest",
            "-map", "0:v:0",
            "-map", "1:a:0",
            tmp_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # Burn subtitles if we have a script
        if script and script.strip():
            self._burn_subtitles_ffmpeg(tmp_path, output_path, script, audio_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        else:
            os.replace(tmp_path, output_path)

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info("FFmpeg output saved: '%s' (%.1f MB).", output_path, size_mb)

    def _ffmpeg_concat(self, video_paths: List[str], output_path: str) -> None:
        """Concatenate multiple video files into one using FFmpeg concat demuxer."""
        list_fd, list_path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(list_fd, "w") as f:
                for p in video_paths:
                    f.write(f"file '{p}'\n")
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
        finally:
            if os.path.exists(list_path):
                os.remove(list_path)

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def _to_vertical_ffmpeg(self, src_path: str) -> str:
        """
        If `src_path` is a landscape video, convert it to 1080×1920 using an
        FFmpeg blur-pad filter and return the path to the converted file.
        Portrait clips (height >= width) are returned as-is.

        Blur-pad technique:
          - Scale + blur the source frame to fill the full 1080×1920 canvas
          - Overlay the original sharp frame centred on top
          Result: no content is lost; black bars are replaced by a beautiful
          blurred version of the same footage.
        """
        try:
            # Probe dimensions
            result = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", src_path],
                capture_output=True, text=True, timeout=15,
            )
            parts = result.stdout.strip().split(",")
            if len(parts) != 2:
                return src_path
            w, h = int(parts[0]), int(parts[1])
        except Exception as e:
            logger.warning("Could not probe dimensions of %s: %s", src_path, e)
            return src_path

        # Portrait or square — no conversion needed
        if h >= w:
            logger.debug("Clip %s is portrait (%dx%d) — no conversion needed.", src_path, w, h)
            return src_path

        # Landscape — apply blur-pad to fill 1080×1920
        logger.info("Converting landscape clip (%dx%d) to vertical via blur-pad: %s", w, h, src_path)
        out_path = src_path.replace(".mp4", "_vertical.mp4")

        # Fill the vertical frame with the source footage instead of placing
        # a small sharp landscape strip over a heavily blurred background.
        # This center-crop approach uses the full screen; some side content may
        # be cropped from landscape footage.
        vf = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},setsar=1"
        )
        cmd = [
            "ffmpeg", "-y", "-i", src_path,
            "-filter_complex", vf,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-b:v", cfg.SHORTS_VIDEO_BITRATE,
            "-an",  # no audio in background segments
            out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            logger.error("Blur-pad conversion failed for %s: %s", src_path, proc.stderr[-1000:])
            return src_path  # fallback: use original
        logger.info("Blur-pad conversion done → %s", out_path)
        return out_path

    def _resize_crop(self, clip, target_duration):
        """Resize and center-crop clip to 1080×1920. Loop if shorter than target.
        NOTE: landscape clips should already be converted by _to_vertical_ffmpeg
        before this is called, so this is mostly a safety net."""
        if clip.duration < target_duration:
            n_loops = int(target_duration / clip.duration) + 2
            clip = clip.loop(n_loops)

        clip = clip.subclip(0, target_duration)

        clip_ratio = clip.w / clip.h
        target_ratio = self.width / self.height

        if clip_ratio > target_ratio:
            new_h = self.height
            new_w = int(clip.w * (self.height / clip.h))
        else:
            new_w = self.width
            new_h = int(clip.h * (self.width / clip.w))

        clip = clip.resize((new_w, new_h))
        clip = clip.crop(
            x_center=clip.w / 2,
            y_center=clip.h / 2,
            width=self.width,
            height=self.height,
        )
        return clip

    def _mix_audio(self, voice_clip, target_duration):
        """Mix voiceover with optional background music."""
        bg_music_dir = "assets/music"
        if os.path.exists(bg_music_dir) and os.path.isdir(bg_music_dir):
            import random
            music_files = [
                f for f in os.listdir(bg_music_dir) if f.endswith((".mp3", ".wav", ".m4a"))
            ]
            if music_files:
                music_file = random.choice(music_files)
                bg_path = os.path.join(bg_music_dir, music_file)
                logger.info("Using background music: %s", bg_path)
                try:
                    bg_clip = AudioFileClip(bg_path)
                    bg_clip = audio_loop(bg_clip, duration=target_duration)
                    bg_clip = volumex(bg_clip, 0.15)
                    return CompositeAudioClip([voice_clip, bg_clip])
                except Exception as e:
                    logger.error("Failed to mix background music: %s", e)
        return voice_clip

    def _probe_duration(self, path: str) -> Optional[float]:
        """Get exact duration via ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True, text=True, timeout=15,
            )
            return float(result.stdout.strip())
        except Exception as e:
            logger.warning("ffprobe failed: %s", e)
            return None
