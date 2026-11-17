"""Config, Razer battery reader and game detection for Mouse Battery Overlay."""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

APP_NAME = "MouseBatteryOverlay"
CONFIG_DIR = os.environ.get("MBO_HOME") or os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_PATH = os.path.join(CONFIG_DIR, "app.log")

DEFAULTS = {
    "corner": "top-right",          # top-left | top-right | bottom-left | bottom-right
    "opacity": 85,                  # 20..100 (percent)
    "hotkey": "ctrl+alt+b",         # toggles the overlay on/off
    "enabled": True,
    "tray_hint_shown": False,       # "still running in the tray" balloon shown once
    # Not in the GUI yet; edit config.json by hand if the auto-detect misses a game.
    "game_dirs": [],                # any exe under these folders counts as a game
    "game_exes": [],                # extra exe names always treated as games
    "fullscreen_means_game": True,  # borderless fullscreen window on its monitor = game
    "margin": 24,
    "poll_seconds": 60,
    "low_percent": 20,
}

IGNORE_EXES = {
    "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "vlc.exe", "mpc-hc64.exe",
    "obs64.exe", "searchhost.exe", "startmenuexperiencehost.exe", "applicationframehost.exe",
    "code.exe", "claude.exe", "discord.exe", "spotify.exe",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    cfg["opacity"] = max(20, min(100, int(cfg["opacity"])))
    if cfg["corner"] not in ("top-left", "top-right", "bottom-left", "bottom-right"):
        cfg["corner"] = DEFAULTS["corner"]
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def log(msg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 300_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- battery (auto-detect)
import devices

devices.log_fn = log
_last_provider = None
_accepted = None   # last accepted reading: (percent, charging, time)
_pending = None    # an out-of-line reading waiting for a second confirmation


def read_battery():
    """Probe every supported provider and return the first mouse that reports a battery:
    {'percent', 'charging', 'name'} or None. The provider that worked last is tried first."""
    global _last_provider
    order = sorted(devices.PROVIDERS, key=lambda p: p[0] != _last_provider)
    for name, fn in order:
        try:
            res = fn()
        except Exception as e:
            log(f"provider {name} failed: {e!r}")
            continue
        if res:
            _last_provider = name
            return _filter(res)
    _last_provider = None
    return None


def _filter(res):
    """Glitch guard: a reading that jumps >15 points, or rises while not charging, is held
    back until the same value shows up on two polls in a row."""
    global _accepted, _pending
    now = time.time()
    if _accepted is None or now - _accepted[2] > 1800:  # first read, or the last one is stale
        _accepted, _pending = (res["percent"], res["charging"], now), None
        return res
    last, was_charging, _ = _accepted
    p, chg = res["percent"], res["charging"]
    odd = abs(p - last) > 15 or (p > last + 5 and not chg and not was_charging)
    if odd and not (_pending is not None and abs(_pending - p) <= 2):
        log(f"held suspicious reading {p}% (last accepted {last}%)")
        _pending = p
        held = dict(res)
        held["percent"], held["charging"] = last, was_charging
        _accepted = (last, was_charging, now)
        return held
    _accepted, _pending = (p, chg, now), None
    return res


# ---------------------------------------------------------------- Win32 / game detection
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def primary_monitor():
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def _exe_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def foreground_game(cfg):
    """Monitor rect (l, t, r, b) if a game is in the foreground, else None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return None
    path = _exe_of(hwnd)
    name = os.path.basename(path).lower()
    if name in IGNORE_EXES or name == os.path.basename(sys.executable).lower():
        return None

    mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    rect = (m.left, m.top, m.right, m.bottom)

    if name in [e.lower() for e in cfg["game_exes"]]:
        return rect
    if path and any(path.lower().startswith(d.lower()) for d in cfg["game_dirs"]):
        return rect
    if cfg["fullscreen_means_game"]:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom:
            return rect
    return None
