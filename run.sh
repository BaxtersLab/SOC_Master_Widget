#!/usr/bin/env bash
# SOC Master Widget — Linux launcher (counterpart to run.bat).
#
# No virtualenv: the board is Tkinter + stdlib only (see the module docstring),
# so the system python3 runs it directly. What this script IS for is the two
# things run.bat does on Windows and a bare `python3 soc_master_widget.py`
# does not — bootstrap the registry from the example on first run, and hand
# the board a clean environment.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# ── Snap contamination scrub — MUST run before any python is invoked ─────────
#
# Same block as Hot Rod Tuner's run.sh, and it matters MORE here: this board's
# whole job is spawning the other apps as children, so anything leaked into
# this process is inherited by HRT, GGUF Chatbox and SOC Ultralight in turn.
# LOCPATH is the fatal one — it points into a snap's locales, built against a
# different glibc, and kills python outright with
#   symbol lookup error: ... undefined symbol: __libc_pthread_init
# GSETTINGS_SCHEMA_DIR is the cosmetic one: VS Code's snap ships an
# org.gnome.desktop.interface WITHOUT Ubuntu's Yaru override, so a contaminated
# child draws an Adwaita (white) titlebar instead of Yaru (gray).
#
# Scrub by rule, not by a hand-picked list — a VS Code terminal leaks around
# 20 of these (21 recorded previously; 18 measured on this box 2026-08-07 — the
# count moves with the extension set, so do not treat it as a fixed number).
# XDG_DATA_DIRS is FILTERED rather than unset: it legitimately holds system
# paths (and /var/lib/snapd/desktop, which is snapd, not /snap/).
#
# tests/test_run_sh_scrub.py executes this block — it is not just documented.
# (scrub:begin)
for _var in $(env | grep -o '^[A-Za-z_][A-Za-z0-9_]*=/snap/[^:]*' | cut -d= -f1); do
    [[ "$_var" == "XDG_DATA_DIRS" ]] && continue
    unset "$_var"
done
for _var in GSETTINGS_SCHEMA_DIR GTK_PATH GTK_IM_MODULE_FILE GTK_EXE_PREFIX \
            GIO_MODULE_DIR LOCPATH GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE \
            XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME; do
    [[ "${!_var:-}" == *"/snap/"* ]] && unset "$_var"
done
unset _var
if [[ "${XDG_DATA_DIRS:-}" == *"/snap/"* ]]; then
    _clean=""
    IFS=':' read -ra _parts <<< "$XDG_DATA_DIRS"
    for _p in "${_parts[@]}"; do
        [[ -z "$_p" || "$_p" == */snap/* ]] && continue
        _clean="${_clean:+$_clean:}$_p"
    done
    export XDG_DATA_DIRS="${_clean:-/usr/local/share:/usr/share}"
    unset _clean _parts _p
fi
# (scrub:end)
# VS Code's extension host exports these into every terminal it spawns, and
# they follow children: GDK_BACKEND=x11 forces a native-Wayland launch onto
# XWayland, ELECTRON_RUN_AS_NODE makes any Electron child start as bare Node.
unset GDK_BACKEND ELECTRON_RUN_AS_NODE

# ── Tkinter check ────────────────────────────────────────────────────────────
# Ubuntu splits tkinter out of the python3 package, so a working python3 is not
# proof the board can open a window. Say which apt package fixes it.
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "[mw] python3-tk is not installed — the board cannot open a window."
    echo "[mw]   sudo apt install python3-tk"
    exit 1
fi

# ── Registry bootstrap (parity with run.bat) ─────────────────────────────────
if [[ ! -f soc_master_apps.json ]]; then
    echo "[mw] First run: creating soc_master_apps.json from the example."
    cp soc_master_apps.example.json soc_master_apps.json
    echo "[mw] Edit soc_master_apps.json to register YOUR apps, then run again."
    exit 0
fi

# --check validates the registry and paths without opening a window.
echo "[mw] Launching SOC Master Widget…"
exec python3 soc_master_widget.py "$@"
