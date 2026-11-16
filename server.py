"""DeskPanel: serves a PC dashboard (stats, shortcut buttons, clocks, weather) to a phone browser."""
import ipaddress, json, os, queue, secrets, shutil, socket, subprocess, sys, threading, time, mimetypes, re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
import psutil

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
# The distributable repository ships only a public example, never a user's key.
# Copy it on first launch; then migrate a previous local key when available.
if not os.path.exists(CFG_PATH):
    shutil.copyfile(os.path.join(BASE, "config.example.json"), CFG_PATH)
with open(CFG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)
# Reuse a locally generated key from a neighboring older installation when possible.
# Never bundle the user's key in redistributed archives.
if not cfg.get("key"):
    for old_folder in ("DeskPanel-v6", "DeskPanel-v5", "DeskPanel-v4", "DeskPanel-v3", "DeskPanel-v2", "DeskPanel"):
        old_cfg = os.path.join(os.path.dirname(BASE), old_folder, "config.json")
        if os.path.abspath(old_cfg) == os.path.abspath(CFG_PATH):
            continue
        try:
            with open(old_cfg, encoding="utf-8") as old_file:
                previous_key = json.load(old_file).get("key")
            if isinstance(previous_key, str) and len(previous_key) >= 24:
                cfg["key"] = previous_key
                with open(CFG_PATH, "w", encoding="utf-8") as current_file:
                    json.dump(cfg, current_file, indent=2, ensure_ascii=False)
                print("Reused existing access key from " + old_folder)
                break
        except (OSError, ValueError, TypeError):
            pass
if not isinstance(cfg.get("key"), str) or len(cfg.get("key", "")) < 24:
    if cfg.get("key"):
        print("Replacing short legacy access key with a stronger generated key.")
    cfg["key"] = secrets.token_urlsafe(32)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

DEFAULT_APPS = [
    {"label": "VS Code", "type": "command", "value": "code"},
    {"label": "Chrome", "type": "command", "value": 'start "" chrome'},
    {"label": "Spotify", "type": "open", "value": "spotify:"},
    {"label": "Discord", "type": "open", "value": "discord://"},
    {"label": "Steam", "type": "open", "value": "steam://open/main"},
    {"label": "Terminal", "type": "command", "value": "wt"},
]

state = {"cpu": 0, "ram": 0, "ram_gb": [0, 0], "disk": 0, "down": 0, "up": 0, "gpu": None, "gpu_err": "", "media": None, "vol": None, "muted": False, "out": None, "out_id": None, "audio_error": None,
         "hist": [], "rhist": [], "ghist": [], "nhist": [],
         "cpu_each": [], "cpu_freq": None, "cpu_temp": None, "disk_gb": [0, 0], "disk_read": 0, "disk_write": 0,
         "net_total": [0, 0], "uptime": 0, "processes": 0}

def push(key, value):
    state[key] = (state[key] + [round(value, 1)])[-60:]

def num(text):
    try:
        return float(text.strip())
    except ValueError:
        return None

def gpu():
    exe = shutil.which("nvidia-smi") or next((p for p in (
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Windows\Sysnative\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe") if os.path.exists(p)), None)
    if not exe:
        state["gpu_err"] = "nvidia-smi not found"
        return None
    try:
        res = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,temperature.gpu,power.draw,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = res.stdout.strip().splitlines()
        parts = (lines[0].split(",") if lines else []) + ["", "", "", "", ""]
        load, temp, power, used, total = (num(x) for x in parts[:5])
        if load is None:
            state["gpu_err"] = (res.stderr.strip() or res.stdout.strip() or "no output")[:70]
            return None
        state["gpu_err"] = ""
        return {"load": load, "temp": temp, "power": power, "vram_used": used, "vram_total": total}
    except subprocess.TimeoutExpired:
        state["gpu_err"] = "nvidia-smi timed out"
    except Exception as e:
        state["gpu_err"] = str(e)[:70]
    return None

def gpu_loop():
    while True:
        state["gpu"] = gpu()
        time.sleep(2)

def safe(fn, default=0):
    try:
        return fn()
    except Exception:
        return default

def sampler():
    last, t0, n = psutil.net_io_counters(), time.time(), 0
    disk_last = psutil.disk_io_counters()
    psutil.cpu_percent(None)
    disk = cfg.get("disk", "C:\\" if os.name == "nt" else "/")
    while True:
        time.sleep(1)
        now, t1 = psutil.net_io_counters(), time.time()
        dt = max(t1 - t0, 0.001)
        state["down"] = (now.bytes_recv - last.bytes_recv) / dt
        state["up"] = (now.bytes_sent - last.bytes_sent) / dt
        last, t0 = now, t1
        state["cpu"] = psutil.cpu_percent(None)
        state["cpu_each"] = psutil.cpu_percent(None, percpu=True)
        freq = safe(psutil.cpu_freq, None)
        state["cpu_freq"] = round(freq.current) if freq else None
        state["uptime"] = max(0, int(time.time() - psutil.boot_time()))
        state["net_total"] = [round(now.bytes_recv / 2**30, 2), round(now.bytes_sent / 2**30, 2)]
        disk_now = safe(psutil.disk_io_counters, None)
        if disk_now and disk_last:
            state["disk_read"] = max(0, (disk_now.read_bytes - disk_last.read_bytes) / dt)
            state["disk_write"] = max(0, (disk_now.write_bytes - disk_last.write_bytes) / dt)
        disk_last = disk_now
        state["ram"] = psutil.virtual_memory().percent
        state["disk"] = safe(lambda: psutil.disk_usage(disk).percent)
        usage = safe(lambda: psutil.disk_usage(disk), None)
        if usage:
            state["disk_gb"] = [round(usage.used / 2**30, 1), round(usage.total / 2**30, 1)]
        if n % 5 == 0:
            state["processes"] = len(psutil.pids())
        vm = psutil.virtual_memory()
        state["ram_gb"] = [round((vm.total - vm.available) / 2**30, 1), round(vm.total / 2**30)]
        push("hist", state["cpu"])
        push("rhist", state["ram"])
        push("nhist", state["down"] / 1e3)
        push("ghist", (state["gpu"] or {}).get("load", 0))
        n += 1

def temperature_loop():
    """Optional CPU sensor: psutil where available or LibreHardwareMonitor WMI."""
    while True:
        temp = None
        try:
            sensors = psutil.sensors_temperatures()
            readings = [(v.current, k) for k, values in sensors.items() for v in values
                        if v.current is not None and -10 < v.current < 125]
            preferred = [v for v, k in readings if "cpu" in k.lower() or "core" in k.lower()]
            if preferred:
                temp = round(max(preferred), 1)
        except (AttributeError, OSError):
            pass
        if temp is None and os.name == "nt":
            try:
                import pythoncom
                import wmi  # optional: LibreHardwareMonitor must expose WMI
                pythoncom.CoInitialize()
                for sensor in wmi.WMI(namespace="root\\LibreHardwareMonitor").Sensor():
                    if sensor.SensorType == "Temperature" and "CPU" in sensor.Name.upper():
                        temp = round(float(sensor.Value), 1)
                        break
            except Exception:
                pass
        state["cpu_temp"] = temp
        time.sleep(8)

wcache = {"t": 0, "data": None}

def weather():
    if wcache["data"] and time.time() - wcache["t"] < 600:
        return wcache["data"]
    w = cfg.get("weather", {})
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,apparent_temperature,weather_code"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=3"
           % (w.get("lat", 37.98), w.get("lon", 23.73)))
    try:
        with urlopen(url, timeout=8) as r:
            wcache["data"], wcache["t"] = json.load(r), time.time()
    except Exception:
        pass
    return wcache["data"]

VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}

def run_action(b):
    kind, value = b.get("type"), b.get("value", "")
    if os.name != "nt":
        raise RuntimeError("Button actions only work on Windows")
    import ctypes
    if kind == "media":
        ctypes.windll.user32.keybd_event(VK[value], 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK[value], 0, 2, 0)
    elif kind == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif kind == "open":
        os.startfile(os.path.expandvars(os.path.expanduser(value)))
    elif kind == "command":
        # Legacy shell commands are allowed ONLY from the local PC config file.
        # Prefer "exec" actions (explicit argv) for new shortcuts.
        subprocess.Popen(value, shell=True)
    elif kind == "exec":
        args = b.get("args", [])
        if not isinstance(value, str) or not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ValueError("Invalid executable action")
        subprocess.Popen([os.path.expandvars(value), *args], shell=False)
    else:
        raise ValueError("Unknown button type: %s" % kind)

volq = queue.Queue()
_pc = []
art = {"data": b"", "type": "image/jpeg"}

def policy_config():
    """Undocumented Windows interface that sets the default audio device."""
    if not _pc:
        from comtypes import COMMETHOD, GUID, IUnknown, CoCreateInstance, CLSCTX_ALL
        from ctypes import HRESULT, c_int, c_wchar_p

        class IPolicyConfigVista(IUnknown):
            _iid_ = GUID("{568b9108-44bf-40b4-9006-86afe5b5a620}")
            _methods_ = tuple(COMMETHOD([], HRESULT, n) for n in (
                "GetMixFormat", "GetDeviceFormat", "SetDeviceFormat", "GetProcessingPeriod", "SetProcessingPeriod",
                "GetShareMode", "SetShareMode", "GetPropertyValue", "SetPropertyValue")) + (
                COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], c_wchar_p, "id"), (["in"], c_int, "role")),
                COMMETHOD([], HRESULT, "SetEndpointVisibility"))
        _pc.append(CoCreateInstance(GUID("{294935CE-F637-4E7C-A41B-AB255460B862}"), IPolicyConfigVista, CLSCTX_ALL))
    return _pc[0]

def render_devices():
    """Active Windows render (speaker/headphone/HDMI) endpoints only."""
    from pycaw.pycaw import AudioUtilities
    found = {}
    for dev in AudioUtilities.GetAllDevices():
        device_id = str(getattr(dev, "id", ""))
        state_value = getattr(dev, "state", None)
        is_active = (getattr(state_value, "value", state_value) == 1 or
                     str(state_value).lower() in ("active", "1", "devicestate.active", "device_state.active"))
        if device_id.startswith("{0.0.0") and is_active:
            found[device_id] = str(getattr(dev, "FriendlyName", None) or getattr(dev, "friendly_name", None) or device_id)
    return found


def set_output(device_id):
    """Set a validated active output; report Windows COM errors to the caller."""
    endpoints = render_devices()
    if device_id not in endpoints:
        raise ValueError("This output is no longer available. Refresh the list.")
    config = policy_config()
    for role in (0, 1, 2):
        config.SetDefaultEndpoint(device_id, role)
    # Windows sometimes accepts a COM call without applying the requested change.
    from pycaw.pycaw import AudioUtilities
    time.sleep(0.25)
    active_id = str(getattr(AudioUtilities.GetSpeakers(), "id", ""))
    if active_id != device_id:
        raise RuntimeError("Windows did not change the default audio output. Check Windows Sound settings and permissions.")
    return endpoints[device_id]


def audio_request(kind, value=None, timeout=7):
    """Execute on the COM-initialized audio thread and wait for the actual result."""
    response = queue.Queue(maxsize=1)
    volq.put((kind, value, response))
    try:
        ok, result = response.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError("Audio service is not responding. Check that pycaw is installed.")
    if not ok:
        raise RuntimeError(str(result))
    return result


def volume_loop():
    """Audio operations must stay on the same COM-initialized Windows thread."""
    try:
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
    except Exception as exc:
        state["audio_error"] = "Audio initialization failed: " + str(exc)
        return

    def acquire():
        dev = AudioUtilities.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            endpoint = dev.EndpointVolume
        else:
            endpoint = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
        return endpoint, str(getattr(dev, "id", None) or dev.GetId())

    count = 0
    names = {}
    while True:
        try:
            try:
                cmd = volq.get(timeout=1)
            except queue.Empty:
                cmd = None
            if cmd:
                kind, value, response = cmd
                try:
                    if kind == "list":
                        names = render_devices()
                        endpoint, current = acquire()
                        result = {"outputs": [{"id": i, "name": n, "active": i == current} for i, n in names.items()], "current": current}
                    elif kind == "out":
                        result = {"name": set_output(value)}
                        time.sleep(.2)
                    elif kind == "set":
                        endpoint, current = acquire()
                        endpoint.SetMasterVolumeLevelScalar(max(0, min(1, float(value) / 100)), None)
                        result = {"ok": True}
                    elif kind == "mute":
                        endpoint, current = acquire()
                        endpoint.SetMute(0 if endpoint.GetMute() else 1, None)
                        result = {"ok": True}
                    else:
                        raise ValueError("Unknown audio operation")
                    response.put((True, result))
                    state["audio_error"] = None
                except Exception as exc:
                    response.put((False, str(exc)))
                    state["audio_error"] = str(exc)
            endpoint, current = acquire()
            state["vol"] = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            state["muted"] = bool(endpoint.GetMute())
            if count % 10 == 0:
                names = render_devices()
            count += 1
            state["out"] = names.get(current) or str(current)
            state["out_id"] = current
        except Exception as exc:
            state["audio_error"] = str(exc)

def media_loop():
    """Track info, cover art and progress via Windows media controls (optional winrt packages)."""
    import importlib
    try:
        import asyncio
        pkg = "winrt"
        try:
            importlib.import_module("winrt.windows.media.control")
        except ImportError:
            pkg = "winsdk"
            importlib.import_module("winsdk.windows.media.control")
        Manager = importlib.import_module(pkg + ".windows.media.control").GlobalSystemMediaTransportControlsSessionManager
    except Exception:
        return

    async def read_thumb(props):
        try:
            ref = props.thumbnail
            if not ref:
                return b""
            stream = await ref.open_read_async()
            reader = importlib.import_module(pkg + ".windows.storage.streams").DataReader(stream)
            await reader.load_async(stream.size)
            data = bytearray(stream.size)
            reader.read_bytes(data)
            return bytes(data)
        except Exception:
            return b""

    async def poll():
        from datetime import datetime, timezone
        mgr = await Manager.request_async()
        last, tries = None, 0
        while True:
            try:
                sess = mgr.get_current_session()
                if not sess:
                    state["media"], last = None, None
                    art["data"] = b""
                else:
                    props = await sess.try_get_media_properties_async()
                    key = (props.title, props.artist)
                    if key != last:
                        last, tries = key, 0
                        art["data"] = b""
                    if not art["data"] and tries < 6:
                        tries += 1
                        art["data"] = await read_thumb(props)
                        art["type"] = "image/png" if art["data"][:4] == b"\x89PNG" else "image/jpeg"
                    tl = sess.get_timeline_properties()
                    dur = (tl.end_time - tl.start_time).total_seconds()
                    pos = tl.position.total_seconds()
                    playing = sess.get_playback_info().playback_status == 4
                    if playing:
                        try:
                            pos += (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds()
                        except Exception:
                            pass
                    source_id = str(getattr(sess, "source_app_user_model_id", "") or "")
                    state["media"] = {"title": props.title, "artist": props.artist, "playing": playing, "source": source_id,
                                      "pos": max(0, min(pos, dur)) if dur > 0 else 0, "dur": max(dur, 0),
                                      "art": str(abs(hash(key))) if art["data"] else None}
            except Exception:
                state["media"] = None
            await asyncio.sleep(2)
    asyncio.run(poll())

# This is LAN-only and HTTP, not suitable for exposing to the internet.
ALLOWED_STATIC = {"/": "index.html", "/style.css": "static/style.css", "/app.js": "static/app.js"}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        key = (parse_qs(urlparse(self.path).query).get("k") or [""])[0]
        return (ip.is_private or ip.is_loopback) and secrets.compare_digest(key.encode(), str(cfg["key"]).encode())

    def valid_post_origin(self):
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site == "cross-site":
            return False
        if origin:
            from urllib.parse import urlsplit
            try:
                host = self.headers.get("Host", "")
                parsed = urlsplit(origin)
                return parsed.scheme == "http" and parsed.netloc == host
            except ValueError:
                return False
        # Some older Safari versions do not send Origin on same-origin POST.
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        # Non-sensitive static assets are public so Safari can request them normally.
        if path not in ("/style.css", "/app.js") and not self.allowed():
            return self.reply(403, {"error": "forbidden"})
        if path in ALLOWED_STATIC:
            file = os.path.join(BASE, ALLOWED_STATIC[path])
            mime = "text/html; charset=utf-8" if path == "/" else (
                "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8")
            with open(file, "rb") as f:
                self.reply(200, f.read(), mime)
        elif path == "/api/stats":
            self.reply(200, state)
        elif path == "/api/art":
            if art["data"]:
                self.reply(200, art["data"], art["type"])
            else:
                self.reply(404, {"error": "no art"})
        elif path == "/api/outputs":
            try:
                self.reply(200, audio_request("list"))
            except Exception as exc:
                self.reply(503, {"error": str(exc)})
        elif path == "/api/weather":
            self.reply(200, weather() or {})
        elif path == "/api/config":
            self.reply(200, {
                "buttons": [{"label": b.get("label", "?"), "icon": b.get("icon"), "color": b.get("color")} for b in cfg.get("buttons", [])],
                "apps": [{"label": b.get("label", "?"), "icon": b.get("icon"), "color": b.get("color")} for b in cfg.get("apps", DEFAULT_APPS)],
                "host": socket.gethostname(), "clocks": cfg.get("clocks", []), "hour12": cfg.get("hour12", False),
                "weather": {"place": cfg.get("weather", {}).get("place", ""),
                            "fahrenheit": cfg.get("weather", {}).get("fahrenheit", False)}})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not self.allowed() or not self.valid_post_origin():
            return self.reply(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        if path == "/api/output":
            try:
                device_id = (parse_qs(urlparse(self.path).query).get("id") or [None])[0]
                if not device_id:
                    return self.reply(400, {"error": "Select an output device"})
                return self.reply(200, audio_request("out", device_id))
            except Exception as exc:
                return self.reply(503, {"error": str(exc)})
        if path == "/api/mute":
            try:
                if state["vol"] is None:
                    run_action({"type": "media", "value": "mute"})
                else:
                    audio_request("mute")
                return self.reply(200, {"ok": True})
            except Exception as e:
                return self.reply(500, {"error": str(e)})
        if path == "/api/volume":
            try:
                audio_request("set", float(parse_qs(urlparse(self.path).query)["v"][0]))
                return self.reply(200, {"ok": True})
            except Exception as e:
                return self.reply(500, {"error": str(e)})
        if path == "/api/media":
            try:
                key = parse_qs(urlparse(self.path).query)["v"][0]
                if key not in VK:
                    raise ValueError("unknown media key")
                run_action({"type": "media", "value": key})
                return self.reply(200, {"ok": True})
            except Exception as e:
                return self.reply(500, {"error": str(e)})
        if path != "/api/press":
            return self.reply(404, {"error": "not found"})
        try:
            i = int(parse_qs(urlparse(self.path).query)["i"][0])
            group = (parse_qs(urlparse(self.path).query).get("g") or ["buttons"])[0]
            if group not in ("apps", "buttons"):
                raise ValueError("unknown group")
            entries = cfg.get("apps", DEFAULT_APPS) if group == "apps" else cfg.get("buttons", [])
            if not 0 <= i < len(entries):
                raise IndexError("invalid action index")
            run_action(entries[i])
            self.reply(200, {"ok": True})
        except Exception as e:
            self.reply(500, {"error": str(e)})

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

if __name__ == "__main__":
    if "--gpu" in sys.argv:
        print("nvidia-smi:", shutil.which("nvidia-smi"))
        print("result:", gpu(), "| error:", state["gpu_err"] or "none")
        raise SystemExit
    if "--devices" in sys.argv:
        for name in render_devices().values():
            print(name)
        raise SystemExit
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=gpu_loop, daemon=True).start()
    threading.Thread(target=media_loop, daemon=True).start()
    threading.Thread(target=volume_loop, daemon=True).start()
    threading.Thread(target=temperature_loop, daemon=True).start()
    port = cfg.get("port", 8765)
    print("\nOpen this on your iPhone (same Wi-Fi):\n  http://%s:%d/?k=%s\n" % (lan_ip(), port, cfg["key"]))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
