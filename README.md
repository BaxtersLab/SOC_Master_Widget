# SOC Master Widget

A zero-dependency launcher board for a multi-app AI workstation — and a
one-click installer for the [SOC Ultralight](https://github.com/BaxtersLab2/SOC_Ultralight)
orchestrator stack.

One slim window to start your stack in order, watch each app's status dot,
and hop in/out of a dedicated virtual desktop where the swarm runs.

## Quick start

```
git clone https://github.com/BaxtersLab/SOC_Master_Widget
cd SOC_Master_Widget
setup.bat      # optional: installs the full SOC stack (SOC + pip deps + Tesseract)
run.bat        # opens the board (creates your registry from the example on first run)
```

The widget itself needs **nothing but Python 3.10+** (stdlib only — Tkinter, ctypes).
`setup.bat` is only for bootstrapping the SOC orchestrator environment.

## Features

- **Config-driven board** — register any apps in `soc_master_apps.json`
  (working dir + argv; per-OS overrides via `cmd_linux`). Status dots:
  grey idle · green running · red exited.
- **Start Stack** — launches your `order`-ed apps in sequence.
- **Second-instance prevention** — double-launching just brings the existing
  board to the front (port-bind lock; no stale lockfiles).
- **Virtual-desktop dock** — the bottom rectangle pulses yellow/orange while
  your stack runs on ANOTHER Windows virtual desktop; click to hop in/out
  with everything left running. Uses only the documented
  `IVirtualDesktopManager` API. To keep the board visible on both desktops:
  Win+Tab → right-click the board → "Show this window on all desktops"
  (once per session).
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
