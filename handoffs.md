# handoffs — SOC Master Widget

_Append-only (Article VIII). Newest entry at the top._

## [2026-08-07] — The scrub in `run.sh` is now executed by a test, not trusted; "21 vars" corrected

Follow-on to the entry below. SOC Ultralight's `run.sh` got the same scrub block
this session, and writing a test for it exposed that **this board's copy — the
higher-stakes one — was equally untested.** The board spawns HRT, Chatbox and
SOC as children, so the environment it holds is the environment they inherit: a
hole here is a hole in four apps at once, and it would surface in a child rather
than in this repo.

Added `test_run_sh_scrub.py` (same file as SOC's, paths and rationale adjusted).
It does not grep `run.sh` for variable names — it cuts the shipped block out
between `# (scrub:begin)` / `# (scrub:end)`, runs it under bash with a real
contaminated environment, and asserts on what survives. Markers added to
`run.sh`; the block itself is unchanged.

**Corrected the "a VS Code terminal leaks 21 of these" comment**, inherited from
HRT's copy. Measured 18 on this box today. The count moves with the installed
extension set, so it now reads ~20 and records both numbers rather than
asserting a fixed one.

### Open Stubs

None introduced.

### Verification

* `pytest` → **50 passed, 8 skipped** (37 + 8 before; +13 scrub tests).
* `./run.sh --check` after the edit → all 6 apps `READY`, `RESULT: all dirs
  present` — the markers did not disturb the launcher.
* `bash -n run.sh` clean.
* Mutation-checked in the SOC repo against the identical block: moving the scrub
  after the first python invocation, and dropping `GTK_IM_MODULE_FILE` from the
  explicit list, each fail the suite. See SOC's handoff entry of this date.

## [2026-08-07] — `run.sh` written (the board had no Linux launcher), desktop entry + icon, AiSmartGuy button removed

Operator reported the board "does not have icons and a way to start the app".

**There was no Linux launcher.** The repo shipped `run.bat` only, so nothing on
this box did the two things the board needs before it opens: bootstrap
`soc_master_apps.json` from the example, and hand the process a clean
environment. Added `run.sh`. No venv — the board is Tkinter + stdlib only, so
system `python3` runs it directly; what the script is for is the scrub.

**The scrub matters more here than in any sibling app.** The board's whole job
is spawning HRT, Chatbox and SOC as children, so anything leaked into it is
inherited by all of them. Same block as `Baxters_Ai_Hot_Rod_Tuner/run.sh`
(scrub-by-rule, `XDG_DATA_DIRS` filtered not unset). Confirmed live this
session: the VS Code terminal on this box leaks **18** `/snap/` variables
including `LOCPATH`, which kills any python child outright.

**Added a desktop entry + icon.** `packaging/soc-master-widget.desktop` →
`~/.local/share/applications/`; `assets/master_widget.ico` extracted to the
16–256 hicolor PNG ladder as `soc-master-widget`. The `.ico` already carried
every size as PNG frames, so this is a re-container, not a resample.

**Two Tk apps were fighting over one identity.** Tk's default `WM_CLASS` is the
generic `"tk"/"Tk"` (measured with `xprop`), and SOC Ultralight is also Tk — so
GNOME could not tell them apart to pick a dash icon. Now
`tk.Tk(className="soc-master-widget")`, matching `StartupWMClass`. The existing
`iconbitmap(ICON)` was a **silent no-op on Linux** — it is Windows-only for
`.ico`, X11 wants an XBM, and the bare `except: pass` hid it. Now `iconphoto`
with the PNG on non-Windows, `.ico` path kept for Windows.

**AiSmartGuy removed from the registry** at operator instruction: it is not an
incorporated app of the suite. Board is 6 buttons, orders 1–6, unchanged
otherwise.

### Open Stubs

None introduced.

### Verification

* `pytest test_master_widget.py` → **37 passed, 8 skipped** (Windows-only paths).
* `./run.sh --check` → all 6 registered apps `READY`, `RESULT: all dirs present`.
* Registry re-parsed after the removal: valid JSON, 6 apps, no AiSmartGuy.
* `desktop-file-validate soc-master-widget.desktop` → clean.
* Launched **through the desktop entry** (`gio launch`), not by hand: window
  `"SOC Master Widget"` mapped, `WM_CLASS = "soc-master-widget",
  "Soc-master-widget"` — matches `StartupWMClass` — and `_NET_WM_ICON` carries a
  live 256×256 payload.
* The file-cabinet registry was **older** than the workspace one (GGUF Chatbox
  still on `./target/debug/gguf-chatbox-app` rather than `./run.sh`, no Timer
  Prompt). Copy direction was workspace → file cabinet, per CLAUDE.md.

## [2026-08-03] — Linux bring-up: registry written, Show A4 Vision signal ported, stack launches

### Done

**The widget already supported Linux; it had no registry.** `load_config` honours `cmd_linux` /
`dir_linux` (soc_master_widget.py:507), so nothing in the widget itself needed changing. What was
missing was `soc_master_apps.json` — only the `.example.json` ships, because the real registry is
private and gitignored.

Wrote a registry for this workstation, four apps:

| order | app | in stack | cmd_linux |
|---|---|---|---|
| 1 | GGUF Chatbox | yes | `./target/debug/gguf-chatbox-app` |
| 2 | SOC Ultralight | yes | `./.venv/bin/python soc_ultralight.py` |
| 3 | VS Mic Widget | no | `/usr/bin/python3 stt_widget.py` |
| 4 | Show A4 Vision | no | `/usr/bin/python3 soc_show_a4.py` |

Order matters: GGUF Chatbox serves the endpoints SOC's A4v plugin depends on (:8080 chat, :8082
vision), so it starts first. The Windows `cmd` arrays are left intact so the same file still works
on the Windows box.

**`soc_show_a4.py`** — Linux counterpart to the existing `.pyw`. Writes `soc_control.signal` in
SOC's folder; SOC polls it every 0.5 s and clears it. No process is launched, which is the point of
the design: nothing here can be orphaned. `SOC_ROOT` overrides the location (Article XI).

**Verified end to end on Linux:** Start Stack launched GGUF Chatbox and SOC in order; SOC loaded
`plugins/v_plugin` in-process and the `Agent 4 · Vision` window exists; the signal file was written
and observed being consumed by SOC's poll loop.

### Remaining

* **`Show A4 Vision` creates/keeps the window but cannot RAISE it on Wayland.** GNOME blocks
  focus-stealing by a background app, so the window is flagged "demands attention" instead of
  coming forward — the operator reported "A4 did not arrive" while the window existed the whole
  time. No Wayland client can lift another client's window; the honest fix is for the plugin to
  present the window itself rather than relying on a raise after the fact.
* The widget's log prints Windows docking advice (`Win+Tab -> 'Show this window on all desktops'`)
  on Linux, where it is meaningless. Cosmetic.
* VS Mic Widget is registered and `_ready`, but **whisper.cpp is not built and `ffmpeg`/`cmake` are
  not installed**, so the button will start a widget that cannot transcribe until `install.sh` is
  run. Note the CPU here (Xeon E5-1620 v3, Haswell) HAS AVX2/FMA, so the `-DGGML_AVX2=OFF
  -DGGML_FMA=OFF` flags in that installer should be removed for speed.
* Its auto-paste uses `xdotool` to focus and send Ctrl+V — works for XWayland targets (VS Code)
  but not native Wayland windows.

### Decisions

* **VS Mic Widget and Show A4 Vision are `in_stack: false`.** Neither belongs in the model
  pipeline: one is an operator tool, the other a signal to an already-running SOC.
* **A4v is not registered as an app.** SOC imports `plugins/v_plugin` into its own process, so
  there is nothing separate to launch — the only entry it warrants is the on-demand signal.
* Registry left gitignored, matching the existing convention documented in
  `test_master_widget.py::test_show_a4_entry_present_and_ready`.

### Open Stubs

None introduced.

### Verification

* `pytest test_master_widget.py` → **34 passed, 8 skipped** (Windows-only paths). One more test
  than before: creating the registry activated `test_show_a4_entry_present_and_ready`, which had
  been skipping for lack of a local registry. It failed first and named the required entry
  `"Show A4 Vision"` — the registry was corrected to match rather than the test loosened.
* `load_config(platform="linux")` reports `_ready=True` for all four apps.
* Start Stack log: `[start] GGUF Chatbox: pid 20432`, `[start] SOC Ultralight: pid 20433`,
  `[stack] done`. The Chatbox pid then deferred to an already-running instance via the
  single-instance guard, which is correct behaviour.
* `soc_show_a4.py` wrote the signal; the file was observed emptied by SOC within 3 s, proving the
  control channel is live.
* NOT VERIFIED: VS Mic Widget actually transcribing (whisper.cpp unbuilt).
