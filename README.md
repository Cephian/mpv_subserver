# MPV Subtitle Viewer

Language learning tool for MPV that shows all subtitles up to the current playback position in a web interface.

## Install

```bash
# Install the server
nix profile install .#

# Copy MPV script
cp subtitle-viewer.lua ~/.config/mpv/scripts/
```

## Usage

1. Open a video in MPV with SRT subtitles
2. Press `Ctrl+Shift+s` to open the viewer (configurable)
3. Browser opens at `http://localhost:8765` with live subtitle feed

Press the keybind again to stop.

### Configuration

Optional: Create `~/.config/mpv/script-opts/subtitle-viewer.conf`:

```conf
keybind=Ctrl+Shift+s
port=8765
auto_open_browser=yes
browser_command=xdg-open
```

See `subtitle-viewer.conf.example` for details.

## Development

```bash
nix develop            # Enter dev shell (or use direnv)
python -m server.main  # Run server
pytest                 # Run tests
ruff check .           # Lint
ruff format .          # Format
```

Server accepts `--host`, `--port`, and `--log-level` flags.

## How It Works

MPV Lua script → Python FastAPI server → WebSocket → Browser

Scrubbing backward removes future subtitles, forward adds past ones.
