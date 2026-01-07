"""FastAPI server for MPV Subtitle Viewer"""

import argparse
import asyncio
import bisect
import logging
import os
import signal
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .srt_parser import SubtitleEntry, parse_subtitles


# Delta types for subtitle updates
@dataclass
class AddSubtitles:
    """Delta representing subtitles to add"""

    subtitles: List[dict]


@dataclass
class RemoveSubtitles:
    """Delta representing subtitles to remove"""

    count: int


SubtitleDelta = AddSubtitles | RemoveSubtitles | None


# Session state for multi-instance support
@dataclass
class Session:
    """Represents a single MPV instance session"""

    session_id: str
    video_title: str = ""
    subtitle_tracks: Dict[str, List[SubtitleEntry]] = field(default_factory=dict)
    current_track: str = ""
    current_time_ms: int = 0
    current_index: int = 0  # For delta calculation
    last_activity: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    connected_clients: Set[WebSocket] = field(default_factory=set)


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info("MPV Subtitle Viewer server starting up")

    # Setup application state (session-based)
    app.state.sessions: Dict[str, Session] = {}
    app.state.global_clients: Set[WebSocket] = set()  # Session picker clients
    app.state.last_session_closed = time.time()
    app.state.shutdown_event = asyncio.Event()

    # Setup legacy global state for backwards compatibility
    app.state.video_title = ""
    app.state.subtitle_tracks: Dict[str, List[SubtitleEntry]] = {}
    app.state.current_track = ""
    app.state.current_time_ms = 0
    app.state.current_index = 0
    app.state.connected_clients: Set[WebSocket] = set()

    # Start background tasks
    app.state.cleanup_task = asyncio.create_task(session_cleanup_task(app))
    app.state.inactivity_task = asyncio.create_task(inactivity_monitor_task(app))

    yield

    # Shutdown
    logger.info("Shutting down server...")

    # Cancel background tasks
    if hasattr(app.state, "cleanup_task"):
        app.state.cleanup_task.cancel()
        try:
            await app.state.cleanup_task
        except asyncio.CancelledError:
            pass

    if hasattr(app.state, "inactivity_task"):
        app.state.inactivity_task.cancel()
        try:
            await app.state.inactivity_task
        except asyncio.CancelledError:
            pass

    # Close all session WebSocket connections gracefully
    for session in list(app.state.sessions.values()):
        for client in list(session.connected_clients):
            try:
                await client.close()
            except Exception as e:
                logger.error(f"Error closing session client connection: {e}")
        session.connected_clients.clear()

    # Close global clients
    for client in list(app.state.global_clients):
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Error closing global client connection: {e}")
    app.state.global_clients.clear()

    # Close legacy clients
    clients = list(app.state.connected_clients)
    for client in clients:
        try:
            await client.close()
        except Exception as e:
            logger.error(f"Error closing client connection: {e}")
    app.state.connected_clients.clear()

    logger.info("Server shutdown complete")


app = FastAPI(title="MPV Subtitle Viewer", lifespan=lifespan)

# Mount static files directory
static_dir = Path(__file__).parent / config.STATIC_DIR_NAME
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class InitRequest(BaseModel):
    video_title: str
    subtitle_tracks: Dict[str, str]  # filename -> SRT content


class TimeUpdate(BaseModel):
    time_ms: int


def find_subtitle_index(entries: List[SubtitleEntry], time_ms: int) -> int:
    """
    Binary search to find how many subtitles should be shown at time_ms.
    Returns the count of subtitles with start_ms <= time_ms.
    """

    # Extract start times (entries are already sorted)
    start_times = [e.start_ms for e in entries]
    # bisect_right returns insertion point, which is count of items <= time_ms
    return bisect.bisect_right(start_times, time_ms)


def calculate_subtitle_delta(
    old_index: int, new_index: int, entries: List[SubtitleEntry]
) -> SubtitleDelta:
    """
    Calculate what delta to send based on index change.

    Returns:
        - AddSubtitles for forward movement
        - RemoveSubtitles for backward movement
        - None if no change
    """
    if new_index == old_index:
        return None

    if new_index > old_index:
        # Moving forward: add new subtitles
        added = []
        for i in range(old_index, new_index):
            if i < len(entries):
                added.append({"text": entries[i].text, "start_ms": entries[i].start_ms})
        return AddSubtitles(subtitles=added)
    else:
        # Moving backward: remove subtitles
        return RemoveSubtitles(count=old_index - new_index)


# Session management helper functions


def create_session_id() -> str:
    """Generate a unique session ID"""
    return str(uuid.uuid4())


def get_session(session_id: str) -> Optional[Session]:
    """Get a session by ID"""
    return app.state.sessions.get(session_id)


async def close_session(session_id: str):
    """Close a session and notify all clients"""
    if session_id not in app.state.sessions:
        return

    session = app.state.sessions[session_id]
    logger.info(f"Closing session {session_id} ({session.video_title})")

    # Close all WebSocket clients for this session
    for client in list(session.connected_clients):
        try:
            await client.send_json({"type": "session_closed"})
            await client.close()
        except Exception as e:
            logger.error(f"Error closing session client: {e}")

    # Remove session
    del app.state.sessions[session_id]

    # Broadcast updated session list to global clients
    await broadcast_sessions_list()

    # Update last_session_closed timestamp if no sessions left
    if len(app.state.sessions) == 0:
        app.state.last_session_closed = time.time()
        logger.info("All sessions closed")


async def broadcast_sessions_list():
    """Broadcast list of active sessions to all global clients"""
    if not app.state.global_clients:
        return

    sessions_data = [
        {
            "session_id": sid,
            "video_title": session.video_title,
            "last_activity": session.last_activity,
            "created_at": session.created_at,
            "connected_clients": len(session.connected_clients),
        }
        for sid, session in app.state.sessions.items()
    ]

    message = {"type": "sessions_list", "sessions": sessions_data}

    disconnected = []
    for client in list(app.state.global_clients):
        try:
            await client.send_json(message)
        except Exception as e:
            logger.error(f"Error broadcasting sessions list: {e}")
            disconnected.append(client)

    # Remove disconnected clients
    for client in disconnected:
        app.state.global_clients.discard(client)


async def broadcast_session_tracks(session_id: str):
    """Broadcast available tracks to all clients of a specific session"""
    session = get_session(session_id)
    if not session or not session.connected_clients:
        return

    disconnected = []
    for client in list(session.connected_clients):
        try:
            await send_tracks_for_session(client, session)
        except Exception as e:
            logger.error(f"Error broadcasting tracks to session client: {e}")
            disconnected.append(client)

    # Remove disconnected clients
    for client in disconnected:
        session.connected_clients.discard(client)


async def send_tracks_for_session(websocket: WebSocket, session: Session):
    """Send track list to a specific client for a session"""
    await websocket.send_json(
        {
            "type": "tracks",
            "tracks": list(session.subtitle_tracks.keys()),
            "currentTrack": session.current_track,
            "videoTitle": session.video_title,
        }
    )


async def send_initial_subtitles_for_session(websocket: WebSocket, session: Session):
    """Send initial full subtitle list to a client for a session"""
    if session.current_track not in session.subtitle_tracks:
        await websocket.send_json({"type": "subtitles_init", "lines": []})
        return

    entries = session.subtitle_tracks[session.current_track]
    current_entries = entries[: session.current_index]

    await websocket.send_json(
        {
            "type": "subtitles_init",
            "lines": [{"text": e.text, "start_ms": e.start_ms} for e in current_entries],
        }
    )


async def broadcast_subtitle_delta_for_session(
    session_id: str, old_index: int, new_index: int
):
    """Broadcast only the subtitle changes (delta) to all clients of a session"""
    session = get_session(session_id)
    if not session or not session.connected_clients:
        return

    if session.current_track not in session.subtitle_tracks:
        return

    entries = session.subtitle_tracks[session.current_track]
    delta = calculate_subtitle_delta(old_index, new_index, entries)

    match delta:
        case None:
            return
        case AddSubtitles(subtitles=subs):
            disconnected = []
            for client in list(session.connected_clients):
                try:
                    for subtitle in subs:
                        await client.send_json({"type": "subtitle_add", "subtitle": subtitle})
                except Exception as e:
                    logger.error(f"Error broadcasting subtitle additions to session client: {e}")
                    disconnected.append(client)
            for client in disconnected:
                session.connected_clients.discard(client)
        case RemoveSubtitles(count=n):
            disconnected = []
            for client in list(session.connected_clients):
                try:
                    await client.send_json({"type": "subtitle_remove", "count": n})
                except Exception as e:
                    logger.error(f"Error broadcasting subtitle removals to session client: {e}")
                    disconnected.append(client)
            for client in disconnected:
                session.connected_clients.discard(client)


# Background tasks


async def session_cleanup_task(app: FastAPI):
    """Background task to clean up stale sessions"""
    logger.info("Session cleanup task started")
    try:
        while True:
            await asyncio.sleep(config.SESSION_CLEANUP_INTERVAL)

            current_time = time.time()
            stale_sessions = []

            for session_id, session in app.state.sessions.items():
                if current_time - session.last_activity > config.SESSION_TIMEOUT_SECONDS:
                    stale_sessions.append(session_id)
                    logger.info(
                        f"Session {session_id} is stale (last activity: "
                        f"{current_time - session.last_activity:.1f}s ago)"
                    )

            # Close stale sessions
            for session_id in stale_sessions:
                await close_session(session_id)

    except asyncio.CancelledError:
        logger.info("Session cleanup task cancelled")
        raise


async def inactivity_monitor_task(app: FastAPI):
    """Background task to shutdown server after global inactivity"""
    logger.info("Inactivity monitor task started")
    try:
        while True:
            await asyncio.sleep(config.INACTIVITY_CHECK_INTERVAL)

            # Only check for inactivity if no sessions exist
            if len(app.state.sessions) == 0:
                current_time = time.time()
                inactive_duration = current_time - app.state.last_session_closed

                if inactive_duration > config.INACTIVITY_SHUTDOWN_SECONDS:
                    logger.info(
                        f"No sessions active for {inactive_duration:.1f}s, "
                        "initiating shutdown"
                    )
                    app.state.shutdown_event.set()
                    # Give time for cleanup
                    await asyncio.sleep(0.1)
                    os.kill(os.getpid(), signal.SIGTERM)
                    return

    except asyncio.CancelledError:
        logger.info("Inactivity monitor task cancelled")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint for verifying server is ready"""
    return JSONResponse(
        {
            "status": "ok",
            "active_sessions": len(app.state.sessions),
            "total_clients": sum(len(s.connected_clients) for s in app.state.sessions.values())
            + len(app.state.global_clients),
            # Legacy fields for backwards compatibility
            "connected_clients": len(app.state.connected_clients),
            "current_track": app.state.current_track,
            "tracks_loaded": len(app.state.subtitle_tracks),
        }
    )


@app.post("/session/create")
async def create_session():
    """Create a new session for an MPV instance"""
    session_id = create_session_id()
    session = Session(session_id=session_id)
    app.state.sessions[session_id] = session

    logger.info(f"Created session {session_id}")

    # Broadcast updated session list to global clients
    await broadcast_sessions_list()

    return JSONResponse({"status": "ok", "session_id": session_id})


@app.get("/sessions")
async def list_sessions():
    """List all active sessions"""
    sessions_data = [
        {
            "session_id": sid,
            "video_title": session.video_title,
            "last_activity": session.last_activity,
            "created_at": session.created_at,
            "connected_clients": len(session.connected_clients),
        }
        for sid, session in app.state.sessions.items()
    ]

    return JSONResponse({"status": "ok", "sessions": sessions_data})


@app.get("/session/{session_id}/health")
async def session_health(session_id: str):
    """Check if a specific session exists"""
    session = get_session(session_id)
    if not session:
        return JSONResponse({"status": "not_found"}, status_code=404)

    return JSONResponse(
        {
            "status": "ok",
            "session": {
                "session_id": session_id,
                "video_title": session.video_title,
                "last_activity": session.last_activity,
                "connected_clients": len(session.connected_clients),
            },
        }
    )


@app.post("/session/{session_id}/init")
async def session_init(session_id: str, req: InitRequest):
    """Initialize session with video metadata and subtitle content"""
    session = get_session(session_id)
    if not session:
        return JSONResponse({"status": "session_not_found"}, status_code=404)

    logger.info(f"Initializing session {session_id} with video: {req.video_title}")

    session.video_title = req.video_title
    session.subtitle_tracks = {}

    for filename, content in req.subtitle_tracks.items():
        try:
            entries = parse_subtitles(content)
            session.subtitle_tracks[filename] = entries
            logger.info(f"Parsed {filename}: {len(entries)} subtitle entries")
        except Exception as e:
            logger.error(f"Error parsing {filename}: {e}", exc_info=True)

    if session.subtitle_tracks:
        session.current_track = list(session.subtitle_tracks.keys())[0]
        logger.info(f"Set current track to: {session.current_track}")
    else:
        logger.warning("No subtitle tracks successfully parsed")

    # Reset index for new video
    session.current_index = 0
    session.current_time_ms = 0
    session.last_activity = time.time()

    # Broadcast tracks to session clients
    await broadcast_session_tracks(session_id)

    # Send initial state to all session clients
    for client in list(session.connected_clients):
        try:
            await send_initial_subtitles_for_session(client, session)
        except Exception as e:
            logger.error(f"Error sending initial subtitles: {e}")

    # Broadcast updated session list
    await broadcast_sessions_list()

    return JSONResponse(
        {
            "status": "ok",
            "tracks": list(session.subtitle_tracks.keys()),
            "entries_count": {k: len(v) for k, v in session.subtitle_tracks.items()},
        }
    )


@app.post("/session/{session_id}/time")
async def session_time_update(session_id: str, req: TimeUpdate):
    """Update current playback time for a session"""
    session = get_session(session_id)
    if not session:
        return JSONResponse({"status": "session_not_found"}, status_code=404)

    old_index = session.current_index
    session.current_time_ms = req.time_ms
    session.last_activity = time.time()  # Update activity timestamp

    # Calculate new index using binary search
    if session.current_track in session.subtitle_tracks:
        entries = session.subtitle_tracks[session.current_track]
        new_index = find_subtitle_index(entries, req.time_ms)
    else:
        new_index = 0

    # Only broadcast if index changed
    if new_index != old_index:
        session.current_index = new_index
        await broadcast_subtitle_delta_for_session(session_id, old_index, new_index)

    return JSONResponse({"status": "ok"})


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a session"""
    logger.info(f"Delete requested for session {session_id}")
    await close_session(session_id)
    return JSONResponse({"status": "ok"})


@app.post("/init")
async def initialize(req: InitRequest):
    """Initialize with video metadata and subtitle content"""
    logger.info(f"Initializing with video: {req.video_title}")

    app.state.video_title = req.video_title
    app.state.subtitle_tracks = {}

    for filename, content in req.subtitle_tracks.items():
        try:
            entries = parse_subtitles(content)
            app.state.subtitle_tracks[filename] = entries
            logger.info(f"Parsed {filename}: {len(entries)} subtitle entries")
        except Exception as e:
            logger.error(f"Error parsing {filename}: {e}", exc_info=True)

    if app.state.subtitle_tracks:
        app.state.current_track = list(app.state.subtitle_tracks.keys())[0]
        logger.info(f"Set current track to: {app.state.current_track}")
    else:
        logger.warning("No subtitle tracks successfully parsed")

    # Reset index for new video
    app.state.current_index = 0
    app.state.current_time_ms = 0

    await broadcast_tracks()
    # Send initial state to all clients
    for client in list(app.state.connected_clients):
        try:
            await send_initial_subtitles(client)
        except Exception as e:
            logger.error(f"Error sending initial subtitles: {e}")

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
    old_index = app.state.current_index
    app.state.current_time_ms = req.time_ms

    # Calculate new index using binary search
    if app.state.current_track in app.state.subtitle_tracks:
        entries = app.state.subtitle_tracks[app.state.current_track]
        new_index = find_subtitle_index(entries, req.time_ms)
    else:
        new_index = 0

    # Only broadcast if index changed
    if new_index != old_index:
        app.state.current_index = new_index
        await broadcast_subtitle_delta(old_index, new_index)

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


@app.websocket("/ws/{session_id}")
async def session_websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for session-specific real-time subtitle updates"""
    await websocket.accept()

    # Check if session exists
    session = get_session(session_id)
    if not session:
        logger.warning(f"WebSocket connection attempted for non-existent session {session_id}")
        await websocket.close(code=1008, reason="Session not found")
        return

    # Check client limit
    total_clients = sum(len(s.connected_clients) for s in app.state.sessions.values())
    if total_clients >= config.WS_MAX_CLIENTS:
        logger.warning("WebSocket client limit reached")
        await websocket.close(code=1008, reason="Server at capacity")
        return

    session.connected_clients.add(websocket)
    client_id = id(websocket)
    logger.info(
        f"Session WebSocket client connected to {session_id} (id={client_id}), "
        f"session clients: {len(session.connected_clients)}"
    )

    try:
        # Send initial data for this session
        await send_tracks_for_session(websocket, session)
        await send_initial_subtitles_for_session(websocket, session)

        # Keep connection alive and handle messages
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "selectTrack":
                track = data.get("track")
                logger.info(f"Client {client_id} selected track: {track} for session {session_id}")
                await handle_session_track_selection(session_id, track)

    except WebSocketDisconnect:
        logger.info(f"Session WebSocket client disconnected from {session_id} (id={client_id})")
    except Exception as e:
        logger.error(f"Session WebSocket error for client {client_id}: {e}", exc_info=True)
    finally:
        session.connected_clients.discard(websocket)
        logger.info(
            f"Client {client_id} removed from session {session_id}, "
            f"session clients: {len(session.connected_clients)}"
        )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for global session list updates"""
    await websocket.accept()

    # Check client limit
    total_clients = (
        sum(len(s.connected_clients) for s in app.state.sessions.values())
        + len(app.state.global_clients)
    )
    if total_clients >= config.WS_MAX_CLIENTS:
        logger.warning("WebSocket client limit reached")
        await websocket.close(code=1008, reason="Server at capacity")
        return

    app.state.global_clients.add(websocket)
    client_id = id(websocket)
    logger.info(
        f"Global WebSocket client connected (id={client_id}), "
        f"total global clients: {len(app.state.global_clients)}"
    )

    try:
        # Send initial session list
        await broadcast_sessions_list()

        # Keep connection alive (no messages expected from global clients)
        while True:
            # Just wait for disconnection
            await websocket.receive_text()

    except WebSocketDisconnect:
        logger.info(f"Global WebSocket client disconnected (id={client_id})")
    except Exception as e:
        logger.error(f"Global WebSocket error for client {client_id}: {e}", exc_info=True)
    finally:
        app.state.global_clients.discard(websocket)
        logger.info(
            f"Global client {client_id} removed, total global clients: {len(app.state.global_clients)}"
        )


async def handle_session_track_selection(session_id: str, track: str):
    """Handle subtitle track selection for a specific session"""
    session = get_session(session_id)
    if not session:
        return

    if track in session.subtitle_tracks:
        session.current_track = track
        logger.info(f"Session {session_id} switched to track: {track}")

        # Recalculate index for new track at current time
        entries = session.subtitle_tracks[track]
        session.current_index = find_subtitle_index(entries, session.current_time_ms)

        # Send new initial state to all clients of this session
        for client in list(session.connected_clients):
            try:
                await send_initial_subtitles_for_session(client, session)
            except Exception as e:
                logger.error(f"Error sending initial subtitles after track change: {e}")
    else:
        logger.warning(f"Invalid track selection for session {session_id}: {track}")


async def handle_track_selection(track: str):
    """Handle subtitle track selection from client (legacy)"""
    if track in app.state.subtitle_tracks:
        app.state.current_track = track
        logger.info(f"Switched to track: {track}")

        # Recalculate index for new track at current time
        entries = app.state.subtitle_tracks[track]
        app.state.current_index = find_subtitle_index(entries, app.state.current_time_ms)

        # Send new initial state to all clients
        for client in list(app.state.connected_clients):
            try:
                await send_initial_subtitles(client)
            except Exception as e:
                logger.error(f"Error sending initial subtitles after track change: {e}")
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


async def broadcast_subtitle_delta(old_index: int, new_index: int):
    """Broadcast only the subtitle changes (delta) to all clients"""
    if not app.state.connected_clients:
        return

    if app.state.current_track not in app.state.subtitle_tracks:
        return

    entries = app.state.subtitle_tracks[app.state.current_track]
    delta = calculate_subtitle_delta(old_index, new_index, entries)

    match delta:
        case None:
            return
        case AddSubtitles(subtitles=subs):
            disconnected = []
            for client in list(app.state.connected_clients):
                try:
                    for subtitle in subs:
                        await client.send_json({"type": "subtitle_add", "subtitle": subtitle})
                except Exception as e:
                    logger.error(f"Error broadcasting subtitle additions to client: {e}")
                    disconnected.append(client)
            for client in disconnected:
                app.state.connected_clients.discard(client)
        case RemoveSubtitles(count=n):
            disconnected = []
            for client in list(app.state.connected_clients):
                try:
                    await client.send_json({"type": "subtitle_remove", "count": n})
                except Exception as e:
                    logger.error(f"Error broadcasting subtitle removals to client: {e}")
                    disconnected.append(client)
            for client in disconnected:
                app.state.connected_clients.discard(client)


async def send_initial_subtitles(websocket: WebSocket):
    """Send initial full subtitle list to a newly connected client"""
    if app.state.current_track not in app.state.subtitle_tracks:
        await websocket.send_json({"type": "subtitles_init", "lines": []})
        return

    entries = app.state.subtitle_tracks[app.state.current_track]
    current_entries = entries[: app.state.current_index]

    await websocket.send_json(
        {
            "type": "subtitles_init",
            "lines": [{"text": e.text, "start_ms": e.start_ms} for e in current_entries],
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

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=args.log_level == "debug",
    )


if __name__ == "__main__":
    run()
