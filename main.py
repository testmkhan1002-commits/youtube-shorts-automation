"""
main.py — YouTube Shorts Automation Orchestrator

FIXED PIPELINE (audio/video sync):
  OLD: Topic → Script → Voice → Download Video → Edit
  NEW: Topic → Download Video → [get duration] → Script (fitted to duration) → Voice → Edit

By writing the script AFTER knowing the video duration, gTTS audio length
matches the video length exactly — no more sync issues.

Run locally:   python main.py
Run in CI:     Triggered automatically by GitHub Actions cron
"""

import logging
import sys
import traceback

from config import Config
from modules.topic_generator import TopicGenerator
from modules.script_generator import ScriptGenerator
from modules.voice_generator import VoiceGenerator
from modules.video_downloader import VideoDownloader
from modules.video_editor import VideoEditor
from modules.metadata_generator import MetadataGenerator
from modules.youtube_uploader import YouTubeUploader
from modules.youtube_analytics import YouTubeAnalytics
from modules.keyword_extractor import KeywordExtractor

# ── Root logger ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


def run_pipeline(cfg: Config) -> None:
    """Execute the full YouTube Shorts generation and upload pipeline."""

    logger.info("=" * 60)
    logger.info("🚀 Starting YouTube Shorts automation pipeline")
    logger.info("=" * 60)

    # ── Step 0: Analytics & Category Management ──────────────────────────────
    logger.info("\n📊 STEP 0/7 — Analyzing data & selecting category...")

    import os
    # Force space category — pipeline is space-only
    selected_category = "space"
    logger.info("Category locked to: %s", selected_category)

    analytics = YouTubeAnalytics(
        client_id=cfg.youtube_client_id,
        client_secret=cfg.youtube_client_secret,
        refresh_token=cfg.youtube_refresh_token,
    )
    try:
        best_series_id = analytics.update_analytics()
        if best_series_id:
            logger.info("Analytics suggests '%s' performs best.", best_series_id)
    except Exception as e:
        logger.warning("Step 0 (Analytics) failed, but continuing pipeline: %s", e)
        best_series_id = None

    # ── Step 1: Generate Topic ────────────────────────────────────────────────
    logger.info("\n📌 STEP 1/7 — Generating space topic...")
    topic_gen = TopicGenerator()
    topic = topic_gen.generate(category=selected_category)
    logger.info("Topic: %s", topic)

    # ── Step 1.5: Extract Keywords ────────────────────────────────────────────
    logger.info("\n🔑 STEP 1.5/7 — Extracting keywords...")
    keyword_extractor = KeywordExtractor()
    keywords = keyword_extractor.extract(topic)
    logger.info("Keywords: %s", keywords)

    # ── Step 2: Download Space Video FIRST ───────────────────────────────────
    #
    # 🔧 SYNC FIX: Video is downloaded before the script is written.
    #    download_space_video() returns the actual video duration AND a
    #    description of the footage so the script can be sized and themed
    #    to match exactly — eliminating A/V sync and relevance issues.
    #
    logger.info("\n🎬 STEP 2/7 — Downloading space background video...")
    video_dl = VideoDownloader(api_key=cfg.pixabay_api_key)
    video_path, video_duration, video_description = video_dl.download_space_video(topic=topic)
    logger.info("Video ready: %s — duration: %.2fs", video_path, video_duration)
    logger.info("Video description: %s", video_description[:100] if video_description else "N/A")

    # ── Step 3: Generate Script FITTED to video duration ─────────────────────
    #
    # 🔧 SYNC FIX: video_duration is passed to generate() so the LLM writes
    #    a script whose spoken length (at gTTS speed) ≈ video_duration.
    # 🔧 VIDEO CONTEXT: video_description is passed so the narration is
    #    relevant to what is actually shown in the footage.
    #
    logger.info("\n✍️  STEP 3/7 — Generating script fitted to %.1fs video...", video_duration)
    script_gen = ScriptGenerator(api_key=cfg.openrouter_api_key)
    script = script_gen.generate(
        topic=topic,
        video_duration=video_duration,
        video_description=video_description,
    )
    logger.info("Script preview: %s...", script[:120])
    logger.info("Script word count: %d", len(script.split()))

    # ── Step 4: Generate Voiceover ─────────────────────────────────────────
    logger.info("\n🎙️  STEP 4/7 — Generating voiceover...")
    voice_gen = VoiceGenerator(language=cfg.tts_language, slow=cfg.tts_slow)
    voice_path = voice_gen.generate(script)
    logger.info("Voiceover saved: %s", voice_path)

    # ── Step 5: Download Additional Segments (for variety in the edit) ───────
    logger.info("\n🎞️  STEP 5a/7 — Downloading additional video segments...")
    try:
        video_paths = video_dl.download_segments(topic=topic, script=script, n_segments=3)
        # Make sure the primary video is always first
        if video_path not in video_paths:
            video_paths.insert(0, video_path)
        logger.info("Using %d video segment(s).", len(video_paths))
    except Exception as e:
        logger.warning("Segment download failed (%s) — using single video.", e)
        video_paths = [video_path]

    # ── Step 5b: Edit + Combine into Final Short ──────────────────────────────
    logger.info("\n🎞️  STEP 5b/7 — Editing video (crop → subtitles → merge audio)...")
    editor = VideoEditor()
    final_video_path = editor.edit(
        video_path=video_paths[0],
        audio_path=voice_path,
        script=script,
        topic=topic,
        video_paths=video_paths,
    )
    logger.info("Final video saved: %s", final_video_path)

    # ── Step 6: Generate Metadata ─────────────────────────────────────────────
    logger.info("\n📝 STEP 6/7 — Generating title, description, and tags...")
    meta_gen = MetadataGenerator(api_key=cfg.openrouter_api_key)
    metadata = meta_gen.generate(topic=topic, script=script)
    logger.info("Title: %s", metadata["title"])
    logger.info("Tags:  %s", metadata["tags"])

    # ── Step 7: Upload to YouTube ─────────────────────────────────────────────
    logger.info("\n📤 STEP 7/7 — Uploading to YouTube...")
    uploader = YouTubeUploader(
        client_id=cfg.youtube_client_id,
        client_secret=cfg.youtube_client_secret,
        refresh_token=cfg.youtube_refresh_token,
    )
    video_id = uploader.upload(
        video_path=final_video_path,
        title=metadata["title"],
        description=metadata["description"],
        tags=metadata["tags"],
        category_id=cfg.video_category_id,
        privacy_status=cfg.video_privacy,
    )

    logger.info("\n" + "=" * 60)
    logger.info("✅ Pipeline complete!")
    logger.info("🎉 YouTube Short published: https://www.youtube.com/watch?v=%s", video_id)
    logger.info("=" * 60)


def main():
    """Entry point — load config, run pipeline, handle top-level errors."""
    try:
        cfg = Config()
        run_pipeline(cfg)
        sys.exit(0)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error("❌ Pipeline failed with an unhandled error!")
        logger.error("Error: %s", str(e))
        logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
