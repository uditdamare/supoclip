"""Time ranges on a task: which part of a source to process, and which parts to skip.

Two separate ideas share one parser:

* ``source_range``: one ``(start, end)`` on the source's own timeline. Only that
  part is downloaded, transcribed and analysed (a 10-hour stream can be done in
  70-minute batches). Clip times are then relative to the start of the batch.
* ``skip_ranges``: spans, on the source's own timeline, that must never become
  clip content (waiting time, intermissions). They are removed from the
  transcript the model reads, and any chosen clip touching one is dropped.

Everything here is pure and has no I/O, so it is cheap to unit test.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Range = Tuple[float, float]

MAX_SKIP_RANGES = 200
MIN_SOURCE_RANGE_SECONDS = 30.0
# A clip that only grazes a skipped span (rounding, a word boundary) is kept.
SEGMENT_OVERLAP_TOLERANCE_SECONDS = 0.5

SOURCE_RANGE_CODE = "INVALID_SOURCE_RANGE"
SKIP_RANGES_CODE = "INVALID_SKIP_RANGES"

_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_UNITS = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$", re.IGNORECASE)
_RANGE_SEPARATOR = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)
_LIST_SEPARATOR = re.compile(r"[,;\n]+")
_TRANSCRIPT_LINE = re.compile(
    r"^\[(\d+:\d{2}(?::\d{2})?)\s*-\s*(\d+:\d{2}(?::\d{2})?)\]"
)


class SourceRangeError(ValueError):
    """A range the caller sent is unusable. ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_timestamp(value: Any, *, code: str = SOURCE_RANGE_CODE) -> float:
    """Seconds from ``754``, ``"12:34"``, ``"1:02:03"``, ``"754"`` or ``"1h2m3s"``."""

    def bad() -> SourceRangeError:
        return SourceRangeError(code, f"'{value}' is not a valid time. Use 1:02:03, 62:03 or 1h2m3s.")

    if isinstance(value, bool):
        raise bad()
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise bad()
        if ":" in text:
            parts = text.split(":")
            if len(parts) > 3 or any(not _NUMBER.match(part) for part in parts):
                raise bad()
            numbers = [float(part) for part in parts]
            # Seconds (and minutes, when hours are given) stay below 60;
            # a leading minutes value may exceed 59 (90:00 is 90 minutes).
            if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[1] >= 60):
                raise bad()
            seconds = 0.0
            for number in numbers:
                seconds = seconds * 60 + number
        elif _NUMBER.match(text):
            seconds = float(text)
        else:
            match = _UNITS.match(text.replace(" ", ""))
            if not match or not any(match.groups()):
                raise bad()
            hours, minutes, secs = (float(group or 0) for group in match.groups())
            seconds = hours * 3600 + minutes * 60 + secs
    else:
        raise bad()
    if not math.isfinite(seconds) or seconds < 0:
        raise bad()
    return seconds


def parse_range(value: Any, *, code: str = SOURCE_RANGE_CODE) -> Range:
    """One ``(start, end)`` from ``"12:30-18:45"``, ``[a, b]`` or ``{"start", "end"}``."""
    if isinstance(value, dict):
        if "start" not in value or "end" not in value:
            raise SourceRangeError(code, "A time range needs both a start and an end.")
        start, end = value["start"], value["end"]
    elif isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise SourceRangeError(code, "A time range needs exactly a start and an end.")
        start, end = value
    elif isinstance(value, str):
        pieces = _RANGE_SEPARATOR.split(value.strip(), maxsplit=1)
        if len(pieces) != 2 or not pieces[0] or not pieces[1]:
            raise SourceRangeError(code, f"'{value}' is not a range. Write it like 12:30-18:45.")
        start, end = pieces
    else:
        raise SourceRangeError(code, "A time range must be text like 12:30-18:45.")
    start_seconds = parse_timestamp(start, code=code)
    end_seconds = parse_timestamp(end, code=code)
    if end_seconds <= start_seconds:
        raise SourceRangeError(
            code,
            f"The range {format_timestamp(start_seconds)}-{format_timestamp(end_seconds)} "
            "ends before it starts.",
        )
    return start_seconds, end_seconds


def parse_source_range(raw: Any) -> Optional[Range]:
    """The part of the source to process, or ``None`` for the whole video."""
    if raw is None or raw == "" or raw == {} or raw == []:
        return None
    start, end = parse_range(raw, code=SOURCE_RANGE_CODE)
    if end - start < MIN_SOURCE_RANGE_SECONDS:
        raise SourceRangeError(
            SOURCE_RANGE_CODE,
            f"The time range must be at least {int(MIN_SOURCE_RANGE_SECONDS)} seconds long.",
        )
    return start, end


def parse_skip_ranges(raw: Any) -> List[Range]:
    """Merged, sorted skip spans from text (``"12:30-18:45, 1:02:00-1:10:00"``) or a list."""
    if raw is None or raw == "" or raw == []:
        return []
    if isinstance(raw, str):
        items: Sequence[Any] = [part.strip() for part in _LIST_SEPARATOR.split(raw) if part.strip()]
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        raise SourceRangeError(
            SKIP_RANGES_CODE, "Skip timestamps must be a list of ranges like 12:30-18:45."
        )
    if len(items) > MAX_SKIP_RANGES:
        raise SourceRangeError(SKIP_RANGES_CODE, f"At most {MAX_SKIP_RANGES} skip ranges are allowed.")
    return normalize_ranges(parse_range(item, code=SKIP_RANGES_CODE) for item in items)


def normalize_ranges(ranges: Iterable[Range]) -> List[Range]:
    """Sorted, with overlapping or touching spans merged."""
    ordered = sorted((float(start), float(end)) for start, end in ranges if end > start)
    merged: List[Range] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def to_local(ranges: Iterable[Range], window_start: float, window_end: float) -> List[Range]:
    """Skip spans on the source timeline, as seen from a file that starts at ``window_start``.

    Spans outside the window drop out; spans crossing its edges are clipped.
    """
    local: List[Range] = []
    for start, end in ranges:
        clipped_start, clipped_end = max(start, window_start), min(end, window_end)
        if clipped_end > clipped_start:
            local.append((clipped_start - window_start, clipped_end - window_start))
    return local


def overlaps(
    start: float,
    end: float,
    ranges: Iterable[Range],
    tolerance: float = SEGMENT_OVERLAP_TOLERANCE_SECONDS,
) -> bool:
    return any(min(end, skip_end) - max(start, skip_start) > tolerance for skip_start, skip_end in ranges)


def format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def label_to_seconds(label: Any) -> Optional[float]:
    """Seconds from a clip label such as ``"07:09"`` or ``"1:02:03"``; ``None`` if unreadable."""
    try:
        return parse_timestamp(label)
    except SourceRangeError:
        return None


def filter_transcript_text(transcript: str, skip_ranges: Sequence[Range]) -> str:
    """Drop transcript lines (``[MM:SS - MM:SS] text``) that touch a skipped span."""
    if not skip_ranges or not transcript:
        return transcript
    kept: List[str] = []
    for line in transcript.splitlines():
        match = _TRANSCRIPT_LINE.match(line.strip())
        if match:
            start, end = label_to_seconds(match.group(1)), label_to_seconds(match.group(2))
            if start is not None and end is not None and overlaps(start, end, skip_ranges, 0.25):
                continue
        kept.append(line)
    return "\n".join(kept)


def filter_segments(
    segments: Sequence[Dict[str, Any]], skip_ranges: Sequence[Range]
) -> List[Dict[str, Any]]:
    """Segments (dicts with ``start_time`` / ``end_time`` labels) clear of every skipped span."""
    if not skip_ranges:
        return list(segments)
    kept: List[Dict[str, Any]] = []
    for segment in segments:
        start = label_to_seconds(segment.get("start_time"))
        end = label_to_seconds(segment.get("end_time"))
        if start is not None and end is not None and overlaps(start, end, skip_ranges):
            continue
        kept.append(segment)
    return kept


def range_key_url(url: str, source_range: Optional[Range]) -> str:
    """The link as cache keys see it: a range is a different video from the whole one."""
    if source_range is None:
        return url
    return f"{url}#range={int(source_range[0])}-{int(source_range[1])}"


def ranges_fingerprint(ranges: Iterable[Range]) -> str:
    """Short stable id of a set of spans, for cache and checkpoint keys."""
    text = ";".join(f"{int(round(start))}-{int(round(end))}" for start, end in ranges)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def ranges_to_json(ranges: Iterable[Range]) -> List[List[float]]:
    return [[float(start), float(end)] for start, end in ranges]


def ranges_from_json(raw: Any) -> List[Range]:
    """Spans saved with a task. Bad data is ignored rather than failing the task."""
    result: List[Range] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            try:
                start, end = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if math.isfinite(start) and math.isfinite(end) and end > start >= 0:
                result.append((start, end))
    return normalize_ranges(result)


def source_range_from_json(raw: Any) -> Optional[Range]:
    spans = ranges_from_json([raw] if raw else [])
    return spans[0] if spans else None
