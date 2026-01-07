--[[
MPV Subtitle Viewer
Language learning subtitle viewer for MPV

Configuration:
To customize, create ~/.config/mpv/script-opts/subtitle-viewer.conf with:
    keybind=Ctrl+Shift+s
    port=8765
    auto_open_browser=yes
    browser_command=xdg-open
]]--

local utils = require 'mp.utils'
local msg = require 'mp.msg'
local options = require 'mp.options'

-- User configuration
local opts = {
    keybind = "Ctrl+Shift+s",
    port = 8765,
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
local last_time = -1


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

    msg.info("Initializing server with " .. track_count .. " subtitle track(s)")
    http_post("/init", init_data)

    -- Start observing playback time
    mp.observe_property("playback-time", "number", on_time_update)
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

-- Start the server
local function start_server()
    if server_running then
        msg.info("Server already running")
        return
    end

    msg.info("Starting mpv_subserver on port " .. opts.port)

    -- Start the server as a subprocess with configured port
    local args = {"mpv_subserver", "--port", tostring(opts.port)}
    local res = mp.command_native_async({
        name = "subprocess",
        playback_only = false,
        args = args
    }, function(success, result, error)
        if not success then
            msg.error("Failed to start server: " .. (error or "unknown error"))
            server_running = false
        end
    end)

    server_running = true
    mp.osd_message("Starting subtitle viewer...", 2)

    -- Wait for server to be ready, then initialize
    wait_for_server(function()
        initialize_server()

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
end

-- Stop the server
local function stop_server()
    if not server_running then
        return
    end

    msg.info("Stopping server")

    -- Unobserve time updates
    mp.unobserve_property(on_time_update)

    -- Send shutdown signal
    http_post("/shutdown", {})

    server_running = false
    last_time = -1

    mp.osd_message("Subtitle viewer stopped", 2)
end

-- Toggle server on/off
local function toggle_viewer()
    if server_running then
        stop_server()
    else
        start_server()
    end
end

-- Register keybind
mp.add_key_binding(opts.keybind, "toggle-subtitle-viewer", toggle_viewer)

-- Cleanup on shutdown
mp.register_event("shutdown", function()
    if server_running then
        stop_server()
    end
end)

msg.info("Subtitle viewer loaded. Press " .. opts.keybind .. " to toggle.")
