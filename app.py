"""Mouse Battery Overlay: tray app + in-game overlay + settings window.

Settings (v1): corner, opacity, show/hide hotkey. Changes apply live and are saved
to %APPDATA%\\MouseBatteryOverlay\\config.json. While the settings window is open the
overlay is forced visible so you can see what you are changing.
"""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

import pystray
from PIL import Image, ImageDraw

import core
from core import user32, kernel32

CORNER_LABELS = {"top-left": "Top left", "top-right": "Top right",
                 "bottom-left": "Bottom left", "bottom-right": "Bottom right"}


# ---------------------------------------------------------------- hotkey
_MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4}


def parse_hotkey(combo):
    """'ctrl+alt+b' -> (modifier_flags, vk) or None."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    key, mods = parts[-1], 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    if len(key) == 1 and key.isalnum():
        return mods, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return mods, 0x70 + int(key[1:]) - 1
    return None


def pretty_hotkey(combo):
    return "+".join(p.upper() if len(p) <= 3 and p != "alt" else p.capitalize()
                    for p in combo.split("+"))


class HotkeyThread(threading.Thread):
    """Owns RegisterHotKey + a message loop; posts 'toggle' to the queue."""

    def __init__(self, combo, q):
        super().__init__(daemon=True)
        self.parsed, self.q = parse_hotkey(combo), q
        self.ok = False
        self.ready = threading.Event()
        self.tid = 0

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        if self.parsed:
            mods, vk = self.parsed
            self.ok = bool(user32.RegisterHotKey(None, 1, mods | 0x4000, vk))  # NOREPEAT
        self.ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.q.put("toggle")
        user32.UnregisterHotKey(None, 1)

    def stop(self):
        self.ready.wait(1)
        if self.ok:
            user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT


# ---------------------------------------------------------------- tray icon image
def make_icon():
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((18, 4, 46, 60), radius=14, fill=(32, 36, 44, 255), outline=(232, 234, 237, 255), width=3)
    d.line((32, 8, 32, 22), fill=(232, 234, 237, 255), width=3)
    d.rounded_rectangle((26, 32, 38, 52), radius=4, fill=(76, 201, 120, 255))
    return im


# ---------------------------------------------------------------- app
class App:
    def __init__(self, open_settings=False):
        self.cfg = core.load_config()
        first_run = not os.path.exists(core.CONFIG_PATH)
        self.q = queue.Queue()
        self.battery = None
        self.last_poll = 0.0
        self.polling = False
        self.visible = False
        self.settings = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(core.APP_NAME)

        self.ov = tk.Toplevel(self.root)
        self.ov.overrideredirect(True)
        self.ov.attributes("-topmost", True)
        self.ov.configure(bg="#101216")
        self.label = tk.Label(self.ov, text="Mouse --", fg="#e8eaed", bg="#101216",
                              font=("Segoe UI Semibold", 13), padx=12, pady=6)
        self.label.pack()
        self.ov.withdraw()

        self.hotkey = None
        self.apply_hotkey()
        self.tray = pystray.Icon(
            core.APP_NAME, make_icon(), "Mouse Battery Overlay",
            menu=pystray.Menu(
                pystray.MenuItem("Settings", lambda: self.q.put("settings"), default=True),
                pystray.MenuItem("Show overlay in games", lambda: self.q.put("toggle"),
                                 checked=lambda item: self.cfg["enabled"]),
                pystray.MenuItem("Quit", lambda: self.q.put("quit")),
            ))
        self.tray.run_detached()

        self.open_evt = kernel32.CreateEventW(None, False, False, OPEN_EVENT)
        if first_run:
            self.save()
        if open_settings or first_run:
            self.q.put("settings")
        self.tick()

    # ---- config
    def save(self):
        try:
            core.save_config(self.cfg)
        except OSError as e:
            core.log(f"save failed: {e!r}")

    def apply_hotkey(self):
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = HotkeyThread(self.cfg["hotkey"], self.q)
        self.hotkey.start()
        self.hotkey.ready.wait(1)

    # ---- overlay
    def _hwnd(self):
        return user32.GetParent(self.ov.winfo_id()) or self.ov.winfo_id()

    def _clickthrough(self):
        h = self._hwnd()
        ex = user32.GetWindowLongW(h, -20)
        # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
        user32.SetWindowLongW(h, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000)

    def _poll_async(self):
        if self.polling:
            return
        self.polling = True

        def run():
            try:
                self.battery = core.read_battery()
            except Exception as e:
                core.log(f"battery read failed: {e!r}")
                self.battery = None
            finally:
                self.last_poll = time.time()
                self.polling = False
        threading.Thread(target=run, daemon=True).start()

    def _render(self):
        b = self.battery
        if b is None:
            txt, fg = "Mouse --", "#9aa0a6"
        else:
            txt = f"Mouse {b['percent']}%" + ("  ⚡" if b["charging"] else "")
            fg = "#ff5c5c" if b["percent"] <= self.cfg["low_percent"] and not b["charging"] else "#e8eaed"
        if self.label.cget("text") != txt or self.label.cget("fg") != fg:
            self.label.config(text=txt, fg=fg)

    def _place(self, mon):
        self.ov.update_idletasks()
        w, h = self.ov.winfo_reqwidth(), self.ov.winfo_reqheight()
        l, t, r, b = mon
        m, c = self.cfg["margin"], self.cfg["corner"]
        x = l + m if "left" in c else r - w - m
        y = t + m if "top" in c else b - h - m
        user32.SetWindowPos(self._hwnd(), wintypes.HWND(-1), x, y, 0, 0, 0x0001 | 0x0010 | 0x0040)

    def refresh_overlay(self):
        """Re-place and re-style immediately (used for live settings changes)."""
        if self.visible:
            self.ov.attributes("-alpha", self.cfg["opacity"] / 100)
            self._place(self._current_monitor() or core.primary_monitor())

    def _current_monitor(self):
        if self.settings is not None:
            return core.primary_monitor()  # preview while settings are open
        if not self.cfg["enabled"]:
            return None
        return core.foreground_game(self.cfg)

    def tick(self):
        try:
            self.drain()
            mon = self._current_monitor()
            if mon:
                if time.time() - self.last_poll > self.cfg["poll_seconds"]:
                    self._poll_async()
                self._render()
                if not self.visible:
                    self.ov.deiconify()
                    self.ov.update()
                    self._clickthrough()
                    self.visible = True
                self.ov.attributes("-alpha", self.cfg["opacity"] / 100)
                self._place(mon)
            elif self.visible:
                self.ov.withdraw()
                self.visible = False
        except Exception as e:
            core.log(f"tick: {e!r}")
        self.root.after(1000, self.tick)

    # ---- events from tray / hotkey threads
    def drain(self):
        if kernel32.WaitForSingleObject(self.open_evt, 0) == 0:  # second launch asked us to show settings
            self.open_settings()
        while True:
            try:
                ev = self.q.get_nowait()
            except queue.Empty:
                return
            if ev == "toggle":
                self.cfg["enabled"] = not self.cfg["enabled"]
                self.save()
                self.tray.update_menu()
                self.tick_soon()
            elif ev == "settings":
                self.open_settings()
            elif ev == "quit":
                self.quit()

    def tick_soon(self):
        self.root.after(50, lambda: None)

    def quit(self):
        try:
            self.hotkey.stop()
            self.tray.stop()
        finally:
            self.root.destroy()

    # ---- settings window
    def open_settings(self):
        if self.settings is not None:
            self.settings.lift()
            self.settings.focus_force()
            return
        w = self.settings = tk.Toplevel(self.root)
        w.title("Mouse Battery Overlay")
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close_settings)
        S = max(1.0, user32.GetDpiForSystem() / 96)
        frm = ttk.Frame(w, padding=int(24 * S))
        frm.grid()
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        H = ("Segoe UI Semibold", 11)

        # corner picker: a mini screen, click a quadrant
        ttk.Label(frm, text="Position", font=H).grid(row=0, column=0, sticky="w")
        corner_lbl = ttk.Label(frm, text=CORNER_LABELS[self.cfg["corner"]])
        corner_lbl.grid(row=0, column=1, sticky="e")
        CW, CH = int(320 * S), int(190 * S)
        cv = tk.Canvas(frm, width=CW, height=CH, highlightthickness=0, bg=w.cget("bg"))
        cv.grid(row=1, column=0, columnspan=2, pady=(8, 20))
        sh = int(CH * 0.80)  # screen height inside the canvas

        def draw():
            cv.delete("all")
            m = int(6 * S)
            cv.create_rectangle(m, m, CW - m, sh, fill="#1b1e24", outline="#5f6368", width=2)
            cv.create_rectangle(CW // 2 - 24 * S, sh, CW // 2 + 24 * S, sh + 12 * S, fill="#5f6368", outline="")
            cv.create_rectangle(CW // 2 - 50 * S, sh + 12 * S, CW // 2 + 50 * S, sh + 17 * S, fill="#5f6368", outline="")
            c = self.cfg["corner"]
            pw, ph, pad = 96 * S, 28 * S, 14 * S
            x0 = m + pad if "left" in c else CW - m - pad - pw
            y0 = m + pad if "top" in c else sh - pad - ph
            cv.create_rectangle(x0, y0, x0 + pw, y0 + ph, fill="#4cc978", outline="")
            cv.create_text(x0 + pw / 2, y0 + ph / 2, text="Mouse 45%", fill="#101216",
                           font=("Segoe UI", 8, "bold"))

        def pick(ev):
            c = ("top" if ev.y < sh / 2 else "bottom") + "-" + ("left" if ev.x < CW / 2 else "right")
            self.cfg["corner"] = c
            corner_lbl.config(text=CORNER_LABELS[c])
            draw()
            self.save()
            self.refresh_overlay()
        cv.bind("<Button-1>", pick)
        draw()

        # opacity
        ttk.Label(frm, text="Opacity", font=H).grid(row=2, column=0, sticky="w")
        op_val = ttk.Label(frm, text=f"{self.cfg['opacity']}%")
        op_val.grid(row=2, column=1, sticky="e")
        var = tk.IntVar(value=self.cfg["opacity"])

        def on_scale(v):
            self.cfg["opacity"] = int(float(v))
            op_val.config(text=f"{self.cfg['opacity']}%")
            self.refresh_overlay()
        sc = ttk.Scale(frm, from_=20, to=100, orient="horizontal", variable=var, command=on_scale)
        sc.grid(row=3, column=0, columnspan=2, pady=(8, 20), sticky="we")
        sc.bind("<ButtonRelease-1>", lambda e: self.save())

        # hotkey
        ttk.Label(frm, text="Show / hide hotkey", font=H).grid(row=4, column=0, columnspan=2, sticky="w")
        hk_var = tk.StringVar(value=pretty_hotkey(self.cfg["hotkey"]))
        hk_btn = ttk.Button(frm, textvariable=hk_var)
        hk_btn.grid(row=5, column=0, columnspan=2, sticky="we", pady=(8, 0), ipady=int(4 * S))
        hint = ttk.Label(frm, text="Click, then press the new combo.", foreground="#80868b",
                         wraplength=int(320 * S))
        hint.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 14))
        capture = {"on": False}

        def start_capture():
            capture["on"] = True
            hk_var.set("Press keys...")
            hint.config(text="Hold Ctrl/Alt/Shift and press a letter, digit or F1-F12. Esc cancels.")
            w.focus_force()

        def on_key(ev):
            if not capture["on"]:
                return
            ks = ev.keysym
            if ks == "Escape":
                capture["on"] = False
                hk_var.set(pretty_hotkey(self.cfg["hotkey"]))
                hint.config(text="")
                return "break"
            if ks.lower().startswith(("control", "shift", "alt", "win", "menu")):
                return "break"
            mods = []
            if ev.state & 0x4:
                mods.append("ctrl")
            if ev.state & 0x20000:
                mods.append("alt")
            if ev.state & 0x1:
                mods.append("shift")
            key = ks.lower() if (len(ks) == 1 and ks.isalnum()) or (ks[0] in "Ff" and ks[1:].isdigit()) else None
            if key is None or not ({"ctrl", "alt"} & set(mods)):
                hint.config(text="Needs Ctrl or Alt plus a letter, digit or F1-F12. Try again.")
                return "break"
            combo = "+".join(mods + [key])
            self.cfg["hotkey"] = combo
            self.save()
            self.apply_hotkey()
            capture["on"] = False
            hk_var.set(pretty_hotkey(combo))
            hint.config(text="" if self.hotkey.ok else "That combo is taken by another app. Pick another.")
            return "break"
        hk_btn.config(command=start_capture)
        w.bind("<KeyPress>", on_key)

        # status
        self.status = ttk.Label(frm, text="", foreground="#80868b")
        self.status.grid(row=7, column=0, sticky="w")
        self._status_tick()
        self._poll_async()
        ttk.Button(frm, text="Close", command=self.close_settings).grid(row=7, column=1, sticky="e")
        w.update_idletasks()
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 2}")
        w.attributes("-topmost", True)  # surface above the foreground app, then drop back
        w.after(400, lambda: w.winfo_exists() and w.attributes("-topmost", False))
        w.focus_force()

    def _status_tick(self):
        if self.settings is None:
            return
        b = self.battery
        if self.polling and b is None:
            txt = "Looking for your mouse..."
        elif b is None:
            txt = "No mouse with a readable battery found (Razer, Logitech or Bluetooth)."
        else:
            txt = f"{b['name']}: {b['percent']}%" + (" (charging)" if b["charging"] else "")
        self.status.config(text=txt)
        self.settings.after(1000, self._status_tick)

    def close_settings(self):
        w, self.settings = self.settings, None
        if w is not None:
            w.destroy()


OPEN_EVENT = "Local\\MouseBatteryOverlay.open"
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]


def single_instance():
    h = kernel32.CreateMutexW(None, False, "Local\\MouseBatteryOverlay")
    return kernel32.GetLastError() != 183, h  # 183 = ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    ok, _mutex = single_instance()
    if not ok:  # already running: ask it to open its settings window, then exit
        evt = kernel32.OpenEventW(0x0002, False, OPEN_EVENT)  # EVENT_MODIFY_STATE
        if evt:
            kernel32.SetEvent(evt)
        sys.exit(0)
    App(open_settings="--settings" in sys.argv).root.mainloop()
