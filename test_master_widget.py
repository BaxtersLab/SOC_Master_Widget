"""Tests for soc_master_widget per-OS registry resolution + gen_launchers output.

Run:  py -3 test_master_widget.py   (from the file-cabinet hub)
Stdlib only; no GUI window is created (load_config/check/gen only).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import gen_launchers
import soc_master_widget as w


def write_cfg(root: Path, apps: list[dict]) -> Path:
    p = root / "soc_master_apps.json"
    p.write_text(json.dumps({"title": "T", "apps": apps}), encoding="utf-8")
    return p


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "appdir").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_uses_cmd(self):
        cfg = write_cfg(self.root, [{"name": "A", "dir": "appdir",
                                     "cmd": ["x.exe"], "cmd_linux": ["./x"]}])
        data = w.load_config(platform="win", config_path=cfg)
        self.assertEqual(data["apps"][0]["_cmd"], ["x.exe"])
        self.assertTrue(data["apps"][0]["_ready"])

    def test_linux_uses_cmd_linux(self):
        cfg = write_cfg(self.root, [{"name": "A", "dir": "appdir",
                                     "cmd": ["x.exe"], "cmd_linux": ["./x"]}])
        data = w.load_config(platform="linux", config_path=cfg)
        self.assertEqual(data["apps"][0]["_cmd"], ["./x"])

    def test_linux_missing_cmd_is_disabled_not_error(self):
        cfg = write_cfg(self.root, [{"name": "A", "dir": "appdir", "cmd": ["x.exe"]}])
        data = w.load_config(platform="linux", config_path=cfg)
        self.assertEqual(data["apps"][0]["_cmd"], [])
        self.assertFalse(data["apps"][0]["_ready"])

    def test_dir_linux_override(self):
        (self.root / "ldir").mkdir()
        cfg = write_cfg(self.root, [{"name": "A", "dir": "appdir", "dir_linux": "ldir",
                                     "cmd": ["x.exe"], "cmd_linux": ["./x"]}])
        data = w.load_config(platform="linux", config_path=cfg)
        self.assertTrue(str(data["apps"][0]["_dir"]).endswith("ldir"))

    def test_apps_sorted_by_order(self):
        cfg = write_cfg(self.root, [
            {"name": "B", "order": 2, "dir": "appdir", "cmd": ["b"]},
            {"name": "A", "order": 1, "dir": "appdir", "cmd": ["a"]}])
        data = w.load_config(platform="win", config_path=cfg)
        self.assertEqual([a["name"] for a in data["apps"]], ["A", "B"])

    @unittest.skipUnless(sys.platform == "win32",
                         "asserts this Windows box's real registry dirs")
    def test_real_registry_still_ready_on_windows(self):
        # Regression: the live registry must stay fully READY on this box.
        data = w.load_config(platform="win")
        for a in data["apps"]:
            self.assertTrue(a["_dir"].is_dir(), f"{a['name']} dir missing")
            self.assertTrue(a["_cmd"], f"{a['name']} lost its Windows cmd")


class GenLaunchersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _gen(self, apps):
        cfg = write_cfg(self.root, apps)
        return gen_launchers.gen(cfg, self.root)

    def test_emits_sh_and_desktop_for_linux_apps(self):
        written, skipped = self._gen([
            {"name": "My App", "order": 1, "dir": "d", "dir_linux": "/opt/my",
             "cmd": ["x.exe"], "cmd_linux": ["./run.sh", "--flag"], "console": False}])
        names = [Path(p).name for p in written]
        self.assertIn("my-app.sh", names)
        self.assertIn("my-app.desktop", names)
        self.assertEqual(skipped, [])
        sh = (self.root / "launchers" / "my-app.sh").read_text(encoding="utf-8")
        self.assertIn("cd /opt/my", sh)
        self.assertIn("exec ./run.sh --flag", sh)
        desk = (self.root / "launchers" / "my-app.desktop").read_text(encoding="utf-8")
        self.assertIn("Name=My App", desk)
        self.assertIn("Terminal=false", desk)

    def test_windows_only_app_skipped(self):
        written, skipped = self._gen([{"name": "WinOnly", "dir": "d", "cmd": ["x.exe"]}])
        self.assertEqual(skipped, ["WinOnly"])
        self.assertNotIn("winonly.sh", [Path(p).name for p in written])

    def test_stack_respects_order_and_in_stack(self):
        self._gen([
            {"name": "Two", "order": 2, "dir": "d", "cmd_linux": ["b"]},
            {"name": "One", "order": 1, "dir": "d", "cmd_linux": ["a"]},
            {"name": "Off", "order": 3, "dir": "d", "cmd_linux": ["c"], "in_stack": False}])
        stack = (self.root / "launchers" / "start_stack.sh").read_text(encoding="utf-8")
        self.assertLess(stack.index("one.sh"), stack.index("two.sh"))
        self.assertNotIn("off.sh", stack)

    def _bash_syntax_ok(self, sh: Path):
        """`bash -n` the script via whichever bash exists (Git Bash or WSL).

        Git Bash takes C:/-style paths; WSL bash needs /mnt/<drive>/…; try both.
        """
        posix = sh.as_posix()
        candidates: list[tuple[str, str]] = []
        for gb in (r"C:\Program Files\Git\usr\bin\bash.exe",
                   r"C:\Program Files\Git\bin\bash.exe"):
            if Path(gb).exists():
                candidates.append((gb, posix))
        which = shutil.which("bash")
        if which:
            candidates.append((which, posix))
            m = re.match(r"^([A-Za-z]):/(.*)$", posix)
            if m:  # WSL path translation
                candidates.append((which, f"/mnt/{m.group(1).lower()}/{m.group(2)}"))
        if not candidates:
            self.skipTest("no bash available")
        errs = []
        for exe, path in candidates:
            r = subprocess.run([exe, "-n", path], capture_output=True, text=True)
            if r.returncode == 0:
                return
            errs.append(f"{exe} {path} -> rc {r.returncode}: {r.stderr.strip()}")
        self.fail(f"{sh.name}: bash -n failed via every candidate:\n" + "\n".join(errs))

    def test_generated_sh_passes_bash_syntax_check(self):
        self._gen([{"name": "A pp", "dir": "d", "cmd_linux": ["./x", "a b"]}])
        for sh in (self.root / "launchers").glob("*.sh"):
            self._bash_syntax_ok(sh)


class ActionEntryTests(unittest.TestCase):
    """Fire-and-forget 'action' entries (e.g. Show A4 Vision) must not be tracked
    as persistent apps, so no process lingers and the status dot stays idle."""

    def test_action_entry_not_tracked_in_procs(self):
        import unittest.mock as mock
        app = {"name": "Sig", "_cmd": ["x"], "_dir": Path("."), "action": True}
        procs = {}
        logs = []
        with mock.patch("subprocess.Popen", return_value=mock.Mock(pid=1)):
            w._launch(app, logs.append, procs)
        self.assertNotIn("Sig", procs)                       # fire-and-forget
        self.assertTrue(any("[action]" in m for m in logs))

    def test_normal_entry_is_tracked(self):
        import unittest.mock as mock
        app = {"name": "App", "_cmd": ["x"], "_dir": Path("."), "console": False}
        procs = {}
        with mock.patch("subprocess.Popen", return_value=mock.Mock(pid=2)):
            w._launch(app, lambda m: None, procs)
        self.assertIn("App", procs)                          # persistent app

    def test_show_a4_entry_present_and_ready(self):
        # The live registry must expose Show A4 Vision as a ready on-demand action.
        data = w.load_config(platform="win")
        entry = next((a for a in data["apps"] if a["name"] == "Show A4 Vision"), None)
        self.assertIsNotNone(entry, "Show A4 Vision entry missing from registry")
        self.assertTrue(entry.get("action"))
        self.assertFalse(entry.get("in_stack", True))        # on-demand, not in stack
        self.assertTrue(entry["_ready"])


@unittest.skipUnless(sys.platform == "win32", "window_title focus path is Windows-only")
class WindowTitleFocusTests(unittest.TestCase):
    """Regression for the VSCodium 'Start does nothing visible' bug: an app
    that enforces its own single-instance lock (Electron) hands off to its
    existing window and quits when relaunched, and that window's own attempt
    to raise itself is often refused by Windows' foreground-lock rules. An app
    declaring 'window_title' must be found-and-focused directly instead of
    relaunched, whether its existing window is normal or minimized."""

    def _app(self, **extra):
        base = {"name": "VSCodium", "_cmd": ["x.exe"], "_dir": Path("."),
                "console": False, "window_title": "VSCodium",
                "window_process": "VSCodium.exe"}
        base.update(extra)
        return base

    def test_existing_window_is_focused_not_relaunched(self):
        import unittest.mock as mock
        procs, logs = {}, []
        with mock.patch("soc_master_widget._find_window", return_value=999) as find, \
             mock.patch("soc_master_widget.restore_and_focus", return_value=True) as focus, \
             mock.patch("subprocess.Popen") as popen:
            status = w._launch(self._app(), logs.append, procs)
        find.assert_called_once_with("VSCodium", "VSCodium.exe")
        focus.assert_called_once_with(999)
        popen.assert_not_called()                             # no redundant process
        self.assertEqual(status, "focused")
        self.assertNotIn("VSCodium", procs)                   # nothing to track/poll
        self.assertTrue(any("[focus]" in m and "brought to front" in m for m in logs))

    def test_focus_failure_is_reported_not_silent(self):
        import unittest.mock as mock
        procs, logs = {}, []
        with mock.patch("soc_master_widget._find_window", return_value=999), \
             mock.patch("soc_master_widget.restore_and_focus", return_value=False):
            status = w._launch(self._app(), logs.append, procs)
        self.assertEqual(status, "focused")
        self.assertTrue(any("could not focus" in m for m in logs))

    def test_no_existing_window_spawns_normally(self):
        import unittest.mock as mock
        procs, logs = {}, []
        with mock.patch("soc_master_widget._find_window", return_value=None) as find, \
             mock.patch("subprocess.Popen", return_value=mock.Mock(pid=4321)) as popen:
            status = w._launch(self._app(), logs.append, procs)
        find.assert_called_once_with("VSCodium", "VSCodium.exe")
        popen.assert_called_once()
        self.assertEqual(status, "started")
        self.assertIn("VSCodium", procs)                      # tracked for status dot + poll

    def test_app_without_window_title_never_probes_windows(self):
        # Every existing app entry (no window_title) must be unaffected: no
        # ctypes window lookup at all, exactly the pre-change behavior.
        import unittest.mock as mock
        procs, logs = {}, []
        app = self._app(window_title=None)
        with mock.patch("soc_master_widget._find_window") as find, \
             mock.patch("subprocess.Popen", return_value=mock.Mock(pid=1)):
            w._launch(app, logs.append, procs)
        find.assert_not_called()


class VSCodiumDependencyTests(unittest.TestCase):
    """VSCodium is registered as a managed dependency ('auto_locate':
    'vscodium'): if its configured path ever goes stale the widget re-probes
    PATH/common install dirs itself, then a 'Locate/Install' button lets the
    operator browse to it or trigger a silent winget install. These test the
    injectable search/persist/install logic without touching the real
    filesystem, PATH, or actually invoking winget."""

    def test_candidate_paths_from_env(self):
        env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local",
               "ProgramFiles": r"C:\Program Files"}
        cands = [str(p) for p in w._candidate_vscodium_paths(env)]
        self.assertIn(r"C:\Users\x\AppData\Local\Programs\VSCodium\VSCodium.exe", cands)
        self.assertIn(r"C:\Program Files\VSCodium\VSCodium.exe", cands)

    def test_candidate_paths_skips_unset_env_vars(self):
        self.assertEqual(w._candidate_vscodium_paths({}), [])

    def test_find_vscodium_direct_exe_on_path(self):
        target = Path(r"C:\Users\x\AppData\Local\Programs\VSCodium\VSCodium.exe")
        found = w.find_vscodium(
            env={}, which=lambda name: str(target) if name == "VSCodium.exe" else None,
            is_file=lambda p: Path(p) == target)
        self.assertEqual(found, target)

    def test_find_vscodium_resolves_codium_cmd_sibling(self):
        # `codium` on PATH typically points at .../VSCodium/bin/codium(.cmd);
        # the real exe is one directory up.
        root = Path(r"C:\Users\x\AppData\Local\Programs\VSCodium")
        cmd_path = root / "bin" / "codium.cmd"
        exe_path = root / "VSCodium.exe"
        found = w.find_vscodium(
            env={},
            which=lambda name: str(cmd_path) if name == "codium" else None,
            is_file=lambda p: Path(p) == exe_path)
        self.assertEqual(found, exe_path)

    def test_find_vscodium_falls_back_to_candidate_dirs(self):
        env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}
        target = Path(r"C:\Users\x\AppData\Local\Programs\VSCodium\VSCodium.exe")
        found = w.find_vscodium(env=env, which=lambda name: None,
                                 is_file=lambda p: Path(p) == target)
        self.assertEqual(found, target)

    def test_find_vscodium_returns_none_when_nowhere(self):
        found = w.find_vscodium(env={}, which=lambda name: None, is_file=lambda p: False)
        self.assertIsNone(found)

    def test_set_app_path_updates_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = write_cfg(Path(d), [
                {"name": "VSCodium", "dir": "old", "cmd": ["old.exe"]},
                {"name": "Other", "dir": "keep", "cmd": ["keep.exe"]}])
            new_exe = Path(d) / "new_spot" / "VSCodium.exe"
            ok = w.set_app_path(cfg, "VSCodium", new_exe)
            self.assertTrue(ok)
            data = json.loads(cfg.read_text(encoding="utf-8"))
            entries = {a["name"]: a for a in data["apps"]}
            self.assertEqual(entries["VSCodium"]["cmd"], [str(new_exe)])
            self.assertEqual(entries["VSCodium"]["dir"], str(new_exe.parent))
            self.assertEqual(entries["Other"]["cmd"], ["keep.exe"])   # untouched

    def test_set_app_path_unknown_app_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = write_cfg(Path(d), [{"name": "A", "dir": "d", "cmd": ["a.exe"]}])
            before = cfg.read_text(encoding="utf-8")
            ok = w.set_app_path(cfg, "NoSuchApp", Path("x.exe"))
            self.assertFalse(ok)
            self.assertEqual(cfg.read_text(encoding="utf-8"), before)   # untouched

    def test_install_winget_missing_reports_not_found(self):
        ok, msg = w.install_vscodium_via_winget(which=lambda name: None)
        self.assertFalse(ok)
        self.assertIn("winget not found", msg)

    def test_install_winget_success(self):
        import unittest.mock as mock
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        ok, msg = w.install_vscodium_via_winget(run=run, which=lambda name: "winget.exe")
        self.assertTrue(ok)
        self.assertIn("installed", msg)
        run.assert_called_once()
        self.assertIn("VSCodium.VSCodium", run.call_args[0][0])

    def test_install_winget_nonzero_exit_reports_stderr(self):
        import unittest.mock as mock
        run = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="no package found"))
        ok, msg = w.install_vscodium_via_winget(run=run, which=lambda name: "winget.exe")
        self.assertFalse(ok)
        self.assertIn("no package found", msg)

    def test_install_winget_exception_is_caught(self):
        def boom(*a, **k):
            raise OSError("boom")
        ok, msg = w.install_vscodium_via_winget(run=boom, which=lambda name: "winget.exe")
        self.assertFalse(ok)
        self.assertIn("OSError", msg)


class SingletonTests(unittest.TestCase):
    """Second-instance prevention: bind-lock port + ping-to-front handshake.
    Uses a test-only port so a live widget (real port 47611) never interferes."""

    ADDR = ("127.0.0.1", 47913)

    def test_second_acquire_fails_while_first_held(self):
        first = w.acquire_singleton(self.ADDR)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(w.acquire_singleton(self.ADDR))
        finally:
            first.close()

    def test_lock_frees_on_close(self):
        first = w.acquire_singleton(self.ADDR)
        self.assertIsNotNone(first)
        first.close()
        second = w.acquire_singleton(self.ADDR)
        self.assertIsNotNone(second)
        second.close()

    def test_notify_pings_the_holder(self):
        import threading, time
        first = w.acquire_singleton(self.ADDR)
        self.assertIsNotNone(first)
        hits = []
        t = threading.Thread(target=w.watch_singleton,
                             args=(first, lambda: hits.append(1)), daemon=True)
        t.start()
        w.notify_existing(self.ADDR)
        deadline = time.time() + 2.0
        while not hits and time.time() < deadline:
            time.sleep(0.02)
        # release (NOT bare close): on Linux a blocked accept() pins the
        # listen socket in the kernel, keeping the port bound for later tests.
        w.release_singleton(first)
        t.join(timeout=3.0)
        self.assertFalse(t.is_alive(), "watcher thread failed to exit on release")
        self.assertTrue(hits, "holder never received the bring-to-front ping")

    def test_release_frees_port_even_with_live_watcher(self):
        """Linux regression: close() alone left the watcher pinning the port
        (EADDRINUSE on relaunch within seconds). release_singleton must free
        it deterministically."""
        import threading
        first = w.acquire_singleton(self.ADDR)
        self.assertIsNotNone(first)
        t = threading.Thread(target=w.watch_singleton,
                             args=(first, lambda: None), daemon=True)
        t.start()
        w.release_singleton(first)
        t.join(timeout=3.0)
        self.assertFalse(t.is_alive())
        second = w.acquire_singleton(self.ADDR)
        self.assertIsNotNone(second, "port still pinned after release")
        w.release_singleton(second)

    def test_notify_with_no_holder_is_silent(self):
        # No instance holds the port — must not raise.
        w.notify_existing(("127.0.0.1", 47914))


class GridTests(unittest.TestCase):
    """Snap grid: click-add arbitrary windows (agent / A4 / outbox / this widget),
    dedupe by title, one-click snap-all. The win32 capture/move needs a live
    desktop; these cover the pure registry + persistence logic that drives it."""

    def test_valid_title_rejects_empty_and_desktop(self):
        self.assertTrue(w.grid_valid_title("A4 Vision"))
        self.assertTrue(w.grid_valid_title("SOC Master Widget"))
        self.assertFalse(w.grid_valid_title(""))
        self.assertFalse(w.grid_valid_title("   "))
        self.assertFalse(w.grid_valid_title("Program Manager"))   # desktop shell
        self.assertFalse(w.grid_valid_title("program manager"))

    def test_upsert_appends_new(self):
        g = w.grid_upsert([], "A4 Vision", [10, 20, 300, 400])
        self.assertEqual(g, [{"title": "A4 Vision", "rect": [10, 20, 300, 400]}])

    def test_upsert_updates_in_place_no_duplicate(self):
        g = [{"title": "A4", "rect": [0, 0, 1, 1]},
             {"title": "OB", "rect": [5, 5, 5, 5]}]
        g2 = w.grid_upsert(g, "A4", [10, 10, 200, 200])
        self.assertEqual(g2, [{"title": "A4", "rect": [10, 10, 200, 200]},
                              {"title": "OB", "rect": [5, 5, 5, 5]}])

    def test_upsert_preserves_order_on_readd(self):
        g = w.grid_upsert([], "W1", [0, 0, 1, 1])
        g = w.grid_upsert(g, "W2", [0, 0, 1, 1])
        g = w.grid_upsert(g, "W1", [9, 9, 9, 9])   # re-capture W1's position
        self.assertEqual([x["title"] for x in g], ["W1", "W2"])
        self.assertEqual(g[0]["rect"], [9, 9, 9, 9])

    def test_title_match_prefix_tolerant(self):
        self.assertTrue(w.title_match("Agent 1", "Agent 1 — Copilot"))   # gained suffix
        self.assertTrue(w.title_match("SOC Ultralight — running", "SOC Ultralight"))
        self.assertFalse(w.title_match("Agent 1", "Agent 2"))
        self.assertFalse(w.title_match("", "anything"))

    def test_grid_roundtrips_through_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "soc_grid.json"
            grid = w.grid_upsert([], "A4 Vision", [1, 2, 3, 4])
            w.save_grid(grid, p)
            self.assertEqual(w.load_grid(p), grid)

    def test_load_grid_missing_or_garbage_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(w.load_grid(Path(d) / "nope.json"), [])
            bad = Path(d) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(w.load_grid(bad), [])
            wrong = Path(d) / "wrong.json"
            wrong.write_text('{"title": "x"}', encoding="utf-8")   # object, not list
            self.assertEqual(w.load_grid(wrong), [])

    def test_load_grid_drops_malformed_entries(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "soc_grid.json"
            p.write_text(json.dumps([
                {"title": "Good", "rect": [0, 0, 10, 10]},
                {"title": "NoRect"},
                {"rect": [1, 1, 1, 1]},
                "not-a-dict",
            ]), encoding="utf-8")
            self.assertEqual(w.load_grid(p), [{"title": "Good", "rect": [0, 0, 10, 10]}])


if __name__ == "__main__":
    unittest.main(verbosity=1)
