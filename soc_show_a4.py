#!/usr/bin/env python3
"""Ask a running SOC Ultralight to bring the A4 vision window to the front.

SOC polls `soc_control.signal` in its own folder every 0.5 s and executes the
one word it finds there. Writing the file is the whole mechanism: nothing is
launched, so there is no process here that can be orphaned or become a zombie —
which is the reason SOC uses a file channel rather than an RPC port.

Exits non-zero when SOC's folder cannot be found, so the Master Widget shows a
failure instead of silently doing nothing.
"""

import os
import sys
from pathlib import Path

#: Overridable so the pair can live anywhere (Article XI: no hardcoded paths).
SOC_DIR = Path(os.environ.get(
    "SOC_ROOT",
    Path(__file__).resolve().parent.parent / "SOC_Ultralight"))

SIGNAL_NAME = "soc_control.signal"


def main() -> int:
    if not SOC_DIR.is_dir():
        print(f"SOC folder not found: {SOC_DIR}\n"
              f"Set SOC_ROOT to override.", file=sys.stderr)
        return 2
    target = SOC_DIR / SIGNAL_NAME
    try:
        target.write_text("show_a4", encoding="utf-8")
    except OSError as exc:
        print(f"could not write {target}: {exc}", file=sys.stderr)
        return 1
    print(f"signalled show_a4 -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
