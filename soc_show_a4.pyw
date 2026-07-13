#!/usr/bin/env python3
"""Master-widget control: bring SOC's Agent 4 vision window to front.

Writes the 'show_a4' command to SOC's control signal file, which SOC's
`_soc_control_loop` polls (every 0.5s) and executes on its Tk main thread.

Fire-and-forget by design:
  • Writes one word and exits immediately -> no lingering process, so nothing
    here can ever become a zombie (the exact problem the old in-SOC "Start V"
    button had).
  • Idempotent -> SOC shows the window once regardless of double-clicks.

Launched by the SOC Master Widget (soc_master_apps.json 'Show A4 Vision' entry).
The signal file lives next to SOC at SOC_Ultralight/soc_control.signal, resolved
relative to this script's folder (the file-cabinet hub).
"""
import sys
from pathlib import Path

# This script lives in file cabinet\SOC_Master_Widget\; SOC is one level up.
HUB = Path(__file__).resolve().parent.parent
SIGNAL = HUB / "SOC_Ultralight" / "soc_control.signal"
CMD = sys.argv[1] if len(sys.argv) > 1 else "show_a4"

try:
    SIGNAL.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL.write_text(CMD, encoding="utf-8")
    print(f"[soc_show_a4] wrote '{CMD}' -> {SIGNAL}")
except Exception as e:  # pragma: no cover - trivial IO guard
    print(f"[soc_show_a4] error: {e}", file=sys.stderr)
    sys.exit(1)
