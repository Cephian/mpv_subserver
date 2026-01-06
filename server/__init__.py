"""MPV Subtitle Viewer Server

A language learning extension for MPV that displays subtitles in a web interface.
"""

__version__ = "0.1.0"
__author__ = "MPV Subtitle Viewer Contributors"

# Export main components
from . import config
from .main import app, run
from .srt_parser import SRTParseError, SubtitleEntry, filter_entries_up_to, parse_srt

__all__ = [
    "app",
    "run",
    "SubtitleEntry",
    "parse_srt",
    "filter_entries_up_to",
    "SRTParseError",
    "config",
    "__version__",
]
