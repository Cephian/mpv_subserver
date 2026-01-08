--[[
MPV Subtitle Viewer
Language learning subtitle viewer for MPV
]]--

local utils = require 'mp.utils'
local msg = require 'mp.msg'
local options = require 'mp.options'

-- User configuration
local opts = {
    keybind = "Ctrl+Shift+s",
    port = 8768,
    auto_open_browser = true,
    browser_command = "xdg-open"
}
options.read_options(opts, "subtitle-viewer")

-- Internal configuration
local SERVER_HOST = "127.0.0.1"
local SERVER_URL = "http://" .. SERVER_HOST .. ":" .. opts.port

-- State
local server_process = nil
local server_running = false
local session_id = nil
local last_time = 0
local heartbeat_timer = nil


-- Read file contents
local function read_file(path)
    local file = io.open(path, "r")
    if not file then
        return nil
    end
    local content = file:read("*all")
    file:close()
    return content
end

-- HTTP request helper using curl
local function http_post(endpoint, json_data)
    local json_str = utils.format_json(json_data)
    local args = {
        "curl",
        "-X", "POST",
        "-H", "Content-Type: application/json",
        "-d", json_str,
        "--silent",
        "--max-time", "2",
        SERVER_URL .. endpoint
    }

    local res = mp.command_native({
        name = "subprocess",
        playback_only = false,
        capture_stdout = true,
        args = args
    })

    return res
end

-- Get all subtitle tracks
local function get_subtitle_tracks()
    local track_list = mp.get_property_native("track-list")
    local subtitle_tracks = {}

    for _, track in ipairs(track_list) do
        if track.type == "sub" and track["external-filename"] then
            local filename = track["external-filename"]
            local content = read_file(filename)
            if content then
                local basename = filename:match("([^/]+)$")
                subtitle_tracks[basename] = content
            end
        end
    end

    return subtitle_tracks
end

-- Count entries in a table (works for string-keyed tables)
local function count_table(t)
    local count = 0
    for _ in pairs(t) do
        count = count + 1
    end
    return count
end

-- Check if server is already running
local function is_server_running()
    local args = {
        "curl",
        "--silent",
        "--max-time", "1",
        SERVER_URL .. "/health"
    }

    local res = mp.command_native({
        name = "subprocess",
        playback_only = false,
        capture_stdout = true,
        args = args
    })

    return res.status == 0
end

-- Create a new session on the server
local function create_session()
    local res = http_post("/session/create", {})
    if res.status == 0 then
        local response = utils.parse_json(res.stdout)
        if response and response.session_id then
            return response.session_id
        end
    end
    return nil
end

-- Send heartbeat to keep session alive
local function send_heartbeat()
    if not server_running or not session_id then
        return
    end

    local res = http_post("/session/" .. session_id .. "/heartbeat", {})
    if res.status ~= 0 then
        msg.warn("Heartbeat failed, session may have expired")
    end
end

-- Start periodic heartbeat timer
local function start_heartbeat()
    if heartbeat_timer then
        heartbeat_timer:kill()
    end

    -- Send heartbeat every 60 seconds
    heartbeat_timer = mp.add_periodic_timer(60, send_heartbeat)
end

-- Stop heartbeat timer
local function stop_heartbeat()
    if heartbeat_timer then
        heartbeat_timer:kill()
        heartbeat_timer = nil
    end
end

-- Wait for session to be ready by checking session health
local function wait_for_session(sid, callback, max_retries)
    max_retries = max_retries or 10
    local retries = 0

    local function check()
        local args = {
            "curl",
            "--silent",
            "--max-time", "1",
            SERVER_URL .. "/session/" .. sid .. "/health"
        }

        local res = mp.command_native({
            name = "subprocess",
            playback_only = false,
            capture_stdout = true,
            args = args
        })

        if res.status == 0 then
            msg.info("Session " .. sid .. " is ready")
            callback()
        else
            retries = retries + 1
            if retries < max_retries then
                msg.info("Waiting for session... (attempt " .. retries .. "/" .. max_retries .. ")")
                mp.add_timeout(0.3, check)
            else
                msg.error("Session failed to become ready after " .. max_retries .. " attempts")
                mp.osd_message("Error: Session creation failed", 3)
                server_running = false
                session_id = nil
            end
        end
    end

    check()
end

-- Handle playback time updates
function on_time_update(name, value)
    if not server_running or not value then
        return
    end

    local current_time = math.floor(value * 1000)

    -- Only send updates if time changed significantly (more than 100ms)
    if math.abs(current_time - last_time) > 100 then
        last_time = current_time
        http_post("/time", {time_ms = current_time})
    end
end

-- Initialize server with video data
local function initialize_server()
    if not session_id then
        msg.error("Cannot initialize: no session ID")
        return false
    end

    local video_title = mp.get_property("media-title", "Unknown")
    local subtitle_tracks = get_subtitle_tracks()

    local track_count = count_table(subtitle_tracks)

    if track_count == 0 then
        msg.warn("No subtitle tracks found")
        mp.osd_message("Warning: No subtitle files found", 3)
    end

    local init_data = {
        video_title = video_title,
        subtitle_tracks = subtitle_tracks
    }

    msg.info("Initializing session " .. session_id .. " with " .. track_count .. " subtitle track(s)")
    local res = http_post("/session/" .. session_id .. "/init", init_data)

    if res.status ~= 0 then
        msg.error("Failed to initialize session")
        msg.error("Response status: " .. tostring(res.status))
        if res.stdout then
            msg.error("Response stdout: " .. res.stdout)
        end
        if res.stderr then
            msg.error("Response stderr: " .. res.stderr)
        end
        msg.error("Error string: " .. (res.error_string or "none"))
        return false
    end

    -- Start observing subtitle changes, seeking, and pause events
    mp.observe_property("sub-text", "string", on_subtitle_change)
    mp.observe_property("seeking", "bool", on_seeking)
    mp.observe_property("pause", "bool", on_pause)

    return true
end

-- Check if server is ready by polling health endpoint
local function wait_for_server(callback, max_retries)
    max_retries = max_retries or 10
    local retries = 0

    local function check()
        local args = {
            "curl",
            "--silent",
            "--max-time", "1",
            SERVER_URL .. "/health"
        }

        local res = mp.command_native({
            name = "subprocess",
            playback_only = false,
            capture_stdout = true,
            args = args
        })

        if res.status == 0 then
            msg.info("Server is ready")
            callback()
        else
            retries = retries + 1
            if retries < max_retries then
                msg.info("Waiting for server... (attempt " .. retries .. "/" .. max_retries .. ")")
                mp.add_timeout(0.5, check)
            else
                msg.error("Server failed to start after " .. max_retries .. " attempts")
                mp.osd_message("Error: Server failed to start", 3)
                server_running = false
            end
        end
    end

    check()
end

-- Start the server (daemon-aware)
local function start_server()
    if server_running then
        msg.info("Session already active")
        return
    end

    -- Check if server is already running (daemon mode)
    if is_server_running() then
        msg.info("Connecting to existing server on port " .. opts.port)
        server_running = true

        -- Create session
        local new_session_id = create_session()
        if not new_session_id then
            msg.error("Failed to create session")
            mp.osd_message("Error: Failed to create session", 3)
            server_running = false
            return
        end

        msg.info("Created session: " .. new_session_id)

        -- Wait for session to be ready, then initialize
        wait_for_session(new_session_id, function()
            session_id = new_session_id

            -- Initialize session
            if not initialize_server() then
                server_running = false
                session_id = nil
                return
            end

            -- Start heartbeat
            start_heartbeat()

            -- Open browser after session is ready (if enabled)
            if opts.auto_open_browser then
                local browser_url = "http://" .. SERVER_HOST .. ":" .. opts.port
                msg.info("Opening browser: " .. browser_url)
                mp.command_native_async({
                    name = "subprocess",
                    playback_only = false,
                    args = {opts.browser_command, browser_url}
                })
            end

            mp.osd_message("Subtitle viewer started", 2)
        end)

        return
    end

    -- Server not running, spawn it
    msg.info("Starting new mpv_subserver on port " .. opts.port)

    local args = {"mpv_subserver", "--port", tostring(opts.port)}
    mp.command_native_async({
        name = "subprocess",
        playback_only = false,
        args = args
    }, function(success, result, error)
        if not success then
            msg.error("Failed to start server: " .. (error or "unknown error"))
            server_running = false
            session_id = nil
        end
    end)

    server_running = true
    mp.osd_message("Starting subtitle viewer...", 2)

    -- Wait for server to be ready
    wait_for_server(function()
        -- Create session
        local new_session_id = create_session()
        if not new_session_id then
            msg.error("Failed to create session")
            mp.osd_message("Error: Failed to create session", 3)
            server_running = false
            return
        end

        msg.info("Created session: " .. new_session_id)

        -- Wait for session to be ready, then initialize
        wait_for_session(new_session_id, function()
            session_id = new_session_id

            -- Initialize session
            if not initialize_server() then
                server_running = false
                session_id = nil
                return
            end

            -- Start heartbeat
            start_heartbeat()

            -- Open browser after successful initialization (if enabled)
            if opts.auto_open_browser then
                local browser_url = "http://" .. SERVER_HOST .. ":" .. opts.port
                msg.info("Opening browser: " .. browser_url)
                mp.command_native_async({
                    name = "subprocess",
                    playback_only = false,
                    args = {opts.browser_command, browser_url}
                })
            else
                msg.info("Browser auto-open disabled. Navigate to: http://" .. SERVER_HOST .. ":" .. opts.port)
            end

            mp.osd_message("Subtitle viewer started", 2)
        end)
    end)
end

-- Send current playback time to server
local function send_time_update()
    if not server_running or not session_id then
        return
    end

    local time = mp.get_property_number("playback-time")
    if not time then
        return
    end

    local current_time = math.floor(time * 1000)
    local res = http_post("/session/" .. session_id .. "/time", {time_ms = current_time})

    -- If session not found (404), try to recreate
    if res.status ~= 0 then
        msg.warn("Failed to send time update, attempting to recreate session")
        session_id = create_session()
        if session_id then
            initialize_server()
        else
            msg.error("Failed to recreate session")
            server_running = false
        end
    end
end

-- Handle subtitle text changes
function on_subtitle_change(name, value)
    -- Subtitle changed (appeared, disappeared, or text changed)
    send_time_update()
end

-- Handle seeking
function on_seeking(name, value)
    if value == false then
        -- Seek just completed
        send_time_update()
    end
end

-- Handle pause state changes
function on_pause(name, value)
    -- Send update when pausing or unpausing to ensure sync
    send_time_update()
end

-- Stop the session (renamed from stop_server)
local function stop_session()
    if not server_running then
        return
    end

    msg.info("Stopping session")

    -- Stop heartbeat
    stop_heartbeat()

    -- Unobserve all properties
    mp.unobserve_property(on_subtitle_change)
    mp.unobserve_property(on_seeking)
    mp.unobserve_property(on_pause)

    -- Delete session (server may stay running for other sessions)
    if session_id then
        local args = {
            "curl",
            "-X", "DELETE",
            "--silent",
            "--max-time", "2",
            SERVER_URL .. "/session/" .. session_id
        }

        mp.command_native({
            name = "subprocess",
            playback_only = false,
            args = args
        })
    end

    session_id = nil
    server_running = false

    mp.osd_message("Subtitle viewer stopped", 2)
end

-- Toggle server on/off
local function toggle_viewer()
    if server_running then
        stop_session()
    else
        start_server()
    end
end

-- Register keybind
mp.add_key_binding(opts.keybind, "toggle-subtitle-viewer", toggle_viewer)

-- Cleanup on shutdown
mp.register_event("shutdown", function()
    if server_running then
        stop_session()
    end
end)

msg.info("Subtitle viewer loaded. Press " .. opts.keybind .. " to toggle.")
