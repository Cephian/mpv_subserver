// State management
let globalWs = null;  // Connection to /ws for session list
let sessionWs = null;  // Connection to /ws/{session_id} for subtitles
let currentSessionId = null;
let sessions = [];
let currentSubtitles = [];
let autoScroll = true;
let reconnectAttempts = 0;
let maxReconnectDelay = 30000; // 30 seconds max
let reconnectTimer = null;

// DOM elements
const sessionPicker = document.getElementById('session-picker');
const subtitleViewer = document.getElementById('subtitle-viewer');
const sessionsList = document.getElementById('sessions-list');
const videoTitle = document.getElementById('video-title');
const trackSelector = document.getElementById('track-selector');
const subtitlesContainer = document.getElementById('subtitles');
const status = document.getElementById('status');
const backButton = document.getElementById('back-button');

// View switching
function showSessionPicker() {
    sessionPicker.style.display = 'block';
    subtitleViewer.style.display = 'none';
}

function showSubtitleViewer() {
    sessionPicker.style.display = 'none';
    subtitleViewer.style.display = 'block';
}

// Utility functions
function getReconnectDelay() {
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), maxReconnectDelay);
    return delay;
}

function formatRelativeTime(timestamp) {
    const now = Date.now() / 1000;
    const diff = now - timestamp;

    if (diff < 60) {
        return 'just now';
    } else if (diff < 3600) {
        const mins = Math.floor(diff / 60);
        return `${mins} minute${mins > 1 ? 's' : ''} ago`;
    } else if (diff < 86400) {
        const hours = Math.floor(diff / 3600);
        return `${hours} hour${hours > 1 ? 's' : ''} ago`;
    } else {
        const days = Math.floor(diff / 86400);
        return `${days} day${days > 1 ? 's' : ''} ago`;
    }
}

// Global WebSocket (session list)
function connectGlobal() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    globalWs = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);

    globalWs.onopen = () => {
        console.log('Connected to global session list');
        reconnectAttempts = 0;
    };

    globalWs.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            if (data.type === 'sessions_list') {
                sessions = data.sessions;
                updateSessionsList(data.sessions);

                // Only auto-select if exactly ONE session and none currently selected
                if (!currentSessionId && data.sessions.length === 1) {
                    console.log('Auto-selecting single session');
                    connectToSession(data.sessions[0].session_id);
                } else if (data.sessions.length === 0) {
                    // No sessions, show picker
                    showSessionPicker();
                } else if (!currentSessionId) {
                    // Multiple sessions, let user choose
                    showSessionPicker();
                }
            } else if (data.type === 'session_added') {
                console.log('Session added:', data.session);
                // Refresh session list
                sessions.push(data.session);
                updateSessionsList(sessions);
            } else if (data.type === 'session_removed') {
                console.log('Session removed:', data.session_id);
                sessions = sessions.filter(s => s.session_id !== data.session_id);
                updateSessionsList(sessions);

                if (currentSessionId === data.session_id) {
                    disconnectFromSession();
                }
            }
        } catch (error) {
            console.error('Error parsing global WebSocket message:', error);
        }
    };

    globalWs.onerror = (error) => {
        console.error('Global WebSocket error:', error);
    };

    globalWs.onclose = () => {
        const delay = getReconnectDelay();
        reconnectAttempts++;

        console.log(`Global WebSocket closed, reconnecting in ${Math.round(delay/1000)}s...`);

        if (reconnectAttempts <= 10) {
            reconnectTimer = setTimeout(connectGlobal, delay);
        }
    };
}

// Session-specific WebSocket (subtitles)
function connectToSession(sessionId) {
    currentSessionId = sessionId;

    // Disconnect existing session WebSocket if any
    if (sessionWs) {
        sessionWs.close();
    }

    // Show loading state
    status.textContent = 'Connecting...';
    status.style.color = '#6b7280';
    showSubtitleViewer();

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    sessionWs = new WebSocket(`${wsProtocol}//${window.location.host}/ws/${sessionId}`);

    sessionWs.onopen = () => {
        console.log('Connected to session:', sessionId);
        status.textContent = 'Connected';
        status.style.color = 'var(--accent-color)';
        reconnectAttempts = 0;  // Reset reconnect attempts on successful connection
    };

    sessionWs.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            if (data.type === 'tracks') {
                updateTracks(data.tracks, data.currentTrack);
                if (data.videoTitle) {
                    videoTitle.textContent = data.videoTitle;
                }
            } else if (data.type === 'subtitles_init') {
                setSubtitles(data.lines);
            } else if (data.type === 'subtitle_add') {
                addSubtitle(data.subtitle);
            } else if (data.type === 'subtitle_remove') {
                removeSubtitles(data.count);
            } else if (data.type === 'session_closed') {
                console.log('Session closed by server');
                status.textContent = 'Session closed';
                status.style.color = '#ef4444';
                setTimeout(() => disconnectFromSession(), 1000);
            }
        } catch (error) {
            console.error('Error parsing session WebSocket message:', error);
        }
    };

    sessionWs.onerror = (error) => {
        console.error('Session WebSocket error:', error);
        status.textContent = 'Connection error';
        status.style.color = '#ef4444';
    };

    sessionWs.onclose = async () => {
        console.log('Session WebSocket closed');
        status.textContent = 'Disconnected';
        status.style.color = '#ef4444';

        // Only attempt reconnection if we haven't manually disconnected
        if (currentSessionId === sessionId && reconnectAttempts < 5) {
            // Check if session still exists before attempting reconnection
            try {
                const response = await fetch(`/session/${sessionId}/health`);
                if (response.status === 404) {
                    console.log('Session no longer exists on server');
                    status.textContent = 'Session expired';
                    setTimeout(() => disconnectFromSession(), 2000);
                    return;
                }
            } catch (error) {
                console.error('Error checking session health:', error);
            }

            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 10000);
            reconnectAttempts++;
            console.log(`Attempting to reconnect to session in ${delay}ms...`);
            status.textContent = `Reconnecting... (${reconnectAttempts}/5)`;
            setTimeout(() => {
                if (currentSessionId === sessionId) {  // Check we haven't moved on
                    connectToSession(sessionId);
                }
            }, delay);
        } else {
            // Give up, return to picker
            setTimeout(() => disconnectFromSession(), 2000);
        }
    };
}

function disconnectFromSession() {
    if (sessionWs) {
        sessionWs.close();
        sessionWs = null;
    }
    currentSessionId = null;
    currentSubtitles = [];
    reconnectAttempts = 0;  // Reset reconnect attempts
    showSessionPicker();
}

// Session list UI
function updateSessionsList(sessionList) {
    sessionsList.innerHTML = '';

    if (sessionList.length === 0) {
        sessionsList.innerHTML = `
            <div class="empty-state">
                <h2>No active sessions</h2>
                <p>Start MPV with subtitle viewer enabled.</p>
            </div>
        `;
        return;
    }

    // Sort by most recently active
    sessionList.sort((a, b) => b.last_activity - a.last_activity);

    sessionList.forEach(session => {
        const card = document.createElement('div');
        card.className = 'session-card';
        card.innerHTML = `
            <h3>${session.video_title || 'Untitled Video'}</h3>
            <p class="session-time">Active ${formatRelativeTime(session.last_activity)}</p>
            <p class="session-clients">${session.connected_clients} viewer${session.connected_clients !== 1 ? 's' : ''}</p>
        `;
        card.onclick = () => connectToSession(session.session_id);
        sessionsList.appendChild(card);
    });
}

// Subtitle UI
function updateTracks(tracks, currentTrack) {
    trackSelector.innerHTML = '';
    tracks.forEach(track => {
        const option = document.createElement('option');
        option.value = track;
        option.textContent = track;
        if (track === currentTrack) {
            option.selected = true;
        }
        trackSelector.appendChild(option);
    });
}

function createSubtitleElement(subtitle) {
    const div = document.createElement('div');
    div.className = 'subtitle-line';

    const text = document.createElement('div');
    text.className = 'subtitle-text';
    text.textContent = subtitle.text;

    div.appendChild(text);
    return div;
}

function showEmptyState() {
    subtitlesContainer.innerHTML = `
        <div class="empty-state">
            <h2>No subtitles yet</h2>
            <p>Subtitles will appear here as the video plays</p>
        </div>
    `;
}

function setSubtitles(lines) {
    currentSubtitles = lines;
    subtitlesContainer.innerHTML = '';

    if (lines.length === 0) {
        showEmptyState();
        return;
    }

    lines.forEach(subtitle => {
        subtitlesContainer.appendChild(createSubtitleElement(subtitle));
    });

    if (autoScroll) {
        scrollToBottom();
    }
}

function addSubtitle(subtitle) {
    // Remove empty state if present
    const emptyState = subtitlesContainer.querySelector('.empty-state');
    if (emptyState) {
        subtitlesContainer.innerHTML = '';
    }

    currentSubtitles.push(subtitle);
    subtitlesContainer.appendChild(createSubtitleElement(subtitle));

    if (autoScroll) {
        scrollToBottom();
    }
}

function removeSubtitles(count) {
    currentSubtitles.splice(currentSubtitles.length - count, count);

    const children = subtitlesContainer.children;
    for (let i = 0; i < count; i++) {
        if (children.length > 0) {
            subtitlesContainer.removeChild(children[children.length - 1]);
        }
    }

    if (currentSubtitles.length === 0) {
        showEmptyState();
    }
}

function scrollToBottom() {
    window.scrollTo({
        top: document.body.scrollHeight,
        behavior: 'smooth'
    });
}

// Event listeners
trackSelector.addEventListener('change', (e) => {
    if (sessionWs && sessionWs.readyState === WebSocket.OPEN) {
        sessionWs.send(JSON.stringify({
            type: 'selectTrack',
            track: e.target.value
        }));
    }
});

backButton.addEventListener('click', () => {
    disconnectFromSession();
});

let scrollTimeout;
window.addEventListener('scroll', () => {
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
        const isAtBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 100;
        autoScroll = isAtBottom;
    }, 100);
});

// Initialize
console.log('MPV Subtitle Viewer: Initializing...');
console.log('Session picker element:', sessionPicker);
console.log('Subtitle viewer element:', subtitleViewer);

try {
    connectGlobal();
    showSessionPicker();
    console.log('MPV Subtitle Viewer: Initialized successfully');
} catch (error) {
    console.error('MPV Subtitle Viewer: Initialization failed:', error);
}
