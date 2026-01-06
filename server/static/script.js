let ws;
let currentSubtitles = [];
let autoScroll = true;
let reconnectAttempts = 0;
let maxReconnectDelay = 30000; // 30 seconds max
let reconnectTimer = null;

const videoTitle = document.getElementById('video-title');
const trackSelector = document.getElementById('track-selector');
const subtitlesContainer = document.getElementById('subtitles');
const status = document.getElementById('status');

function getReconnectDelay() {
    // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s (max)
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), maxReconnectDelay);
    return delay;
}

function connect() {
    // Clear any existing reconnect timer
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }

    ws = new WebSocket('ws://localhost:8765/ws');

    ws.onopen = () => {
        status.textContent = 'Connected';
        status.style.color = 'var(--accent-color)';
        reconnectAttempts = 0; // Reset on successful connection
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'tracks') {
            updateTracks(data.tracks, data.currentTrack);
            if (data.videoTitle) {
                videoTitle.textContent = data.videoTitle;
            }
        } else if (data.type === 'subtitles') {
            updateSubtitles(data.lines);
        }
    };

    ws.onerror = (error) => {
        status.textContent = 'Connection error';
        status.style.color = '#ef4444';
    };

    ws.onclose = () => {
        const delay = getReconnectDelay();
        reconnectAttempts++;

        status.textContent = `Disconnected (reconnecting in ${Math.round(delay/1000)}s...)`;
        status.style.color = '#ef4444';

        // Stop reconnecting after 10 attempts
        if (reconnectAttempts > 10) {
            status.textContent = 'Disconnected (max retries exceeded)';
            return;
        }

        reconnectTimer = setTimeout(connect, delay);
    };
}

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

function updateSubtitles(lines) {
    if (JSON.stringify(lines) === JSON.stringify(currentSubtitles)) {
        return;
    }

    currentSubtitles = lines;
    subtitlesContainer.innerHTML = '';

    if (lines.length === 0) {
        subtitlesContainer.innerHTML = `
            <div class="empty-state">
                <h2>No subtitles yet</h2>
                <p>Subtitles will appear here as the video plays</p>
            </div>
        `;
        return;
    }

    lines.forEach(line => {
        const div = document.createElement('div');
        div.className = 'subtitle-line';

        const text = document.createElement('div');
        text.className = 'subtitle-text';
        text.textContent = line.text;

        const time = document.createElement('div');
        time.className = 'subtitle-time';
        time.textContent = formatTime(line.start_ms);

        div.appendChild(text);
        div.appendChild(time);
        subtitlesContainer.appendChild(div);
    });

    if (autoScroll) {
        scrollToBottom();
    }
}

function formatTime(ms) {
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const millis = ms % 1000;

    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${pad(millis, 3)}`;
}

function pad(num, length = 2) {
    return String(num).padStart(length, '0');
}

function scrollToBottom() {
    window.scrollTo({
        top: document.body.scrollHeight,
        behavior: 'smooth'
    });
}

trackSelector.addEventListener('change', (e) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'selectTrack',
            track: e.target.value
        }));
    }
});

let scrollTimeout;
window.addEventListener('scroll', () => {
    clearTimeout(scrollTimeout);
    const isAtBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 100;
    autoScroll = isAtBottom;
});

connect();
