"""SRT subtitle parser for MPV Subtitle Viewer"""

import logging
import re
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class SubtitleEntry:
    """Represents a single subtitle entry"""

    start_ms: int
    end_ms: int
    text: str


class SRTParseError(Exception):
    """Raised when SRT parsing fails"""

    pass


def parse_timestamp(timestamp: str) -> int:
    """
    Parse SRT timestamp to milliseconds.
    Format: HH:MM:SS,mmm
    Example: 00:01:23,456 -> 83456

    Raises:
        ValueError: If timestamp format is invalid
    """
    match = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", timestamp)
    if not match:
        raise ValueError(f"Invalid timestamp format: '{timestamp}' (expected HH:MM:SS,mmm)")

    hours, minutes, seconds, milliseconds = map(int, match.groups())

    # Validate ranges
    if minutes >= 60:
        raise ValueError(f"Invalid minutes in timestamp '{timestamp}': {minutes} >= 60")
    if seconds >= 60:
        raise ValueError(f"Invalid seconds in timestamp '{timestamp}': {seconds} >= 60")
    if milliseconds >= 1000:
        raise ValueError(f"Invalid milliseconds in timestamp '{timestamp}': {milliseconds} >= 1000")

    return (hours * 3600 + minutes * 60 + seconds) * 1000 + milliseconds


def parse_srt(content: str) -> List[SubtitleEntry]:
    """
    Parse SRT subtitle content into a list of SubtitleEntry objects.

    SRT format:
    1
    00:00:01,000 --> 00:00:03,000
    First subtitle line
    Can be multiple lines

    2
    00:00:04,000 --> 00:00:06,000
    Second subtitle

    Args:
        content: Raw SRT file content

    Returns:
        List of parsed SubtitleEntry objects, sorted by start time

    Raises:
        SRTParseError: If content is completely invalid or empty
    """
    if not content or not content.strip():
        raise SRTParseError("Empty SRT content")

    entries = []
    blocks = content.strip().split("\n\n")
    parse_errors = 0

    logger.debug(f"Parsing SRT with {len(blocks)} blocks")

    for block_idx, block in enumerate(blocks, 1):
        if not block.strip():
            continue

        lines = block.strip().split("\n")
        if len(lines) < 3:
            logger.warning(
                f"Block {block_idx} has only {len(lines)} lines (need ≥3), skipping: {block[:50]}"
            )
            parse_errors += 1
            continue

        # Line 0: sequence number (we validate but don't use it)
        try:
            _ = int(lines[0].strip())
        except ValueError:
            logger.warning(f"Block {block_idx} has invalid sequence number '{lines[0]}', skipping")
            parse_errors += 1
            continue

        # Line 1: timestamps
        timestamp_line = lines[1]
        match = re.match(r"([\d:,]+)\s*-->\s*([\d:,]+)", timestamp_line)
        if not match:
            logger.warning(
                f"Block {block_idx} has invalid timestamp line '{timestamp_line}', skipping"
            )
            parse_errors += 1
            continue

        start_str, end_str = match.groups()

        try:
            start_ms = parse_timestamp(start_str)
            end_ms = parse_timestamp(end_str)
        except ValueError as e:
            logger.warning(f"Block {block_idx}: {e}, skipping")
            parse_errors += 1
            continue

        # Validate time ordering
        if start_ms >= end_ms:
            logger.warning(
                f"Block {block_idx}: start time ({start_ms}ms) >= end time ({end_ms}ms), skipping"
            )
            parse_errors += 1
            continue

        # Lines 2+: subtitle text
        text = "\n".join(lines[2:])

        if not text.strip():
            logger.warning(f"Block {block_idx}: empty subtitle text, skipping")
            parse_errors += 1
            continue

        entries.append(SubtitleEntry(start_ms=start_ms, end_ms=end_ms, text=text))

    # Log summary
    if entries:
        logger.info(
            f"Successfully parsed {len(entries)} subtitle entries ({parse_errors} blocks skipped)"
        )
    else:
        error_msg = f"Failed to parse any valid subtitles from {len(blocks)} blocks"
        logger.error(error_msg)
        raise SRTParseError(error_msg)

    # Sort by start time to ensure correct ordering
    entries.sort(key=lambda e: e.start_ms)

    return entries


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
