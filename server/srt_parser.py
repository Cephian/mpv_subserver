"""Subtitle parser for MPV Subtitle Viewer using pysubs2"""

import logging
from dataclasses import dataclass
from typing import List

import pysubs2

logger = logging.getLogger(__name__)


@dataclass
class SubtitleEntry:
    """Represents a single subtitle entry"""

    start_ms: int
    end_ms: int
    text: str


class SubtitleParseError(Exception):
    """Raised when subtitle parsing fails"""

    pass


def parse_subtitles(content: str, format_hint: str = "srt") -> List[SubtitleEntry]:
    """
    Parse subtitle content using pysubs2.

    Supports multiple formats: SRT, WebVTT, SSA/ASS, MicroDVD, MPL2, TMP, and more.

    Args:
        content: Raw subtitle file content
        format_hint: Format hint for pysubs2 (default: "srt")
                    Common values: "srt", "ass", "ssa", "vtt", "microdvd", "mpl2", "tmp"

    Returns:
        List of parsed SubtitleEntry objects, sorted by start time

    Raises:
        SubtitleParseError: If content cannot be parsed
    """
    if not content or not content.strip():
        raise SubtitleParseError("Empty subtitle content")

    try:
        # Parse using pysubs2
        subs = pysubs2.SSAFile.from_string(content, format_=format_hint)
    except Exception as e:
        error_msg = f"Failed to parse {format_hint.upper()} subtitles: {e}"
        logger.error(error_msg)
        raise SubtitleParseError(error_msg) from e

    if not subs:
        raise SubtitleParseError("No subtitle entries found")

    entries = []
    for event in subs:
        # Skip empty subtitles
        if not event.text.strip():
            continue

        # Clean up text: pysubs2 may preserve \N (SSA/ASS line break markers) and \r
        # Replace \N with actual newlines, then strip unwanted whitespace
        text = event.text.replace('\\N', '\n').replace('\r', '').strip()

        entries.append(
            SubtitleEntry(
                start_ms=event.start,  # pysubs2 uses milliseconds
                end_ms=event.end,
                text=text,
            )
        )

    if not entries:
        raise SubtitleParseError("No valid subtitle entries found after filtering")

    logger.info(f"Successfully parsed {len(entries)} subtitle entries")

    # Sort by start time (pysubs2 should already do this, but be explicit)
    entries.sort(key=lambda e: e.start_ms)

    return entries


def parse_srt(content: str) -> List[SubtitleEntry]:
    """
    Parse SRT subtitle content.

    Convenience wrapper around parse_subtitles() for SRT format.
    Kept for backward compatibility with existing code.

    Args:
        content: Raw SRT file content

    Returns:
        List of parsed SubtitleEntry objects, sorted by start time

    Raises:
        SubtitleParseError: If content cannot be parsed
    """
    return parse_subtitles(content, format_hint="srt")


def filter_entries_up_to(entries: List[SubtitleEntry], current_time_ms: int) -> List[SubtitleEntry]:
    """
    Filter subtitle entries to only those that have started before or at current_time_ms.
    Returns entries sorted by start time.

    Args:
        entries: List of subtitle entries (should already be sorted)
        current_time_ms: Current playback position in milliseconds

    Returns:
        Filtered list of entries
    """
    return [entry for entry in entries if entry.start_ms <= current_time_ms]
