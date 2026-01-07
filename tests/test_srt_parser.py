"""Tests for subtitle parser using pysubs2"""

import pytest

from server.srt_parser import SubtitleParseError, filter_entries_up_to, parse_srt, parse_subtitles


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
    # pysubs2 uses \N for line breaks in SSA/ASS format
    assert "First line" in entries[0].text
    assert "Second line" in entries[0].text
    assert "Third line" in entries[0].text


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
    assert "First subtitle" in entries[0].text
    assert "Second subtitle" in entries[1].text
    assert "Third subtitle" in entries[2].text


def test_parse_empty_content():
    """Test that empty content raises error"""
    with pytest.raises(SubtitleParseError, match="Empty subtitle content"):
        parse_srt("")

    with pytest.raises(SubtitleParseError, match="Empty subtitle content"):
        parse_srt("   \n\n   ")


def test_parse_invalid_content():
    """Test that invalid content raises error"""
    with pytest.raises(SubtitleParseError):
        parse_srt("This is not valid SRT content at all")


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
    assert "First" in entries[0].text
    assert "Second" in entries[1].text
    assert "Third" in entries[2].text
    # Verify times are sorted
    assert entries[0].start_ms < entries[1].start_ms < entries[2].start_ms


def test_parse_windows_line_endings():
    """Test parsing SRT with Windows (CRLF) line endings"""
    srt = "1\r\n00:00:01,000 --> 00:00:02,000\r\nFirst subtitle\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nSecond subtitle"

    entries = parse_srt(srt)
    assert len(entries) == 2
    assert "First subtitle" in entries[0].text
    assert "Second subtitle" in entries[1].text


def test_parse_skips_empty_subtitles():
    """Test that parser skips empty subtitle text"""
    srt = """1
00:00:01,000 --> 00:00:02,000
Valid subtitle

2
00:00:03,000 --> 00:00:04,000


3
00:00:05,000 --> 00:00:06,000
Another valid one"""

    entries = parse_srt(srt)
    assert len(entries) == 2
    assert "Valid subtitle" in entries[0].text
    assert "Another valid one" in entries[1].text


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


def test_parse_subtitles_with_format_hint():
    """Test using the generic parse_subtitles function with format hint"""
    srt = """1
00:00:01,000 --> 00:00:02,000
Test subtitle"""

    entries = parse_subtitles(srt, format_hint="srt")
    assert len(entries) == 1
    assert "Test subtitle" in entries[0].text


def test_parse_webvtt():
    """Test parsing WebVTT format"""
    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
WebVTT subtitle"""

    entries = parse_subtitles(vtt, format_hint="vtt")
    assert len(entries) == 1
    assert "WebVTT subtitle" in entries[0].text
    assert entries[0].start_ms == 1000
