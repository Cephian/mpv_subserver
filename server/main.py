"""FastAPI server for MPV Subtitle Viewer"""

import argparse
import asyncio
import logging
import signal
from pathlib import Path
from typing import Dict, List, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .srt_parser import SubtitleEntry, filter_entries_up_to, parse_srt

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


app = FastAPI(title="MPV Subtitle Viewer")

# Mount static files directory
static_dir = Path(__file__).parent / config.STATIC_DIR_NAME
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Setup application state
app.state.video_title = ""
app.state.subtitle_tracks: Dict[str, List[SubtitleEntry]] = {}
app.state.current_track = ""
app.state.current_time_ms = 0
app.state.connected_clients: Set[WebSocket] = set()
app.state.shutdown_event = asyncio.Event()


class InitRequest(BaseModel):
    video_title: str
    subtitle_tracks: Dict[str, str]  # filename -> SRT content


class TimeUpdate(BaseModel):
    time_ms: int


@app.on_event("startup")
async def startup_event():
    """Initialize application on startup"""
    logger.info("MPV Subtitle Viewer server starting up")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    logger.info("Shutting down server...")
    # Close all WebSocket connections gracefully
    clients = list(app.state.connected_clients)
    for client in clients:
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Error closing client connection: {e}")
    app.state.connected_clients.clear()
    logger.info("Server shutdown complete")


@app.get("/health")
async def health_check():
    """Health check endpoint for verifying server is ready"""
    return JSONResponse(
        {
            "status": "ok",
            "connected_clients": len(app.state.connected_clients),
            "current_track": app.state.current_track,
            "tracks_loaded": len(app.state.subtitle_tracks),
        }
    )


@app.post("/init")
async def initialize(req: InitRequest):
    """Initialize with video metadata and subtitle content"""
    logger.info(f"Initializing with video: {req.video_title}")

    app.state.video_title = req.video_title
    app.state.subtitle_tracks = {}

    for filename, content in req.subtitle_tracks.items():
        try:
            entries = parse_srt(content)
            app.state.subtitle_tracks[filename] = entries
            logger.info(f"Parsed {filename}: {len(entries)} subtitle entries")
        except Exception as e:
            logger.error(f"Error parsing {filename}: {e}", exc_info=True)

    if app.state.subtitle_tracks:
        app.state.current_track = list(app.state.subtitle_tracks.keys())[0]
        logger.info(f"Set current track to: {app.state.current_track}")
    else:
        logger.warning("No subtitle tracks successfully parsed")

    await broadcast_tracks()
    await broadcast_subtitles()

    return JSONResponse(
        {
            "status": "ok",
            "tracks": list(app.state.subtitle_tracks.keys()),
            "entries_count": {k: len(v) for k, v in app.state.subtitle_tracks.items()},
        }
    )


@app.post("/time")
async def update_time(req: TimeUpdate):
    """Update current playback time"""
    old_time = app.state.current_time_ms
    app.state.current_time_ms = req.time_ms

    # Log significant time changes (scrubbing)
    if abs(old_time - req.time_ms) > 5000:
        logger.debug(f"Time update: {old_time}ms -> {req.time_ms}ms (scrubbed)")

    await broadcast_subtitles()
    return JSONResponse({"status": "ok"})


@app.post("/shutdown")
async def shutdown():
    """Graceful shutdown endpoint"""
    logger.info("Shutdown requested via API")
    asyncio.create_task(shutdown_server())
    return JSONResponse({"status": "shutting down"})


async def shutdown_server():
    """Shutdown the server gracefully after a brief delay"""
    await asyncio.sleep(config.SHUTDOWN_DELAY_SECONDS)
    logger.info("Initiating graceful shutdown")
    app.state.shutdown_event.set()

    # Give uvicorn time to finish current requests
    await asyncio.sleep(0.1)

    # Send SIGTERM to self for graceful shutdown
    import os

    os.kill(os.getpid(), signal.SIGTERM)


@app.get("/")
async def serve_index():
    """Serve the viewer HTML page"""
    static_dir = Path(__file__).parent / config.STATIC_DIR_NAME
    index_path = static_dir / config.INDEX_HTML_NAME

    if not index_path.exists():
        logger.error(f"index.html not found at {index_path}")
        return JSONResponse({"error": "Frontend not found"}, status_code=500)

    return FileResponse(index_path)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await websocket.accept()

    # Check client limit
    if len(app.state.connected_clients) >= config.WS_MAX_CLIENTS:
        logger.warning("WebSocket client limit reached")
        await websocket.close(code=1008, reason="Server at capacity")
        return

    app.state.connected_clients.add(websocket)
    client_id = id(websocket)
    logger.info(
        f"WebSocket client connected (id={client_id}), total clients: {len(app.state.connected_clients)}"
    )

    try:
        # Send initial data
        await send_tracks(websocket)
        await send_subtitles(websocket)

        # Keep connection alive and handle messages
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "selectTrack":
                track = data.get("track")
                logger.info(f"Client {client_id} selected track: {track}")
                await handle_track_selection(track)

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected (id={client_id})")
    except Exception as e:
        logger.error(f"WebSocket error for client {client_id}: {e}", exc_info=True)
    finally:
        app.state.connected_clients.discard(websocket)
        logger.info(
            f"Client {client_id} removed, total clients: {len(app.state.connected_clients)}"
        )


async def handle_track_selection(track: str):
    """Handle subtitle track selection from client"""
    if track in app.state.subtitle_tracks:
        app.state.current_track = track
        logger.info(f"Switched to track: {track}")
        await broadcast_subtitles()
    else:
        logger.warning(f"Invalid track selection: {track}")


async def broadcast_tracks():
    """Broadcast available tracks to all connected clients"""
    if not app.state.connected_clients:
        return

    disconnected = []
    for client in list(app.state.connected_clients):
        try:
            await send_tracks(client)
        except Exception as e:
            logger.error(f"Error broadcasting tracks to client: {e}")
            disconnected.append(client)

    # Remove disconnected clients
    for client in disconnected:
        app.state.connected_clients.discard(client)


async def send_tracks(websocket: WebSocket):
    """Send track list to a specific client"""
    await websocket.send_json(
        {
            "type": "tracks",
            "tracks": list(app.state.subtitle_tracks.keys()),
            "currentTrack": app.state.current_track,
            "videoTitle": app.state.video_title,
        }
    )


async def broadcast_subtitles():
    """Broadcast filtered subtitles to all connected clients"""
    if not app.state.connected_clients:
        return

    disconnected = []
    for client in list(app.state.connected_clients):
        try:
            await send_subtitles(client)
        except Exception as e:
            logger.error(f"Error broadcasting subtitles to client: {e}")
            disconnected.append(client)

    # Remove disconnected clients
    for client in disconnected:
        app.state.connected_clients.discard(client)


async def send_subtitles(websocket: WebSocket):
    """Send filtered subtitles to a specific client"""
    if app.state.current_track not in app.state.subtitle_tracks:
        return

    entries = app.state.subtitle_tracks[app.state.current_track]
    filtered = filter_entries_up_to(entries, app.state.current_time_ms)

    await websocket.send_json(
        {
            "type": "subtitles",
            "lines": [{"text": e.text, "start_ms": e.start_ms} for e in filtered],
            "currentTime": app.state.current_time_ms / 1000.0,
        }
    )


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="MPV Subtitle Viewer Server")
    parser.add_argument(
        "--host",
        default=config.DEFAULT_HOST,
        help=f"Host to bind to (default: {config.DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.DEFAULT_PORT,
        help=f"Port to bind to (default: {config.DEFAULT_PORT})",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=config.LOG_LEVEL,
        help=f"Log level (default: {config.LOG_LEVEL})",
    )
    return parser.parse_args()


def run():
    """Entry point for the mpv_subserver command"""
    args = parse_args()

    # Update log level based on args
    logging.getLogger().setLevel(args.log_level.upper())

    logger.info(f"Starting server on {args.host}:{args.port}")

    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=args.log_level == "debug",
    )


if __name__ == "__main__":
    run()
