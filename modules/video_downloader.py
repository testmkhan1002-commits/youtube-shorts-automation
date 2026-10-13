
"""
modules/video_downloader.py
Downloads space-related stock videos from Pixabay.
"""

import json
import logging
import os
import random
import re
import subprocess
import requests
from typing import Optional, List, Tuple

import config as cfg
from modules.keyword_extractor import KeywordExtractor

logger = logging.getLogger(__name__)

PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
MIN_DURATION = 15
MAX_DURATION = 70
REQUEST_TIMEOUT = 30
TOP_N_RANDOM = 5

USED_IDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "data",
    "used_video_ids.json",
)
MAX_USED_IDS = 100

SPACE_POSITIVE_KEYWORDS = {
    "space", "galaxy", "galaxies", "nebula", "nebulae", "star", "stars",
    "planet", "planets", "cosmos", "universe", "astronaut", "milky",
    "milky-way", "milkyway", "black-hole", "blackhole", "supernova",
    "telescope", "solar", "moon", "sun", "mars", "jupiter", "saturn",
    "earth-orbit", "astronomer", "astronomy", "rocket", "nasa", "spacex",
    "comet", "asteroid", "meteor", "night-sky", "nightsky", "timelapse",
    "time-lapse", "cosmic", "orbit", "spacecraft", "satellite", "hubble",
    "stellar", "astrophoto", "astrophotography", "aurora", "constellation",
    "exoplanet", "quasar", "pulsar", "interstellar", "deep-space",
    "space-exploration", "launch", "observatory",
}

NEGATIVE_KEYWORDS = {
    "child", "children", "kid", "kids", "baby", "babies", "toddler",
    "woman", "man", "people", "person", "girl", "boy", "family",
    "beach", "ocean", "sea", "forest", "mountain", "city", "urban",
    "food", "cook", "cooking", "kitchen", "restaurant", "coffee",
    "dog", "cat", "animal", "bird", "horse", "fish",
    "wedding", "yoga", "gym", "fitness", "dance", "sport", "football",
    "fashion", "makeup", "hair",
}

SPACE_FALLBACK_QUERIES = [
    "galaxy nebula space",
    "stars cosmos universe",
    "planet solar system",
    "astronaut space exploration",
    "milky way night sky",
    "black hole astronomy",
    "space timelapse stars",
    "nebula astrophotography",
]


class VideoDownloader:
    """Downloads space videos from Pixabay, portrait or landscape."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("PIXABAY_API_KEY is required for video download.")
        self.api_key = api_key
        self.extractor = KeywordExtractor()
        self._used_ids = self._load_used_ids()

    def download_space_video(
        self,
        topic: str,
        output_path: str = cfg.VIDEO_RAW_PATH,
    ) -> Tuple[str, float, str]:
        keywords = self.extractor.extract(topic)
        logger.info("Pixabay search keywords: %s", keywords)

        queries = []
        if len(keywords) >= 2:
            queries.append(f"{keywords[0]} {keywords[1]} space")
        for keyword in keywords:
            queries.extend([f"{keyword} space", f"{keyword} astronomy"])
        queries.extend(SPACE_FALLBACK_QUERIES)

        queries = list(dict.fromkeys(queries))
        selected = None

        # First pass: require space-related metadata.
        for query in queries:
            videos = self._search_pixabay(query)
            selected = self._pick_best_video(videos or [], require_space=True)
            if selected:
                break

        # Second pass: relax the positive space-keyword requirement.
        if not selected:
            logger.warning("No strict space match; relaxing positive keyword filter.")
            for query in queries:
                videos = self._search_pixabay(query)
                selected = self._pick_best_video(videos or [], require_space=False)
                if selected:
                    break

        if not selected:
            raise RuntimeError("No suitable Pixabay video found after all search queries.")

        video_url, api_duration, description, video_id = selected

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        self._stream_download(video_url, output_path)

        if video_id is not None:
            self._mark_used(video_id)

        real_duration = self._probe_duration(output_path) or api_duration
        real_duration = min(real_duration, cfg.SHORTS_MAX_DURATION)

        logger.info("Video saved: %s (%.2fs)", output_path, real_duration)
        return output_path, real_duration, description

    def download_segments(
        self,
        topic: str,
        script: str,
        n_segments: int = 3,
    ) -> List[str]:
        sentences = re.split(
            r"(?<=[.!?])\s+",
            re.sub(r"\s+", " ", script.strip()),
        )
        sentences = [sentence for sentence in sentences if sentence.strip()]

        if not sentences:
            path, _, _ = self.download_space_video(topic)
            return [path]

        n_segments = max(1, n_segments)
        segment_texts = []
        for i in range(n_segments):
            start = i * len(sentences) // n_segments
            end = (i + 1) * len(sentences) // n_segments
            if start < end:
                segment_texts.append(" ".join(sentences[start:end]))

        video_paths = []
        for i, segment in enumerate(segment_texts):
            segment_path = os.path.join(cfg.OUTPUT_DIR, f"segment_{i}.mp4")
            try:
                segment_topic = f"{topic} {' '.join(segment.split()[:4])}"
                path, _, _ = self.download_space_video(segment_topic, segment_path)
                video_paths.append(path)
            except Exception as exc:
                logger.error("Segment %d download failed: %s", i, exc)

        if not video_paths:
            logger.warning("All segments failed; falling back to one video.")
            path, _, _ = self.download_space_video(topic)
            return [path]

        return video_paths

    def download(
        self,
        topic: str,
        script: str = None,
        output_path: str = cfg.VIDEO_RAW_PATH,
    ) -> str:
        path, _, _ = self.download_space_video(topic, output_path)
        return path

    def _search_pixabay(self, query: str) -> Optional[list]:
        """Search Pixabay and normalize its response for the selector."""
        params = {
            "key": self.api_key,
            "q": query,
            "per_page": 30,
            "safesearch": "true",
        }

        try:
            response = requests.get(
                PIXABAY_SEARCH_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            normalized = []
            for hit in data.get("hits", []):
                renditions = hit.get("videos") or {}
                video_files = []

                quality_order = [
                    ("large", "hd"),
                    ("medium", "sd"),
                    ("small", "sd"),
                    ("tiny", "sd"),
                ]

                for rendition_name, quality in quality_order:
                    rendition = renditions.get(rendition_name) or {}
                    url = rendition.get("url")
                    if not url:
                        continue
                    video_files.append({
                        "quality": quality,
                        "link": url,
                        "width": rendition.get("width", 0),
                        "height": rendition.get("height", 0),
                    })

                if not video_files:
                    continue

                # Use the best available rendition for orientation scoring.
                best_file = video_files[0]
                normalized.append({
                    "id": hit.get("id"),
                    "duration": hit.get("duration", 0),
                    "width": best_file.get("width", 0),
                    "height": best_file.get("height", 0),
                    "alt": hit.get("tags", ""),
                    "url": hit.get("pageURL", ""),
                    "user": {"name": hit.get("user", "")},
                    "video_files": video_files,
                })

            return normalized or None

        except Exception as exc:
            logger.error("Pixabay API error for query '%s': %s", query, exc)
            return None

    def _extract_text_fields(self, video: dict) -> str:
        tags = (video.get("alt") or "").lower()
        page_url = (video.get("url") or "").lower()
        username = (video.get("user", {}).get("name") or "").lower()
        return f"{tags} {page_url} {username}"

    def _is_space_related(self, combined: str) -> bool:
        return any(keyword in combined for keyword in SPACE_POSITIVE_KEYWORDS)

    def _has_negative_keyword(self, combined: str) -> bool:
        return any(keyword in combined for keyword in NEGATIVE_KEYWORDS)

    def _pick_best_video(
        self,
        videos: list,
        require_space: bool = True,
    ) -> Optional[Tuple[str, float, str, int]]:
        scored = []

        for video in videos:
            video_id = video.get("id")
            if video_id is not None and str(video_id) in self._used_ids:
                continue

            combined = self._extract_text_fields(video)

            if self._has_negative_keyword(combined):
                continue
            if require_space and not self._is_space_related(combined):
                continue

            duration = float(video.get("duration") or 0)
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)

            score = 0
            if 20 <= duration <= 59:
                score += 1
            elif duration < MIN_DURATION or duration > MAX_DURATION:
                score -= 1
            if height > width:
                score += 3

            files = video.get("video_files", [])
            best_url = None
            best_quality = None

            for preferred in ("hd", "sd"):
                for video_file in files:
                    if video_file.get("quality") == preferred:
                        best_url = video_file.get("link")
                        best_quality = preferred
                        break
                if best_url:
                    break

            if not best_url and files:
                best_url = files[0].get("link")
                best_quality = files[0].get("quality")

            if not best_url:
                continue

            if best_quality == "hd":
                score += 2
            elif best_quality == "sd":
                score += 1

            tags = (video.get("alt") or "").strip()
            username = video.get("user", {}).get("name", "")
            description = tags or f"Space video by {username}"

            scored.append((
                score,
                duration,
                best_url,
                description,
                video_id,
            ))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        chosen = random.choice(scored[:min(TOP_N_RANDOM, len(scored))])
        score, duration, url, description, video_id = chosen

        logger.info(
            "Selected Pixabay video id=%s score=%d duration=%.1fs",
            video_id,
            score,
            duration,
        )
        return url, duration, description, video_id

    def _probe_duration(self, path: str) -> Optional[float]:
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            return float(result.stdout.strip())
        except Exception as exc:
            logger.warning("ffprobe failed: %s", exc)
            return None

    def _stream_download(self, url: str, output_path: str) -> None:
        logger.info("Downloading video from Pixabay...")
        with requests.get(url, stream=True, timeout=90) as response:
            response.raise_for_status()
            with open(output_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        file.write(chunk)
        logger.info("Video saved to %s", output_path)

    def _load_used_ids(self) -> set:
        try:
            if os.path.exists(USED_IDS_PATH):
                with open(USED_IDS_PATH, "r", encoding="utf-8") as file:
                    data = json.load(file)
                ids = {str(item) for item in data.get("used_ids", [])}
                logger.info("Loaded %d previously used video IDs.", len(ids))
                return ids
        except Exception as exc:
            logger.warning("Could not load used video history: %s", exc)
        return set()

    def _mark_used(self, video_id) -> None:
        self._used_ids.add(str(video_id))
        try:
            os.makedirs(os.path.dirname(USED_IDS_PATH), exist_ok=True)
            current = list(self._used_ids)[-MAX_USED_IDS:]
            self._used_ids = set(current)

            with open(USED_IDS_PATH, "w", encoding="utf-8") as file:
                json.dump({"used_ids": current}, file, indent=2)

            logger.info("Saved used video ID %s.", video_id)
        except Exception as exc:
            logger.warning("Could not save used video history: %s", exc)
