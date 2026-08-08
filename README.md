# SOC Master Widget

A zero-dependency launcher board for a multi-app AI workstation — and a
one-click installer for the [SOC Ultralight](https://github.com/BaxtersLab2/SOC_Ultralight)
orchestrator stack.

One slim window to start your stack in order, watch each app's status dot,
and hop in/out of a dedicated virtual desktop where the swarm runs.

## Quick start

**Windows**

```
git clone https://github.com/BaxtersLab/SOC_Master_Widget
cd SOC_Master_Widget
setup.bat      # optional: installs the full SOC stack (SOC + pip deps + Tesseract)
run.bat        # opens the board (creates your registry from the example on first run)
```

**Linux**

```
git clone https://github.com/BaxtersLab/SOC_Master_Widget
cd SOC_Master_Widget
./run.sh       # opens the board (creates your registry from the example on first run)
```

Use `run.sh` rather than `python3 soc_master_widget.py`: it bootstraps the
registry on first run, checks for `python3-tk` (Ubuntu splits Tkinter out of
the `python3` package), and scrubs snap-owned environment variables. That last
one matters because the board *spawns* your stack — a snap-owned `LOCPATH`
inherited from, say, a VS Code terminal kills every Python child outright with
`undefined symbol: __libc_pthread_init`. `packaging/soc-master-widget.desktop`
installs a launcher into the GNOME app grid.

The widget itself needs **nothing but Python 3.10+** (stdlib only — Tkinter, ctypes).
`setup.bat` is only for bootstrapping the SOC orchestrator environment.

## Features

- **Config-driven board** — register any apps in `soc_master_apps.json`
  (working dir + argv; per-OS overrides via `cmd_linux`). Status dots:
  grey idle · green running · red exited.
- **Start Stack** — launches your `order`-ed apps in sequence.
- **Snap grid** — **Add Window** click-picks any top-level window (an app in
  your stack, a helper window, even the board itself) and remembers its title +
  position; **Snap to Grid** moves them all back into place with one click;
  right-click **Add** clears the grid. Handles are re-resolved by title, so a
  window reopened at a new spot is still found. Positions live in a git-ignored
  `soc_grid.json`. Windows-only (uses `MoveWindow` via ctypes).
- **Collapsible log** — click the **Log** header to hide/show the log pane;
  collapsing shrinks the window to reclaim desktop space, expanding restores it.
- **Second-instance prevention** — double-launching just brings the existing
  board to the front (port-bind lock; no stale lockfiles).
- **Virtual-desktop dock** (Windows-only) — the bottom rectangle pulses yellow/orange while
  your stack runs on ANOTHER Windows virtual desktop; click to hop in/out
  with everything left running. Uses only the documented
  `IVirtualDesktopManager` API. To keep the board visible on both desktops:
  Win+Tab → right-click the board → "Show this window on all desktops"
  (once per session).
### Platform support

The board, registry, Start Stack, status dots, collapsible log and
single-instance lock are cross-platform. Window management is not: the snap
grid and the virtual-desktop dock are Win32 calls (`ctypes.windll`), and there
is no portable equivalent — Wayland deliberately refuses to let a client
enumerate, place or focus another application's windows.

Those controls are therefore **not built** off Windows rather than shown and
disabled. A visible control that cannot work invites a click and then has to
explain itself, and this one explained itself wrongly: it reported "SOC not
running" regardless of whether SOC was running.

- **Action entries** — `"action": true` entries fire-and-forget a command
  (e.g. write a control signal) without being tracked as running apps.
- **`--check` mode** — headless validation of your registry (paths + commands).

## Build a standalone exe (optional)

```
build_exe.bat   # installs PyInstaller on demand, bakes assets/master_widget.ico
```

The exe reads `soc_master_apps.json` from the folder it sits in.

## Files

| file | role |
|---|---|
| `soc_master_widget.py` | the whole app (stdlib only) |
| `soc_master_apps.example.json` | registry template — copy to `soc_master_apps.json` |
| `setup.bat` | SOC stack bootstrap (clone + pip + Tesseract) |
| `run.bat` / `build_exe.bat` | launch / build |
| `gen_launchers.py` | emits Linux `.sh` + `.desktop` launchers from the registry |
| `soc_show_a4.pyw` | example action entry (signals SOC to raise a window) |

Your real `soc_master_apps.json` is gitignored — registries are personal.
