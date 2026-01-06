"""Smoke tests for SRT parser - just enough to catch obvious breakage"""

import pytest

from server.srt_parser import SRTParseError, filter_entries_up_to, parse_srt, parse_timestamp


def test_parse_timestamp_basic():
    """Test basic timestamp parsing"""
    assert parse_timestamp("00:00:01,000") == 1000
    assert parse_timestamp("00:01:00,000") == 60000
    assert parse_timestamp("01:00:00,000") == 3600000
    assert parse_timestamp("00:00:00,500") == 500


def test_parse_timestamp_complex():
    """Test more complex timestamps"""
    assert parse_timestamp("01:23:45,678") == 5025678


def test_parse_timestamp_invalid():
    """Test that invalid timestamps raise errors"""
    with pytest.raises(ValueError, match="Invalid timestamp format"):
        parse_timestamp("invalid")

    with pytest.raises(ValueError, match="Invalid timestamp format"):
        parse_timestamp("1:2:3,4")  # Wrong format


def test_parse_timestamp_out_of_range():
    """Test that out-of-range values are caught"""
    with pytest.raises(ValueError, match="Invalid minutes"):
        parse_timestamp("00:99:00,000")

    with pytest.raises(ValueError, match="Invalid seconds"):
        parse_timestamp("00:00:99,000")


def test_parse_basic_srt():
    """Test parsing a simple valid SRT"""
    srt = """1
00:00:01,000 --> 00:00:02,000
Hello world"""

    entries = parse_srt(srt)
    assert len(entries) == 1
    assert entries[0].start_ms == 1000
    assert entries[0].end_ms == 2000
    assert entries[0].text == "Hello world"


def test_parse_multiline_subtitle():
    """Test subtitle with multiple lines of text"""
    srt = """1
00:00:01,000 --> 00:00:03,000
First line
Second line
Third line"""

    entries = parse_srt(srt)
    assert len(entries) == 1
    assert entries[0].text == "First line\nSecond line\nThird line"


def test_parse_multiple_subtitles():
    """Test parsing multiple subtitle entries"""
    srt = """1
00:00:01,000 --> 00:00:02,000
First subtitle

2
00:00:03,000 --> 00:00:04,000
Second subtitle

3
00:00:05,000 --> 00:00:06,000
Third subtitle"""

    entries = parse_srt(srt)
    assert len(entries) == 3
    assert entries[0].text == "First subtitle"
    assert entries[1].text == "Second subtitle"
    assert entries[2].text == "Third subtitle"


def test_parse_empty_content():
    """Test that empty content raises error"""
    with pytest.raises(SRTParseError, match="Empty SRT content"):
        parse_srt("")

    with pytest.raises(SRTParseError, match="Empty SRT content"):
        parse_srt("   \n\n   ")


def test_parse_all_invalid():
    """Test that completely invalid SRT raises error"""
    with pytest.raises(SRTParseError, match="Failed to parse any valid subtitles"):
        parse_srt("This is not valid SRT content at all")


def test_parse_skips_invalid_blocks():
    """Test that parser skips invalid blocks but keeps valid ones"""
    srt = """1
00:00:01,000 --> 00:00:02,000
Valid subtitle

INVALID BLOCK
Not a subtitle

2
00:00:03,000 --> 00:00:04,000
Another valid one"""

    entries = parse_srt(srt)
    assert len(entries) == 2
    assert entries[0].text == "Valid subtitle"
    assert entries[1].text == "Another valid one"


def test_parse_sorts_by_time():
    """Test that entries are sorted by start time"""
    srt = """3
00:00:05,000 --> 00:00:06,000
Third

1
00:00:01,000 --> 00:00:02,000
First

2
00:00:03,000 --> 00:00:04,000
Second"""

    entries = parse_srt(srt)
    assert len(entries) == 3
    assert entries[0].text == "First"
    assert entries[1].text == "Second"
    assert entries[2].text == "Third"


def test_filter_entries_basic():
    """Test basic filtering by time"""
    from server.srt_parser import SubtitleEntry

    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
        SubtitleEntry(start_ms=5000, end_ms=6000, text="Third"),
    ]

    # At 2.5 seconds, should only see first entry
    filtered = filter_entries_up_to(entries, 2500)
    assert len(filtered) == 1
    assert filtered[0].text == "First"

    # At 4 seconds, should see first two
    filtered = filter_entries_up_to(entries, 4000)
    assert len(filtered) == 2

    # At 10 seconds, should see all
    filtered = filter_entries_up_to(entries, 10000)
    assert len(filtered) == 3


def test_filter_entries_at_exact_time():
    """Test filtering at exact subtitle start time"""
    from server.srt_parser import SubtitleEntry

    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
    ]

    # At exactly 3 seconds, should include second entry
    filtered = filter_entries_up_to(entries, 3000)
    assert len(filtered) == 2


def test_filter_entries_before_first():
    """Test filtering before any subtitles appear"""
    from server.srt_parser import SubtitleEntry

    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
    ]

    filtered = filter_entries_up_to(entries, 500)
    assert len(filtered) == 0


def test_real_world_srt():
    """Test with a realistic SRT example"""
    srt = """1
00:00:00,500 --> 00:00:02,000
Welcome to the show!

2
00:00:02,500 --> 00:00:05,000
Today we're talking about
language learning with subtitles.

3
00:00:05,500 --> 00:00:07,000
It's a great way to learn!"""

    entries = parse_srt(srt)
    assert len(entries) == 3
    assert entries[0].start_ms == 500
    assert entries[1].start_ms == 2500
    assert entries[2].start_ms == 5500
    assert "language learning" in entries[1].text
