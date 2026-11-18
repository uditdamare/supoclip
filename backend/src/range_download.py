"""Download only a time range of a YouTube, Twitch or Kick video.

A 5-10 hour stream recording cannot be fetched whole for every batch, so a task
with a ``source_range`` downloads just ``[start, end]`` with yt-dlp's range
support (ffmpeg reads that part over the network) into one local file. The rest
of the pipeline then treats that file like an upload: it starts at 0:00, clip
times are relative to it, and the duration limit sees the range's own length.

Cuts land on the nearest keyframe, so a range may begin a few seconds early.
Ranged tasks go out directly: ``YOUTUBE_PROXY_URL`` is not used here because
ffmpeg cannot route SOCKS or authenticated proxies.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yt_dlp
from yt_dlp.utils import download_range_func

from .config import get_config
from .media.ffmpeg import ffprobe_duration
from .source_ranges import Range, format_timestamp
from .youtube_utils import YouTubeDownloader, get_youtube_video_id

logger = logging.getLogger(__name__)

_VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".mov")
# yt-dlp stops at the video's real end, so a range past it comes back short.
_MIN_RETURNED_FRACTION = 0.6
_MIN_RETURNED_SECONDS = 5.0
_ATTEMPTS = 2


class RangeDownloadError(Exception):
    """The requested part of the source could not be downloaded."""


def range_work_dir(task_id: Optional[str]) -> Path:
    return Path(get_config().temp_dir) / "range-sources" / (task_id or "adhoc")


def _options(url: str, source_type: str, source_range: Range, output: Path, max_height: Optional[int]) -> Dict[str, Any]:
    start, end = source_range
    if source_type == "youtube":
        video_id = get_youtube_video_id(url)
        if not video_id:
            raise RangeDownloadError("Could not read the YouTube video id from this link.")
        opts = YouTubeDownloader().get_optimal_download_options(video_id, max_height)
    else:
        height = max_height or get_config().youtube_max_height
        opts = {
            "format": f"best[height<={height}]/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "overwrites": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 5,
            "fragment_retries": 5,
        }
    opts["outtmpl"] = str(output / "range.%(ext)s")
    opts["download_ranges"] = download_range_func(None, [(start, end)])
    # Stream-copy cut: fast and light. The start may be a few seconds early.
    opts["force_keyframes_at_cuts"] = False
    # max_filesize would compare the whole stream's size, not the range's.
    opts.pop("max_filesize", None)
    return opts


def _find_output(output: Path) -> Optional[Path]:
    candidates = [path for path in output.glob("range.*") if path.is_file() and path.suffix.lower() in _VIDEO_SUFFIXES]
    return max(candidates, key=lambda path: path.stat().st_size) if candidates else None


def _reusable(output: Path, url: str, source_range: Range) -> Optional[Path]:
    """A finished download of this exact range from an earlier attempt."""
    marker = output / "range.json"
    try:
        saved = json.loads(marker.read_text())
    except (OSError, ValueError):
        return None
    if saved.get("url") != url or saved.get("range") != [source_range[0], source_range[1]]:
        return None
    path = _find_output(output)
    return path if path is not None and path.stat().st_size > 0 else None


def download_source_range(
    url: str,
    source_type: str,
    source_range: Range,
    task_id: Optional[str] = None,
    *,
    max_height: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> Path:
    """Fetch ``source_range`` of ``url`` into ``TEMP_DIR/range-sources/<task>/`` and return the file."""
    config = get_config()
    if getattr(config, "youtube_proxy_url", ""):
        logger.warning("Ranged downloads go out directly; YOUTUBE_PROXY_URL is not used for them")
    start, end = source_range
    wanted = end - start
    output = range_work_dir(task_id)

    reused = _reusable(output, url, source_range)
    if reused is not None:
        logger.info("Reusing the earlier download of %s-%s: %s", format_timestamp(start), format_timestamp(end), reused.name)
        return reused

    last_error: Optional[Exception] = None
    for attempt in range(1, _ATTEMPTS + 1):
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        began = time.monotonic()
        try:
            logger.info(
                "Downloading %s - %s (%s) of %s, attempt %s/%s",
                format_timestamp(start), format_timestamp(end), source_type, source_type, attempt, _ATTEMPTS,
            )
            with yt_dlp.YoutubeDL(_options(url, source_type, source_range, output, max_height)) as ydl:
                ydl.download([url])
            path = _find_output(output)
            if path is None:
                raise RangeDownloadError("The download finished but produced no video file.")
            got = ffprobe_duration(path)
            if got < max(_MIN_RETURNED_SECONDS, wanted * _MIN_RETURNED_FRACTION):
                raise RangeDownloadError(
                    f"Only {format_timestamp(got)} of video came back for the range "
                    f"{format_timestamp(start)}-{format_timestamp(end)}. Check that the range is inside the video."
                )
            size = path.stat().st_size
            if max_bytes and size > max_bytes:
                shutil.rmtree(output, ignore_errors=True)
                raise RangeDownloadError(
                    f"This section is {size / 1e9:.1f} GB, above the {max_bytes / 1e9:.1f} GB download limit. "
                    "Use a shorter range or raise YOUTUBE_DOWNLOAD_MAX_BYTES."
                )
            (output / "range.json").write_text(json.dumps({"url": url, "range": [start, end]}))
            logger.info(
                "Downloaded range: %s (%.0f MB, %.0fs of video) in %.0fs",
                path.name, size / 1e6, got, time.monotonic() - began,
            )
            return path
        except RangeDownloadError:
            raise
        except Exception as exc:  # yt-dlp raises DownloadError and friends
            last_error = exc
            logger.warning("Range download attempt %s failed: %s", attempt, str(exc)[:300])
            time.sleep(2 * attempt)
    raise RangeDownloadError(
        f"Could not download that part of the video ({str(last_error)[:200]}). "
        "Check the link and the time range, then try again."
    )
