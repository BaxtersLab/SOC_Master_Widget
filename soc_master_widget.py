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
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Frozen-aware: as a PyInstaller exe, __file__ points into the temp unpack dir —
# the registry json + assets live NEXT TO THE EXE (the file-cabinet hub).
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent
CONFIG = HERE / "soc_master_apps.json"
ICON = HERE / "assets" / "master_widget.ico"
# Tk's iconbitmap() only accepts .ico on Windows; on X11 it wants an XBM and
# raises TclError on a .ico, so the window fell back to the bare Tk feather.
# iconphoto() takes a PhotoImage, which reads PNG natively in Tk 8.6.
ICON_PNG = HERE / "assets" / "soc-master-widget.png"

CREATE_NEW_CONSOLE = 0x00000010  # Windows: give a console app its own window

# ── Platform capability: window management ───────────────────────────────────
# Docking to a virtual desktop, snap-to-grid and click-to-pick are Win32 calls
# through ctypes.windll. There is no portable equivalent to reach for: Wayland
# deliberately refuses to let a client enumerate, place or focus another app's
# windows, and this box is Wayland-only. So this is a Windows capability, and
# the honest thing on other platforms is to not offer it.
#
# The controls are therefore NOT BUILT when this is False, rather than built
# and disabled. A visible control that cannot work invites a click and then has
# to explain itself, and the dock's explanation was actively wrong: dock_state()
# raised AttributeError on Linux, _dock_poll() caught it and fell back to
# "unknown", and clicking then reported "SOC not running — nothing to dock to"
# whether or not SOC was running.
WINDOW_MGMT = os.name == "nt"


class UnsupportedOnThisPlatform(RuntimeError):
    """A Win32-only window-management call was made on another platform.

    Raised in place of the `module 'ctypes' has no attribute 'windll'`
    AttributeError, which named the wrong problem — the caller's mistake is
    reaching a Windows capability, not a missing attribute."""

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
    if os.name != "nt":
        # Linux: a just-closed board leaves the port in TIME_WAIT for ~60s and
        # a fresh bind fails (EADDRINUSE) — the relaunch would refuse to open.
        # SO_REUSEADDR permits the TIME_WAIT rebind while STILL rejecting a
        # second live listener on Linux, so the single-instance guarantee
        # holds. (On Windows SO_REUSEADDR can steal an active listen — weaker
        # semantics — so it stays off there; Windows rebinds fine anyway.)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def release_singleton(lock_sock):
    """Release the instance lock deterministically. shutdown() wakes a thread
    blocked in accept() IMMEDIATELY on Linux (a bare close() does not — the
    in-flight accept pins the listen socket in the kernel and the port stays
    bound); on Windows shutdown on a listener just errors, harmlessly."""
    import socket as _socket
    try:
        lock_sock.shutdown(_socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        lock_sock.close()
    except OSError:
        pass


def watch_singleton(lock_sock, on_ping):
    """Accept pings from later launches; call on_ping() for each. Run on a
    daemon thread; returns when the lock socket is released (app exit).
    A 1s accept timeout is the backstop: Linux does not wake a blocked
    accept() when the fd is closed elsewhere, so poll for closure too."""
    import socket as _socket
    try:
        lock_sock.settimeout(1.0)
    except OSError:
        return
    while True:
        try:
            conn, _ = lock_sock.accept()
            conn.close()
        except _socket.timeout:
            try:
                if lock_sock.fileno() == -1:   # released elsewhere
                    return
            except OSError:
                return
            continue
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


def _window_owner_exe(hwnd) -> str:
    """Lowercase basename of the executable owning hwnd's process (''  on any
    failure). ctypes-only, no pywin32 — matches the rest of this file."""
    import ctypes
    from ctypes import wintypes
    import ntpath
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return ""
        try:
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD)]
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return ntpath.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def _find_window(title_substr: str, exe_name: str | None = None):
    """First visible top-level window whose title contains the substring
    (case-insensitive). Windows on OTHER virtual desktops are still
    enumerated (cloaked, but visible) — exactly what the dock needs.

    When exe_name is given, also requires the window's owning process image
    to match it (case-insensitive basename). Title-only matching can false-
    positive on a generic app name that happens to appear in an unrelated
    window's title (e.g. a browser tab or another editor's workspace name
    mentioning it) — the exe check disambiguates. Without exe_name, behavior
    is unchanged from before (first title match wins, in Z-order)."""
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
                if not exe_name:
                    return False   # first match is enough — same as before
        return True

    user32.EnumWindows(_enum, 0)
    if not exe_name:
        return found[0] if found else None
    target = exe_name.lower()
    for hwnd in found:
        if _window_owner_exe(hwnd) == target:
            return hwnd
    return None


def restore_and_focus(hwnd) -> bool:
    """Un-minimize (if needed) and raise hwnd to the foreground.

    A plain SetForegroundWindow often gets silently refused by Windows'
    foreground-lock rules when called from a process that isn't the current
    foreground app (exactly the case for a window found sitting minimized
    from an earlier launch). AttachThreadInput temporarily joins our input
    queue to the target window's so the OS treats the call as if it came
    from that window's own thread, which the lock permits. Best-effort:
    returns False (never raises) if anything about this fails.
    """
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        cur_tid = kernel32.GetCurrentThreadId()
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        attached = False
        if target_tid and target_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, target_tid, True))
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, target_tid, False)
        return True
    except Exception:
        return False


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
       'unknown' = COM unavailable (indicator stays passive)
       'unsupported' = not Windows; there is no dock to report on"""
    if not WINDOW_MGMT:
        # Answered without touching Win32 rather than left to raise: the poller
        # ran this every 600 ms and swallowed the AttributeError each time.
        return "unsupported"
    hwnd = _find_window(SOC_WINDOW_TITLE)
    if not hwnd:
        return "none"
    cur = _on_current_desktop(hwnd)
    if cur is None:
        return "unknown"
    return "here" if cur else "docked"


def switch_desktop(direction: str):
    """Native virtual-desktop switch via Win+Ctrl+Left/Right (keybd_event)."""
    if not WINDOW_MGMT:
        raise UnsupportedOnThisPlatform("virtual-desktop switching is Windows-only")
    import ctypes
    u = ctypes.windll.user32
    VK_WIN, VK_CTRL = 0x5B, 0x11
    key = 0x27 if direction == "right" else 0x25
    for vk in (VK_WIN, VK_CTRL, key):
        u.keybd_event(vk, 0, 0, 0)
    for vk in (key, VK_CTRL, VK_WIN):
        u.keybd_event(vk, 0, 2, 0)     # KEYEVENTF_KEYUP


# ── Snap grid — align any set of windows with one click ──────────────────────
# Moved here from the SOC GUI (which was overcrowded). The widget can MoveWindow
# ANY top-level window by its title, so it is the natural home for a whole-desktop
# "snap everything back into place" button. Click-add each window you want in the
# grid (an agent, A4 vision, the outbox monitor, even this widget itself); "Snap
# to Grid" then restores every one to its saved position/size in a single click.
# Positions live in a machine-local soc_grid.json (git-ignored). Stdlib only —
# win32 comes through ctypes exactly like the dock above, never pywin32.

GRID_CONFIG = HERE / "soc_grid.json"
SW_RESTORE = 9      # ShowWindow: un-minimize before moving
GA_ROOT = 2         # GetAncestor: climb from the clicked child to its top-level
VK_LBUTTON = 0x01   # left mouse button, for click-to-pick


def grid_valid_title(title: str) -> bool:
    """A window is eligible for the grid if it has a real title and is not the
    desktop shell. Pure/testable."""
    t = (title or "").strip()
    return bool(t) and t.lower() != "program manager"


def grid_upsert(grid_windows, title, rect):
    """Add a window, or update its rect if the title is already present (so re-
    adding a window re-captures its current position). Order-preserving.
    Pure/testable — the win32 capture/snap stays in the helpers below."""
    out, updated = [], False
    for wd in (grid_windows or []):
        if wd.get("title") == title:
            out.append({"title": title, "rect": list(rect)})
            updated = True
        else:
            out.append(wd)
    if not updated:
        out.append({"title": title, "rect": list(rect)})
    return out


def title_match(saved: str, candidate: str) -> bool:
    """Loose title match (prefix-30, either direction) so a window is still found
    after its title gains or loses a suffix. Pure/testable."""
    s = (saved or "").strip().lower()
    c = (candidate or "").strip().lower()
    if not s or not c:
        return False
    return s[:30] in c or c[:30] in s


def load_grid(path: Path | None = None) -> list:
    """Read the saved grid (list of {title, rect}); [] if missing/invalid."""
    p = path or GRID_CONFIG
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [w for w in raw
            if isinstance(w, dict) and w.get("title") and w.get("rect")]


def save_grid(grid_windows, path: Path | None = None) -> None:
    """Persist the grid; failures are swallowed (window positions are non-critical)."""
    p = path or GRID_CONFIG
    try:
        Path(p).write_text(json.dumps(list(grid_windows), indent=2), encoding="utf-8")
    except Exception:
        pass


def find_window_by_title(title: str):
    """First visible, non-minimized top-level window whose title loosely matches
    `title`. Windows-only (ctypes/user32); returns the hwnd or None. Re-resolving
    by title means a window reopened at a new handle is still found."""
    if not title:
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lp):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value and title_match(title, buf.value):
                found.append(hwnd)
                return False
        return True

    try:
        user32.EnumWindows(_enum, 0)
    except Exception:
        return None
    return found[0] if found else None


def snap_window(title: str, rect, hwnd=None):
    """Move ONE window to rect, resolving its handle by title when the passed one
    is missing/invalid. Returns (hwnd, status) — 'snapped' | 'missing' | 'error:…'."""
    if not WINDOW_MGMT:
        raise UnsupportedOnThisPlatform("snap-to-grid is Windows-only")
    import ctypes
    user32 = ctypes.windll.user32
    try:
        valid = bool(hwnd) and user32.IsWindow(hwnd)
    except Exception:
        valid = False
    if not valid:
        hwnd = find_window_by_title(title)
    if not hwnd:
        return None, "missing"
    try:
        x, y, w, h = rect
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.MoveWindow(hwnd, int(x), int(y), int(w), int(h), True)
        return hwnd, "snapped"
    except Exception as e:
        return hwnd, f"error: {e}"


def mouse_left_down() -> bool:
    """True while the physical left mouse button is held (async key state)."""
    if not WINDOW_MGMT:
        raise UnsupportedOnThisPlatform("global mouse-button state is Windows-only")
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def window_under_cursor():
    """(title, [x, y, w, h]) of the top-level window under the cursor, or
    ('', [0,0,0,0]) on failure. Windows-only. WindowFromPoint takes POINT BY
    VALUE and returns a 64-bit HWND, so those prototypes are declared explicitly —
    ctypes would otherwise mis-marshal the struct and truncate the handle."""
    if not WINDOW_MGMT:
        raise UnsupportedOnThisPlatform("picking a window by cursor is Windows-only")
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    user32 = ctypes.windll.user32
    try:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.WindowFromPoint.argtypes = [POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT)
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        return (buf.value or ""), [r.left, r.top, r.right - r.left, r.bottom - r.top]
    except Exception:
        return "", [0, 0, 0, 0]


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


# ── Dependency auto-resolution ───────────────────────────────────────────────
# An entry can opt in with "auto_locate": "<name>" (see AUTO_LOCATORS below) so
# that a stale/missing path isn't just a permanently disabled button: the
# widget probes common install spots itself, then falls back to letting the
# operator browse to the exe, then (if they agree) a silent winget install.
# Each locator is injectable (env/which/is_file) so the search logic is
# unit-testable without a real filesystem or real installs.

def _candidate_vscodium_paths(env: dict | None = None) -> list[Path]:
    """Common per-user/per-machine VSCodium install locations, most likely
    first (winget's default user-scope install dir, then machine-wide)."""
    env = os.environ if env is None else env
    out = []
    local = env.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Programs" / "VSCodium" / "VSCodium.exe")
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        pf = env.get(key)
        if pf:
            out.append(Path(pf) / "VSCodium" / "VSCodium.exe")
    return out


def find_vscodium(env: dict | None = None, which=shutil.which,
                   is_file=Path.is_file) -> Path | None:
    """Best-effort locate an installed VSCodium.exe: PATH first (covers a
    winget/user install that registered itself), then common install
    directories. which()/is_file() are injectable for testing; real callers
    use the stdlib defaults."""
    exe = which("VSCodium.exe") or which("VSCodium") or which("codium")
    if exe:
        p = Path(exe)
        if p.name.lower() == "vscodium.exe" and is_file(p):
            return p
        # PATH commonly resolves 'codium' to bin\codium(.cmd) next to the
        # real exe, one level up.
        sibling = p.parent.parent / "VSCodium.exe"
        if is_file(sibling):
            return sibling
    for cand in _candidate_vscodium_paths(env):
        if is_file(cand):
            return cand
    return None


AUTO_LOCATORS = {"vscodium": find_vscodium}


def set_app_path(config_path: Path, app_name: str, exe_path: Path) -> bool:
    """Point app_name's Windows cmd/dir at exe_path and persist to disk (the
    registry stays the single source of truth — no separate state file).
    Returns True if the app was found and updated."""
    p = Path(config_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    found = False
    for a in data.get("apps", []):
        if a.get("name") == app_name:
            a["cmd"] = [str(exe_path)]
            a["dir"] = str(Path(exe_path).parent)
            found = True
    if found:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return found


def install_vscodium_via_winget(run=subprocess.run, which=shutil.which):
    """Best-effort silent install via winget (official package id
    VSCodium.VSCodium). Returns (ok: bool, message: str). run()/which() are
    injectable for testing without actually invoking winget."""
    if not which("winget"):
        return False, "winget not found on this system"
    try:
        r = run(["winget", "install", "--id", "VSCodium.VSCodium", "-e",
                 "--silent", "--accept-package-agreements",
                 "--accept-source-agreements"],
                capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            return True, "installed via winget"
        detail = (r.stderr or r.stdout or "").strip()[:300]
        return False, f"winget exited {r.returncode}: {detail}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


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
    """Returns a status string for callers that want to react (e.g. gui()'s
    launch() starts a post-spawn window watch on "started"): "skip" | "error"
    | "already-running" | "focused" | "action" | "started".
    """
    name = app["name"]
    if not app.get("_cmd"):
        log(f"[skip]  {name}: no launch command for this OS (edit soc_master_apps.json)")
        return "skip"
    d = app["_dir"]
    if not d.is_dir():
        log(f"[error] {name}: folder missing -> {d}")
        return "error"
    existing = procs.get(name)
    if existing and existing.poll() is None:
        log(f"[info]  {name}: already running (pid {existing.pid})")
        return "already-running"

    # Apps that declare a window_title get found-and-focused instead of
    # re-launched when a window already exists (even minimized/off-screen) —
    # e.g. an Electron app like VSCodium enforces its own single-instance
    # lock, so spawning again just makes it hand off to the existing window
    # and quit, and its own attempt to raise that window is often refused by
    # Windows' foreground-lock rules. Finding and focusing it ourselves,
    # right after the button click that still owns recent input, is reliable
    # where relying on the app's internal forwarding was not.
    window_title = app.get("window_title")
    if window_title and os.name == "nt":
        hwnd = _find_window(window_title, app.get("window_process"))
        if hwnd:
            ok = restore_and_focus(hwnd)
            log(f"[focus] {name}: existing window found — "
                f"{'brought to front' if ok else 'could not focus (click its taskbar icon)'}")
            return "focused"

    try:
        kwargs = {}
        if os.name == "nt" and app.get("console", True):
            kwargs["creationflags"] = CREATE_NEW_CONSOLE
        # Optional per-app environment from the registry, layered over our own.
        # Needed where two apps default to the same port: Hot Rod Tuner binds
        # 8080, which llama-server already owns, and whichever starts second
        # simply fails — so the registry sets HOTROD_PORT rather than the
        # operator having to remember. Values are stringified because JSON
        # numbers are legal here but execve requires strings.
        extra_env = app.get("env")
        if extra_env:
            kwargs["env"] = {**os.environ, **{k: str(v) for k, v in extra_env.items()}}
        proc = subprocess.Popen(app["_cmd"], cwd=str(d), **kwargs)
        if app.get("action"):
            # Fire-and-forget control action (e.g. write a signal file, then exit).
            # Not tracked in procs: it is meant to exit immediately, so there is
            # no persistent process and its status dot stays idle (not "exited").
            log(f"[action] {name}: sent")
            return "action"
        else:
            procs[name] = proc
            log(f"[start] {name}: pid {proc.pid}")
            return "started"
    except Exception as e:  # never let one bad launch kill the widget
        log(f"[error] {name}: {type(e).__name__}: {e}")
        return "error"


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

    # Silent auto-heal: an app registered with "auto_locate" whose configured
    # path has gone stale (moved/reinstalled elsewhere) gets one quiet
    # re-probe of common install spots before the board ever shows a button —
    # no dialog, just a log line, same as any other startup status.
    for a in apps:
        if a["_ready"] or not a.get("auto_locate"):
            continue
        locator = AUTO_LOCATORS.get(a["auto_locate"])
        found = locator() if locator else None
        if found:
            set_app_path(CONFIG, a["name"], found)
            a["cmd"] = [str(found)]
            a["_cmd"] = [str(found)]
            a["_dir"] = Path(found).parent
            a["_ready"] = True
            print(f"[locate] {a['name']}: found -> {found} (saved to {CONFIG.name})")

    # className sets WM_CLASS, which is how GNOME matches a window to its
    # .desktop file (StartupWMClass=soc-master-widget) to pick the dash icon.
    # Tk's default is the generic "tk"/"Tk" — SOC Ultralight is also Tk, so
    # without a distinct class the two apps are indistinguishable to the shell.
    root = tk.Tk(className="soc-master-widget")
    root.title(data.get("title", "SOC Master Widget"))
    try:
        if os.name == "nt" and ICON.is_file():
            root.iconbitmap(str(ICON))
        elif ICON_PNG.is_file():
            # Held on root: Tk keeps no reference to a PhotoImage, so a local
            # would be garbage-collected and the icon would blank out.
            root._icon_img = tk.PhotoImage(file=str(ICON_PNG))
            root.iconphoto(True, root._icon_img)
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

    row_buttons = {}  # name -> Button, so a resolved dependency can flip it back to "Start"

    def _apply_found(app, exe_path):
        name = app["name"]
        set_app_path(CONFIG, name, exe_path)
        app["cmd"] = [str(exe_path)]
        app["_cmd"] = [str(exe_path)]
        app["_dir"] = Path(exe_path).parent
        app["_ready"] = True
        btn = row_buttons.get(name)
        if btn is not None:
            btn.configure(text="Start", command=lambda app=app: launch(app), state="normal")
        log(f"[locate] {name}: found -> {exe_path} (saved to {CONFIG.name})")

    def try_auto_resolve(app):
        """'Locate/Install' button: silent re-probe, then let the operator
        browse to the exe, then (if they agree) a silent winget install."""
        name = app["name"]
        locator = AUTO_LOCATORS.get(app.get("auto_locate"))
        found = locator() if locator else None
        if found:
            _apply_found(app, found)
            return

        import tkinter.filedialog as fd
        log(f"[locate] {name}: not found automatically — choose its .exe, or Cancel to install it")
        picked = fd.askopenfilename(
            title=f"Locate {name}.exe", parent=root,
            filetypes=[(f"{name} executable", "*.exe"), ("All files", "*.*")])
        if picked:
            _apply_found(app, Path(picked))
            return

        if app.get("auto_locate") != "vscodium":
            log(f"[locate] {name}: no installer wired up for this app yet")
            return
        import tkinter.messagebox as mb
        if not mb.askyesno("Install VSCodium?",
                            "VSCodium wasn't found on this system.\n"
                            "Install it now via winget (silent)?", parent=root):
            log(f"[locate] {name}: skipped — click Locate/Install again once it's on the system")
            return

        log(f"[locate] {name}: installing via winget (this can take a minute)...")
        btn = row_buttons.get(name)
        if btn is not None:
            btn.configure(state="disabled", text="Installing…")

        def _install():
            ok, msg = install_vscodium_via_winget()

            def _done():
                log(f"[locate] {name}: {msg}")
                found2 = find_vscodium() if ok else None
                if found2:
                    _apply_found(app, found2)
                elif btn is not None:
                    btn.configure(state="normal", text="Locate/Install")
            root.after(0, _done)

        threading.Thread(target=_install, daemon=True).start()

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

    def watch_for_window(app, deadline, name=None):
        """Non-blocking poll (root.after, main thread only — no raw thread,
        matching refresh()/_dock_poll() elsewhere in this file) for a freshly
        spawned app's window to appear, then bring it to front. Covers apps
        (VSCodium/Electron) that restore a previously-minimized window state
        on launch, or are just slow to open a window."""
        title = app.get("window_title")
        nm = name or app["name"]
        hwnd = _find_window(title, app.get("window_process")) if (title and os.name == "nt") else None
        if hwnd:
            ok = restore_and_focus(hwnd)
            log(f"[focus] {nm}: window appeared — "
                f"{'brought to front' if ok else 'could not focus'}")
            return
        if time.time() >= deadline:
            log(f"[focus] {nm}: no window appeared yet (still starting?)")
            return
        root.after(500, lambda: watch_for_window(app, deadline, nm))

    def launch(app):
        status = _launch(app, log, procs)
        if status == "started" and app.get("window_title") and os.name == "nt":
            root.after(500, lambda: watch_for_window(app, time.time() + 20.0))
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
        ready = bool(a.get("_cmd")) and a["_dir"].is_dir()
        if not ready and a.get("auto_locate"):
            # Missing dependency the widget knows how to chase down itself,
            # rather than a permanently-disabled button.
            btn = mkbtn(row, "Locate/Install", lambda app=a: try_auto_resolve(app))
        else:
            btn = mkbtn(row, "Start", lambda app=a: launch(app))
            if not ready:
                btn.configure(state="disabled")
        btn.pack(side="right")
        row_buttons[a["name"]] = btn

    ctrl = tk.Frame(outer, bg=BG)
    ctrl.pack(fill="x", pady=(14, 8))
    mkbtn(ctrl, "▶  Start Stack", start_stack, fg=GREEN, accent=GREEN).pack(side="left")
    mkbtn(ctrl, "Refresh", lambda: refresh()).pack(side="left", padx=(8, 0))

    # ── Snap grid — align every registered window with one click ──────────────
    # "Add Window" click-picks any top-level window (agent, A4 vision, outbox,
    # even this widget) and remembers its title + current rect; "Snap to Grid"
    # moves them all back. Windows-only; on other platforms the row is omitted.
    if os.name == "nt":
        grid_state = {"windows": load_grid()}

        def grid_refresh_btn():
            n = len(grid_state["windows"])
            grid_add_btn.config(text=(f"⊞＋ Add ({n})" if n else "⊞＋ Add Window"))

        def grid_snap():
            wins = grid_state["windows"]
            if not wins:
                log("[grid] nothing added yet — use '⊞＋ Add Window' to pick windows")
                return
            snapped, missing = 0, []
            for wd in wins:
                title, rect = wd.get("title"), wd.get("rect")
                if not title or not rect:
                    continue
                short = (title[:22] + "…") if len(title) > 22 else title
                _h, status = snap_window(title, rect)
                if status == "snapped":
                    snapped += 1
                elif status == "missing":
                    missing.append(short)
                else:
                    log(f"[grid] '{short}' {status}")
            msg = f"[grid] snapped {snapped}/{len(wins)} window(s)"
            if missing:
                msg += f" — not found: {', '.join(missing)}"
            log(msg)

        def grid_register(title, rect):
            title = (title or "").strip()
            if not grid_valid_title(title):
                grid_refresh_btn()
                log("[grid] ignored — click a real app window")
                return
            grid_state["windows"] = grid_upsert(grid_state["windows"], title, rect)
            save_grid(grid_state["windows"])
            grid_refresh_btn()
            log(f"[grid] + '{title}'  ({rect[0]},{rect[1]}) {rect[2]}x{rect[3]}"
                f"  [{len(grid_state['windows'])} in grid]")

        def grid_clear(_e=None):
            n = len(grid_state["windows"])
            grid_state["windows"] = []
            save_grid(grid_state["windows"])
            grid_refresh_btn()
            log(f"[grid] cleared {n} window(s)")

        def grid_add():
            log("[grid] click any window to add it to the grid…")
            grid_add_btn.config(text="● click a window…")

            def _capture():
                deadline = time.time() + 2.0          # let the button click release
                while time.time() < deadline and mouse_left_down():
                    time.sleep(0.02)
                deadline = time.time() + 15.0
                while time.time() < deadline:
                    if mouse_left_down():
                        title, rect = window_under_cursor()
                        while mouse_left_down():
                            time.sleep(0.01)
                        root.after(0, lambda t=title, r=rect: grid_register(t, r))
                        return
                    time.sleep(0.02)
                root.after(0, lambda: (grid_refresh_btn(),
                                       log("[grid] add-to-grid timed out")))

            threading.Thread(target=_capture, daemon=True).start()

        grid_ctrl = tk.Frame(outer, bg=BG)
        grid_ctrl.pack(fill="x", pady=(0, 4))
        mkbtn(grid_ctrl, "⊞ Snap to Grid", grid_snap, fg=ACCENT).pack(side="left")
        grid_add_btn = mkbtn(grid_ctrl, "⊞＋ Add Window", grid_add)
        grid_add_btn.pack(side="left", padx=(8, 0))
        grid_add_btn.bind("<Button-3>", grid_clear)   # right-click = clear all
        grid_refresh_btn()

    # Collapsible log: a header row with a real Hide/Show button. Collapsing drops
    # the min height and shrinks the window to reclaim desktop space; expanding
    # restores it. The log keeps recording while hidden.
    log_row = tk.Frame(outer, bg=BG)
    log_row.pack(fill="x", pady=(6, 2))
    tk.Label(log_row, text="Log", bg=BG, fg=MUTED, font=("Segoe UI", 8),
             anchor="w").pack(side="left")
    log_toggle_btn = mkbtn(log_row, "▾ Hide", lambda: None, fg=MUTED)
    log_toggle_btn.configure(font=("Segoe UI", 8, "bold"), padx=8, pady=1)
    log_toggle_btn.pack(side="right")
    logbox = tk.Text(outer, height=5, wrap="word", state="disabled", bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", highlightthickness=0, bd=0,
                     padx=6, pady=4, font=("Consolas", 8))
    logbox.pack(fill="both", expand=True)

    _log_shown = {"v": True}
    # Bound before toggle_log so the closure always resolves it. Stays None off
    # Windows, where the dock is not built at all.
    dock = None

    def toggle_log(_e=None):
        root.update_idletasks()
        if _log_shown["v"]:
            h = logbox.winfo_height()
            wcur, hcur = root.winfo_width(), root.winfo_height()
            logbox.pack_forget()
            log_toggle_btn.config(text="▸ Show")
            root.minsize(250, 300)                       # let it shrink while hidden
            root.geometry(f"{wcur}x{max(hcur - h, 300)}")
            _log_shown["v"] = False
        else:
            # The dock is the last packed widget on Windows, so the log has to
            # go BEFORE it to stay above. Where there is no dock the log is
            # last anyway, and `before=None` is not a valid pack option.
            if dock is not None:
                logbox.pack(fill="both", expand=True, before=dock)
            else:
                logbox.pack(fill="both", expand=True)
            log_toggle_btn.config(text="▾ Hide")
            root.minsize(250, 540)                       # restore the expanded floor
            root.update_idletasks()
            root.geometry(f"{root.winfo_width()}x{max(root.winfo_height(), 540)}")
            _log_shown["v"] = True

    log_toggle_btn.configure(command=toggle_log)

    # ── Vi_minimizer dock — the pulsing rectangle ─────────────────────────────
    # Pulses yellow↔orange while the SOC swarm lives on ANOTHER virtual desktop
    # ("docked"); click hops in/out (Win+Ctrl+←/→) with everything left running.
    #
    # Windows-only, and NOT BUILT elsewhere — see WINDOW_MGMT. Virtual desktops
    # are driven here by keybd_event through ctypes.windll; Wayland has no
    # equivalent a client may call. Shown-but-inert was the old Linux
    # behaviour and it misreported "SOC not running" whatever the truth.
    if WINDOW_MGMT:
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
    if WINDOW_MGMT:
        # Win+Tab advice is meaningless without the dock it refers to.
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
