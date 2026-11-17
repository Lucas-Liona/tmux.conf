#!/usr/bin/env python3
"""Pins for the local-models window (scripts/local-models).

    python3 ~/.config/tmux/scripts/local-models-check.py

A throwaway tmux server, never the live one. fleet.json is a file in a temp dir that the tests
rewrite (LOCAL_MODELS_FLEET); workmux is a stub that writes down that it ran (LOCAL_MODELS_WORKMUX).
Nothing on the network is asked, and workmux's records are not written.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "local-models"
MENU = HERE.parent / "click-menu.conf"
_loader = SourceFileLoader("local_models", str(SCRIPT))
local = importlib.util.module_from_spec(importlib.util.spec_from_loader("local_models", _loader))
_loader.exec_module(local)


def snapshot(at=None, wsl=(), windows=(), ct100=(), runs=(), mlflow_up=True, pi=False):
    """fleet.json's shape as `fleet --write` wrote it on 2026-11-17 06:33 (~/.local/bin/fleet, snapshot())."""
    side = lambda found, up=True: {"up": up, "models": list(found)}
    return {"at": at or time.strftime("%Y-%m-%d %H:%M"), "lucas": "afk", "lucas2": "idle",
            "lanes": {"working": 3, "done": 6},
            "local": {"wsl": side(wsl), "windows": side(windows, bool(windows))},
            "homelab": {"ct100_gpu": side(ct100), "mlflow": {"up": mlflow_up, "running": list(runs)}},
            "pi": pi}


EMBED = {"name": "nomic-embed-text:latest", "gb": 0.3, "gpu_gb": 0.0}
GPT = {"name": "cpu-gpt-oss:20b", "gb": 13.1, "gpu_gb": 0.0}
QWEN = {"name": "qwen2.5-coder:14b", "gb": 9.8, "gpu_gb": 9.8}
NINE = [f"ken_kitchen_{n}" for n in range(9)]


class Titles(unittest.TestCase):
    def test_what_fleet_saw_on_one_line_local_then_ct100_runs_and_pi(self):
        self.assertEqual(local.title(snapshot(wsl=[EMBED], runs=NINE)),
                         "nomic-embed-text 0.3G · ct100 idle · 9 runs · pi not installed")
        self.assertEqual(local.title(snapshot(wsl=[EMBED], windows=[GPT], ct100=[QWEN], pi=True)),
                         "nomic-embed-text 0.3G, cpu-gpt-oss:20b 13.1G · ct100 qwen2.5-coder:14b · 0 runs · pi installed")

    def test_nothing_anywhere_and_mlflow_down(self):
        self.assertEqual(local.title(snapshot(mlflow_up=False)), "local idle · ct100 idle · mlflow down · pi not installed")

    def test_no_snapshot_and_a_stale_one(self):
        self.assertEqual(local.title(None), "fleet: no snapshot")
        old = snapshot(at="2026-11-17 06:33", wsl=[EMBED])
        then = time.mktime(time.strptime("2026-11-17 06:33", "%Y-%m-%d %H:%M"))
        self.assertEqual(local.title(old, now=then + 179), "nomic-embed-text 0.3G · ct100 idle · 0 runs · pi not installed")
        self.assertEqual(local.title(old, now=then + 181), "fleet: stale since 06:33")
        self.assertEqual(local.title({"lucas": "afk"}), "fleet: stale since ?")

    def test_the_body_names_every_surface_and_each_run(self):
        text = "\n".join(local.body(snapshot(wsl=[EMBED], runs=NINE)))
        for want in ("WSL Ollama", "nomic-embed-text:latest 0.3G (0.0G GPU)", "Windows Ollama (relay :11435)   down",
                     "CT 100 Ollama (GPU LXC)         nothing loaded", "9 marked running", "ken_kitchen_8",
                     "Pi coding agent                 not installed"):
            self.assertIn(want, text)

    def test_the_menu_has_the_toggle_on_l(self):
        lines = [line for line in MENU.read_text().splitlines() if line.startswith("local models ")]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^local models +l +tmux: run-shell -b '~/\.config/tmux/scripts/local-models --toggle'$")


class Window(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="local-models-check-"))
        self.sock = str(self.tmp / "tmux.sock")
        self.fleet = self.tmp / "fleet.json"
        self.workmux_ran = self.tmp / "workmux.log"
        workmux = self.tmp / "workmux"
        workmux.write_text(f'#!/bin/sh\necho "$TMUX_PANE $* [$(tmux display-message -p -t "$TMUX_PANE" "#{{pane_title}}")]" >> {self.workmux_ran}\n')
        workmux.chmod(0o755)
        self.env = {"LOCAL_MODELS_FLEET": str(self.fleet), "LOCAL_MODELS_WORKMUX": str(workmux), "LOCAL_MODELS_EVERY": "0.3"}
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "elsewhere", "-n", "view", "-x", "100", "-y", "30", "cat")
        for name, value in self.env.items():
            self.tmux("set-environment", "-g", name, value)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.tmux, "kill-server")

    def write(self, snap):
        part = self.fleet.with_suffix(".part")  # as a whole file at once, the way a reader would see a rename
        part.write_text(json.dumps(snap))
        part.replace(self.fleet)

    def tmux(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", self.sock, *args], capture_output=True, text=True, env=env).stdout.rstrip("\n")

    def toggle(self):
        run = subprocess.run([str(SCRIPT), "--toggle"], capture_output=True, text=True,
                             env={**os.environ, **self.env, "TMUX": f"{self.sock},0,0"})
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))

    def windows(self):
        return self.tmux("list-windows", "-a", "-F", "#{session_name}:#{window_name}").split("\n")

    def wait(self, what, said, seconds=8):
        end = time.time() + seconds
        while time.time() < end:
            if what():
                return
            time.sleep(0.1)
        self.fail(said())

    def window(self):
        """The local-models window's id, or None: by id, since a name is looked up in one session only."""
        rows = self.tmux("list-windows", "-a", "-F", "#{window_id} #{window_name}").split("\n")
        return next((row.split()[0] for row in rows if row.endswith(" local-models")), None)

    def of(self, what):
        return self.tmux("display-message", "-p", "-t", self.window(), what) if self.window() else None

    def title(self):
        return self.of("#{pane_title}")

    def wait_title(self, want):
        self.wait(lambda: self.title() == want, lambda: f"title is {self.title()!r}, wanted {want!r}")

    def workmux(self):
        return self.workmux_ran.read_text().splitlines() if self.workmux_ran.exists() else []

    def test_the_title_follows_fleet_json_and_workmux_is_told(self):
        self.write(snapshot(wsl=[EMBED], runs=NINE))
        self.toggle()
        loaded = "nomic-embed-text 0.3G · ct100 idle · 9 runs · pi not installed"
        self.wait_title(loaded)
        pane = self.of("#{pane_id}")
        self.wait(lambda: len(self.workmux()) >= 2, self.workmux)
        self.assertEqual(self.workmux()[:2], [f"{pane} register-agent [{loaded}]",  # registered with its title set:
                                              f"{pane} set-window-status working [{loaded}]"])  # workmux records that one
        self.write(snapshot(runs=NINE))  # runs marked RUNNING alone are not "working": a crashed one stays marked
        self.wait_title("local idle · ct100 idle · 9 runs · pi not installed")
        self.wait(lambda: self.workmux()[-1].startswith(f"{pane} set-window-status done ["), self.workmux)
        self.write(snapshot(ct100=[QWEN]))
        self.wait_title("local idle · ct100 qwen2.5-coder:14b · 0 runs · pi not installed")
        self.wait(lambda: self.workmux()[-1].startswith(f"{pane} set-window-status working ["), self.workmux)
        self.write(snapshot(wsl=[EMBED]))  # another model, the same status: the title moves, workmux is not called
        self.wait_title("nomic-embed-text 0.3G · ct100 idle · 0 runs · pi not installed")
        time.sleep(1)
        self.assertEqual(len(self.workmux()), 4)
        shown = self.tmux("capture-pane", "-p", "-t", self.window())
        self.assertIn("CT 100 Ollama (GPU LXC)", shown)
        self.assertIn("Pi coding agent                 not installed", shown)

    def test_a_missing_then_stale_snapshot_says_so_and_is_done(self):
        self.toggle()
        self.wait_title("fleet: no snapshot")
        self.write(snapshot(at="2026-11-17 06:33", wsl=[EMBED]))
        self.wait_title("fleet: stale since 06:33")
        self.wait(lambda: self.workmux() and self.workmux()[-1].split()[1:3] == ["set-window-status", "done"], self.workmux)
        self.assertNotIn("working", " ".join(self.workmux()))

    def test_it_asks_nothing_on_the_network(self):
        """No Ollama, relay or MLflow call from the tile: fleet does those. Every connect() refused, the title still comes."""
        guard = self.tmp / "sitecustomize.py"
        guard.write_text("import socket\ndef _no(*a, **k): raise SystemExit('local-models connected to the network')\n"
                         "socket.socket.connect = _no\nsocket.create_connection = _no\n")
        self.env["PYTHONPATH"] = str(self.tmp)
        self.tmux("set-environment", "-g", "PYTHONPATH", str(self.tmp))
        self.write(snapshot(wsl=[EMBED]))
        self.toggle()
        self.wait_title("nomic-embed-text 0.3G · ct100 idle · 0 runs · pi not installed")
        time.sleep(1)
        self.assertEqual(self.of("#{pane_current_command}").startswith("python"), True)  # still polling, not exited

    def test_the_toggle_opens_it_in_main_behind_the_view_and_closes_it_again(self):
        self.write(snapshot(wsl=[EMBED]))
        self.tmux("new-session", "-d", "-s", "main", "-n", "coord", "cat")
        view = lambda: self.tmux("list-windows", "-a", "-F", "#{session_name}:#{window_name} #{window_active}")
        before = view()
        self.toggle()
        self.assertIn("main:local-models", self.windows())
        self.assertEqual([row for row in view().split("\n") if "local-models" not in row], before.split("\n"))
        self.assertIn("main:local-models 0", view())  # not the window in view
        self.assertEqual(self.of("#{pane_current_path}"), str(Path.home()))
        poller = self.of("#{pane_pid}")
        self.toggle()
        self.wait(lambda: not any(w.endswith(":local-models") for w in self.windows()), self.windows)
        self.wait(lambda: not os.path.exists(f"/proc/{poller}"), lambda: "the poller is still running")
        self.assertEqual(view(), before)

    def test_with_no_session_called_main_it_opens_where_the_toggle_ran(self):
        self.toggle()
        self.assertIn("elsewhere:local-models", self.windows())


if __name__ == "__main__":
    unittest.main(verbosity=2)
