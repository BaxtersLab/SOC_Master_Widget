#!/usr/bin/env python3
"""SOC Master Widget - a small local launcher (Tkinter, standard library only).

One window to start the SOC Ultralight workflow stack, in order:
  1. Hot Rod Tuner   2. GGUF Chatbox (restores last session's model itself)   3. SOC Ultralight
The V-plugin (Agent 4 vision) auto-loads inside SOC; the on-demand "Show A4
Vision" button brings its window to front via a fire-and-forget signal (no
process launched, so no zombies).

Dark theme matched to SOC Ultralight (soc_ultralight.py palette).
Config-driven: edit soc_master_apps.json to add/reorder apps or set launch commands.
Console apps get their own window; GUI apps (console:false) launch windowless.
Status dots: idle (grey) / running (green) / exited (red).

Run:   soc_master_widget.bat        (or:  pyw soc_master_widget.py)
Check: py -3 soc_master_widget.py --check   (validates config + paths, no window)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Frozen-aware: as a PyInstaller exe, __file__ points into the temp unpack dir —
# the registry json + assets live NEXT TO THE EXE (the file-cabinet hub).
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent
CONFIG = HERE / "soc_master_apps.json"
ICON = HERE / "assets" / "master_widget.ico"

CREATE_NEW_CONSOLE = 0x00000010  # Windows: give a console app its own window

# Single-instance lock: first launch binds this port and holds it for its
# lifetime (the OS releases it on process death — no stale-lockfile problem).
# A second launch can't bind, pings the holder to bring its window to the
# front, and exits. Same pattern GGUF Chatbox uses for its instance lock.
SINGLETON_ADDR = ("127.0.0.1", 47611)


def acquire_singleton(addr=SINGLETON_ADDR):
    """Bind the instance-lock port. Returns the held socket (keep a reference
    for the process lifetime), or None when another instance holds it."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(addr)
        s.listen(2)
        return s
    except OSError:
        s.close()
        return None


def notify_existing(addr=SINGLETON_ADDR):
    """Ask the running instance to bring its window to the front."""
    import socket
    try:
        with socket.create_connection(addr, timeout=1.0) as c:
            c.sendall(b"SHOW")
    except OSError:
        pass


def watch_singleton(lock_sock, on_ping):
    """Accept pings from later launches; call on_ping() for each. Run on a
    daemon thread; returns when the lock socket is closed (app exit)."""
    while True:
        try:
            conn, _ = lock_sock.accept()
            conn.close()
        except OSError:
            return
        try:
            on_ping()
        except Exception:
            pass

# Palette matched to SOC Ultralight (soc_ultralight.py: BG/BG2/FG/ACCENT/GREEN/RED).
BG = "#1e1e1e"
BG2 = "#2d2d2d"
FG = "#d4d4d4"
ACCENT = "#569cd6"   # blue
GREEN = "#4ec994"
RED = "#e05555"
MUTED = "#888888"
IDLE = "#666666"


def current_platform() -> str:
    """'linux' or 'win' — the registry's per-OS command key space."""
    return "linux" if sys.platform.startswith("linux") else "win"


# ── Virtual-desktop dock (the Vi_minimizer dock) ─────────────────────────────
# The widget is the one GUI no agent ever clicks, so it hosts the dock: a
# symbolic indicator that pulses when the SOC swarm lives on another virtual
# desktop ("docked"), and a click that hops in/out while everything keeps
# running. Detection uses the DOCUMENTED IVirtualDesktopManager COM interface
# (no undocumented desktop APIs); switching uses the native Win+Ctrl+←/→ keys.
#
# NOTE on persistence: to keep this widget visible on BOTH desktops, pin it
# once per session — Task View (Win+Tab) → right-click the widget window →
# "Show this window on all desktops". (Programmatic pinning is undocumented.)

SOC_WINDOW_TITLE = "SOC Ultralight"   # marker window for the swarm's desktop

_vdm_ptr = None   # cached COM pointer (False = init failed, don't retry)


def _find_window(title_substr: str):
    """First visible top-level window whose title contains the substring.
    Windows on OTHER virtual desktops are still enumerated (cloaked, but
    visible) — exactly what the dock needs."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if title_substr.lower() in buf.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(_enum, 0)
    return found[0] if found else None


def _vdm():
    """IVirtualDesktopManager COM object pointer, or None if unavailable."""
    global _vdm_ptr
    if _vdm_ptr is not None:
        return _vdm_ptr or None
    import ctypes
    from uuid import UUID
    try:
        ole32 = ctypes.oledll.ole32
        try:
            ole32.CoInitialize(None)
        except OSError:
            pass                       # already initialized on this thread
        clsid = (ctypes.c_ubyte * 16).from_buffer_copy(
            UUID("aa509086-5ca9-4c25-8f95-589d3c07b48a").bytes_le)
        iid = (ctypes.c_ubyte * 16).from_buffer_copy(
            UUID("a5cd92ff-29be-454c-8d04-d82879fb3f1b").bytes_le)
        ptr = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1 | 0x4,
                               ctypes.byref(iid), ctypes.byref(ptr))
        _vdm_ptr = ptr
        return ptr
    except Exception:
        _vdm_ptr = False
        return None


def _on_current_desktop(hwnd):
    """True/False from IVirtualDesktopManager::IsWindowOnCurrentVirtualDesktop
    (vtable slot 3), or None when COM is unavailable/errors."""
    vdm = _vdm()
    if not vdm:
        return None
    import ctypes
    from ctypes import wintypes
    try:
        vtbl = ctypes.cast(vdm, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                   wintypes.HWND, ctypes.POINTER(wintypes.BOOL))
        fn = proto(vtbl[3])
        onto = wintypes.BOOL()
        if fn(vdm, hwnd, ctypes.byref(onto)) != 0:
            return None
        return bool(onto.value)
    except Exception:
        return None


def dock_state() -> str:
    """'docked'  = SOC runs on ANOTHER desktop (pulse!)
       'here'    = SOC is on THIS desktop
       'none'    = SOC not running
       'unknown' = COM unavailable (indicator stays passive)"""
    hwnd = _find_window(SOC_WINDOW_TITLE)
    if not hwnd:
        return "none"
    cur = _on_current_desktop(hwnd)
    if cur is None:
        return "unknown"
    return "here" if cur else "docked"


def switch_desktop(direction: str):
    """Native virtual-desktop switch via Win+Ctrl+Left/Right (keybd_event)."""
    import ctypes
    u = ctypes.windll.user32
    VK_WIN, VK_CTRL = 0x5B, 0x11
    key = 0x27 if direction == "right" else 0x25
    for vk in (VK_WIN, VK_CTRL, key):
        u.keybd_event(vk, 0, 0, 0)
    for vk in (key, VK_CTRL, VK_WIN):
        u.keybd_event(vk, 0, 2, 0)     # KEYEVENTF_KEYUP


def load_config(platform: str | None = None, config_path: Path | None = None):
    """Load the registry, resolving per-OS fields.

    'cmd' is the Windows argv (historic key); 'cmd_linux' overrides on Linux.
    'dir' likewise has an optional 'dir_linux'. An app with no command for the
    current platform gets _cmd = [] (row shows disabled, not an error).
    """
    platform = platform or current_platform()
    cfg = config_path or CONFIG
    if not cfg.exists():
        raise SystemExit(f"config not found next to this script: {cfg.name}")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    apps = data.get("apps", [])
    if not isinstance(apps, list) or not apps:
        raise SystemExit("config must have a non-empty 'apps' array")
    for a in apps:
        if platform == "linux":
            a["_cmd"] = a.get("cmd_linux") or []
            raw_dir = a.get("dir_linux") or a.get("dir", ".")
        else:
            a["_cmd"] = a.get("cmd") or []
            raw_dir = a.get("dir", ".")
        # dir may be relative to the hub (this file's folder) or absolute.
        a["_dir"] = (cfg.parent / raw_dir).resolve()
        a["_ready"] = bool(a["_cmd"]) and a["_dir"].is_dir()
    data["apps"] = sorted(apps, key=lambda x: x.get("order", 99))
    return data


def check():
    """Headless validation: prove config + paths resolve. No window."""
    data = load_config()
    print(f"config : {CONFIG}")
    print(f"hub    : {HERE}")
    print(f"os     : {current_platform()}")
    ok = True
    for a in data["apps"]:
        dir_ok = a["_dir"].is_dir()
        cmd_ok = bool(a["_cmd"])
        if not dir_ok:
            ok = False
        flag = "READY" if (dir_ok and cmd_ok) else ("no-cmd(this OS)" if dir_ok else "DIR-MISSING")
        print(f"  [{a.get('order','?')}] {a['name']:<16} {flag:<16} {a['_dir']}")
    print("RESULT: all dirs present" if ok else "RESULT: a directory is missing (see above)")
    return 0 if ok else 1


def _launch(app, log, procs):
    name = app["name"]
    if not app.get("_cmd"):
        log(f"[skip]  {name}: no launch command for this OS (edit soc_master_apps.json)")
        return
    d = app["_dir"]
    if not d.is_dir():
        log(f"[error] {name}: folder missing -> {d}")
        return
    existing = procs.get(name)
    if existing and existing.poll() is None:
        log(f"[info]  {name}: already running (pid {existing.pid})")
        return
    try:
        kwargs = {}
        if os.name == "nt" and app.get("console", True):
            kwargs["creationflags"] = CREATE_NEW_CONSOLE
        proc = subprocess.Popen(app["_cmd"], cwd=str(d), **kwargs)
        if app.get("action"):
            # Fire-and-forget control action (e.g. write a signal file, then exit).
            # Not tracked in procs: it is meant to exit immediately, so there is
            # no persistent process and its status dot stays idle (not "exited").
            log(f"[action] {name}: sent")
        else:
            procs[name] = proc
            log(f"[start] {name}: pid {proc.pid}")
    except Exception as e:  # never let one bad launch kill the widget
        log(f"[error] {name}: {type(e).__name__}: {e}")


def gui():
    import tkinter as tk

    # Second-instance prevention: if the board is already open, bring IT to the
    # front instead of spawning a duplicate, and exit quietly.
    lock = acquire_singleton()
    if lock is None:
        print("[widget] already running — bringing the existing board to front")
        notify_existing()
        return

    data = load_config()
    apps = data["apps"]
    procs = {}  # name -> Popen

    root = tk.Tk()
    root.title(data.get("title", "SOC Master Widget"))
    try:
        if ICON.is_file():
            root.iconbitmap(str(ICON))
    except Exception:
        pass                       # icon is cosmetic — never block startup
    root.geometry("270x600")
    root.minsize(250, 540)
    root.configure(bg=BG)

    outer = tk.Frame(root, bg=BG, padx=10, pady=8)
    outer.pack(fill="both", expand=True)

    tk.Label(outer, text=data.get("title", "SOC Master Widget"), bg=BG, fg=FG,
             font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))

    rows = tk.Frame(outer, bg=BG)
    rows.pack(fill="x")
    dots = {}
    logbox = None  # assigned below; used by log()

    def log(msg):
        logbox.configure(state="normal")
        logbox.insert("end", msg + "\n")
        logbox.see("end")
        logbox.configure(state="disabled")

    def mkbtn(parent, text, cmd, fg=FG, accent=ACCENT):
        return tk.Button(parent, text=text, command=cmd, bg=BG2, fg=fg,
                         activebackground=accent, activeforeground="white",
                         disabledforeground=MUTED, relief="flat", bd=0,
                         highlightthickness=0, cursor="hand2",
                         font=("Segoe UI", 9, "bold"), padx=12, pady=3)

    def refresh():
        for a in apps:
            p = procs.get(a["name"])
            if p is None:
                dots[a["name"]].configure(fg=IDLE)          # idle
            elif p.poll() is None:
                dots[a["name"]].configure(fg=GREEN)         # running
            else:
                dots[a["name"]].configure(fg=RED)           # exited
        root.after(2000, refresh)

    def launch(app):
        _launch(app, log, procs)
        refresh()

    def start_stack():
        log("[stack] launching in order...")
        seq = [a for a in apps if a.get("in_stack", True)]  # on-demand apps opt out

        def step(i=0):
            if i >= len(seq):
                log("[stack] done")
                return
            launch(seq[i])
            root.after(1800, lambda: step(i + 1))  # ~1.8s between launches

        step()

    for a in apps:
        row = tk.Frame(rows, bg=BG)
        row.pack(fill="x", pady=2)
        dot = tk.Label(row, text="●", bg=BG, fg=IDLE, font=("Segoe UI", 10), width=2)
        dot.pack(side="left")
        dots[a["name"]] = dot
        tk.Label(row, text=f"{a.get('order','?')}. {a['name']}", bg=BG, fg=FG,
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
        btn = mkbtn(row, "Start", lambda app=a: launch(app))
        btn.pack(side="right")
        if not a.get("_cmd") or not a["_dir"].is_dir():
            btn.configure(state="disabled")

    ctrl = tk.Frame(outer, bg=BG)
    ctrl.pack(fill="x", pady=(14, 8))
    mkbtn(ctrl, "▶  Start Stack", start_stack, fg=GREEN, accent=GREEN).pack(side="left")
    mkbtn(ctrl, "Refresh", lambda: refresh()).pack(side="left", padx=(8, 0))

    tk.Label(outer, text="Log", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 2))
    logbox = tk.Text(outer, height=5, wrap="word", state="disabled", bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", highlightthickness=0, bd=0,
                     padx=6, pady=4, font=("Consolas", 8))
    logbox.pack(fill="both", expand=True)

    # ── Vi_minimizer dock — the pulsing rectangle ─────────────────────────────
    # Pulses yellow↔orange while the SOC swarm lives on ANOTHER virtual desktop
    # ("docked"); click hops in/out (Win+Ctrl+←/→) with everything left running.
    YELLOW, ORANGE_P = "#f5d90a", "#ff8c00"
    dock = tk.Frame(outer, bg=BG2, highlightbackground=IDLE,
                    highlightthickness=3, cursor="hand2")
    dock.pack(fill="x", pady=(8, 0))
    dock_state_lbl = tk.Label(dock, text="Inactive", bg=BG2, fg=ACCENT,
                              font=("Segoe UI", 12, "bold"), cursor="hand2")
    dock_state_lbl.pack(pady=(6, 0))
    dock_hint_lbl = tk.Label(dock, text="click to dock virtual desktop",
                             bg=BG2, fg=FG, font=("Segoe UI", 10, "bold"),
                             cursor="hand2")
    dock_hint_lbl.pack(pady=(0, 6))

    _dock = {"state": "none", "pulse": False}

    def _dock_click(_e=None):
        st = _dock["state"]
        if st == "docked":
            log("[dock] hopping to the swarm desktop →")
            switch_desktop("right")
        elif st == "here":
            log("[dock] ← returning to the main desktop")
            switch_desktop("left")
        else:
            log("[dock] SOC not running — nothing to dock to")

    for w in (dock, dock_state_lbl, dock_hint_lbl):
        w.bind("<Button-1>", _dock_click)

    def _dock_poll():
        try:
            st = dock_state()
        except Exception:
            st = "unknown"
        _dock["state"] = st
        if st == "docked":
            # Pulse the border yellow↔orange so "the swarm is elsewhere,
            # running" is unmistakable at a glance.
            _dock["pulse"] = not _dock["pulse"]
            dock.configure(highlightbackground=YELLOW if _dock["pulse"] else ORANGE_P)
            dock_state_lbl.configure(text="DOCKED — swarm running", fg=YELLOW)
            dock_hint_lbl.configure(text="click to enter virtual desktop")
        elif st == "here":
            dock.configure(highlightbackground=GREEN)
            dock_state_lbl.configure(text="Active — on this desktop", fg=GREEN)
            dock_hint_lbl.configure(text="click to return to main desktop")
        else:
            dock.configure(highlightbackground=IDLE)
            dock_state_lbl.configure(text="Inactive", fg=ACCENT)
            dock_hint_lbl.configure(text="click to dock virtual desktop")
        root.after(600, _dock_poll)

    _dock_poll()

    # Raise this window whenever a second launch pings the instance lock.
    def bring_to_front():
        try:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.after(150, lambda: root.attributes("-topmost", False))
            root.focus_force()
        except Exception:
            pass

    import threading
    threading.Thread(target=watch_singleton,
                     args=(lock, lambda: root.after(0, bring_to_front)),
                     daemon=True).start()

    log("Ready. Start Stack launches 1 -> 2 -> 3.")
    log("[dock] to keep this widget on BOTH desktops: Win+Tab -> right-click"
        " this window -> 'Show this window on all desktops' (once per session)")
    refresh()
    root.mainloop()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        return check()
    gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
