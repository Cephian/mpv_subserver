"""Tests for delta update logic - ensures optimal subtitle streaming"""

from server.main import AddSubtitles, RemoveSubtitles, calculate_subtitle_delta, find_subtitle_index
from server.srt_parser import SubtitleEntry


def test_find_subtitle_index_empty():
    """Test index finding with empty list"""
    assert find_subtitle_index([], 1000) == 0


def test_find_subtitle_index_before_first():
    """Test index when time is before any subtitle"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
    ]
    assert find_subtitle_index(entries, 500) == 0


def test_find_subtitle_index_exact_match():
    """Test index at exact subtitle start time"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
        SubtitleEntry(start_ms=5000, end_ms=6000, text="Third"),
    ]
    assert find_subtitle_index(entries, 3000) == 2  # First and Second


def test_find_subtitle_index_between():
    """Test index between subtitles"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
        SubtitleEntry(start_ms=5000, end_ms=6000, text="Third"),
    ]
    assert find_subtitle_index(entries, 3500) == 2


def test_find_subtitle_index_after_all():
    """Test index after all subtitles"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
    ]
    assert find_subtitle_index(entries, 10000) == 2


def test_calculate_delta_no_change():
    """Test that no delta is returned when index doesn't change"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
    ]
    delta = calculate_subtitle_delta(1, 1, entries)
    assert delta is None


def test_calculate_delta_forward_one():
    """Test delta when moving forward by one subtitle"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
    ]
    delta = calculate_subtitle_delta(0, 1, entries)

    assert isinstance(delta, AddSubtitles)
    assert len(delta.subtitles) == 1
    assert delta.subtitles[0]["text"] == "First"
    assert delta.subtitles[0]["start_ms"] == 1000


def test_calculate_delta_forward_multiple():
    """Test delta when moving forward by multiple subtitles"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=2000, end_ms=3000, text="Second"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Third"),
    ]
    delta = calculate_subtitle_delta(0, 3, entries)

    assert isinstance(delta, AddSubtitles)
    assert len(delta.subtitles) == 3
    assert delta.subtitles[0]["text"] == "First"
    assert delta.subtitles[1]["text"] == "Second"
    assert delta.subtitles[2]["text"] == "Third"


def test_calculate_delta_backward_one():
    """Test delta when moving backward by one subtitle (scrubbing back)"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
    ]
    delta = calculate_subtitle_delta(2, 1, entries)

    assert isinstance(delta, RemoveSubtitles)
    assert delta.count == 1


def test_calculate_delta_backward_multiple():
    """Test delta when scrubbing far backward"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=2000, end_ms=3000, text="Second"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Third"),
        SubtitleEntry(start_ms=4000, end_ms=5000, text="Fourth"),
    ]
    delta = calculate_subtitle_delta(4, 1, entries)

    assert isinstance(delta, RemoveSubtitles)
    assert delta.count == 3


def test_calculate_delta_backward_to_zero():
    """Test delta when scrubbing back to beginning"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="First"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="Second"),
    ]
    delta = calculate_subtitle_delta(2, 0, entries)

    assert isinstance(delta, RemoveSubtitles)
    assert delta.count == 2


def test_playback_scenario():
    """Test a realistic playback scenario with multiple time updates"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=2000, text="Hello"),
        SubtitleEntry(start_ms=3000, end_ms=4000, text="World"),
        SubtitleEntry(start_ms=5000, end_ms=6000, text="!"),
    ]

    # Start at beginning
    idx = find_subtitle_index(entries, 0)
    assert idx == 0

    # Play forward to 1.5s - should show first subtitle
    new_idx = find_subtitle_index(entries, 1500)
    assert new_idx == 1
    delta = calculate_subtitle_delta(idx, new_idx, entries)
    assert isinstance(delta, AddSubtitles)
    assert len(delta.subtitles) == 1
    idx = new_idx

    # Play to 3.5s - should show second subtitle
    new_idx = find_subtitle_index(entries, 3500)
    assert new_idx == 2
    delta = calculate_subtitle_delta(idx, new_idx, entries)
    assert isinstance(delta, AddSubtitles)
    assert len(delta.subtitles) == 1
    idx = new_idx

    # Scrub back to 2s - should remove second subtitle
    new_idx = find_subtitle_index(entries, 2000)
    assert new_idx == 1
    delta = calculate_subtitle_delta(idx, new_idx, entries)
    assert isinstance(delta, RemoveSubtitles)
    assert delta.count == 1
    idx = new_idx

    # Play to end
    new_idx = find_subtitle_index(entries, 10000)
    assert new_idx == 3
    delta = calculate_subtitle_delta(idx, new_idx, entries)
    assert isinstance(delta, AddSubtitles)
    assert len(delta.subtitles) == 2  # Second and third


def test_no_update_during_steady_playback():
    """Test that no delta is sent when playing in same subtitle"""
    entries = [
        SubtitleEntry(start_ms=1000, end_ms=5000, text="Long subtitle"),
    ]

    # At 2s
    idx1 = find_subtitle_index(entries, 2000)
    # At 2.1s
    idx2 = find_subtitle_index(entries, 2100)
    # At 2.2s
    idx3 = find_subtitle_index(entries, 2200)

    # All should be same index
    assert idx1 == idx2 == idx3 == 1

    # No deltas needed
    assert calculate_subtitle_delta(idx1, idx2, entries) is None
    assert calculate_subtitle_delta(idx2, idx3, entries) is None
