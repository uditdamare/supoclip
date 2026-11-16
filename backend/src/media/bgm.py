"""Optional background music under generated clips (local self-host add-on).

Off unless ``BGM_DIR`` (default ``/app/bgm``) contains audio files. One track
is picked per clip and mixed quietly under the clip's own audio; the video
stream is copied, so this adds seconds, not a re-encode. A failed mix leaves
the clip untouched.
"""
import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional

from .common import AUDIO_BITRATE
from .ffmpeg import NO_CHAPTERS, ffprobe_duration, ffprobe_has_audio, run_ffmpeg_command

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}
DEFAULT_VOLUME = 0.12
FADE_SECONDS = 1.0


def bgm_tracks() -> List[Path]:
    directory = Path(os.getenv("BGM_DIR", "/app/bgm"))
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    )


def bgm_volume() -> float:
    try:
        volume = float(os.getenv("BGM_VOLUME", "") or DEFAULT_VOLUME)
    except ValueError:
        volume = DEFAULT_VOLUME
    return min(max(volume, 0.0), 1.0)


def pick_track(tracks: List[Path], seed: str) -> Optional[Path]:
    """Stable per clip (a re-render keeps its track), varied across clips."""
    if not tracks:
        return None
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return tracks[int.from_bytes(digest[:4], "big") % len(tracks)]


def mix_background_music(clip_path: Path, seed: str) -> bool:
    """Mix a track from ``BGM_DIR`` into ``clip_path`` in place.

    Returns whether the file changed. Never raises.
    """
    try:
        volume = bgm_volume()
        track = pick_track(bgm_tracks(), seed)
        if track is None or volume <= 0:
            return False
        duration = ffprobe_duration(clip_path)
        if duration <= 0:
            return False

        fade_start = max(0.0, duration - FADE_SECONDS)
        music = (
            f"[1:a]aresample=48000,volume={volume:.3f},"
            f"afade=t=out:st={fade_start:.3f}:d={FADE_SECONDS}[bgm]"
        )
        if ffprobe_has_audio(clip_path):
            # normalize=0 keeps the clip's own (already loudness-normalised)
            # audio at full level; the limiter only catches summed peaks.
            graph = (
                f"{music};[0:a][bgm]amix=inputs=2:duration=first:"
                "dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
            )
        else:
            graph = f"{music};[bgm]anull[a]"

        mixed_path = clip_path.with_name(f".{clip_path.stem}.bgm{clip_path.suffix}")
        command = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(clip_path),
            "-stream_loop", "-1", "-i", str(track),
            "-filter_complex", graph,
            "-map", "0:v:0", "-map", "[a]",
            *NO_CHAPTERS,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000",
            "-t", f"{duration:.3f}",
            "-movflags", "+faststart",
            str(mixed_path),
        ]
        result = run_ffmpeg_command(command, timeout=300)
        if result.returncode != 0 or not mixed_path.exists():
            mixed_path.unlink(missing_ok=True)
            logger.warning("Background music mix failed; keeping the clip without it")
            return False
        mixed_path.replace(clip_path)
        logger.info("Mixed background music '%s' at volume %.2f", track.name, volume)
        return True
    except Exception as exc:
        logger.warning("Background music mix failed: %s", exc)
        return False
