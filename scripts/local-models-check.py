#!/usr/bin/env python3
"""Pins for the local-models window (scripts/local-models).

    python3 ~/.config/tmux/scripts/local-models-check.py

A throwaway tmux server, never the live one. Ollama is a small HTTP server in this process; the
relay and workmux are stubs that write down that they ran (LOCAL_MODELS_PORT, _RELAY, _WORKMUX).
No model is loaded, nothing is asked of the real Ollama, and workmux's records are not written.
"""
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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

# /api/ps as the Windows Ollama answered on 2026-11-16 08:1x, one model on the CPU; then Ollama's own example,
# a model wholly on the GPU (docs/api.md, "List Running Models": no context_length in that version).
ON_CPU = {"name": "cpu-gpt-oss:20b", "size": 14048663960, "size_vram": 0, "context_length": 16384}
ON_GPU = {"name": "mistral:latest", "size": 5137025024, "size_vram": 5137025024}
SPLIT = {"name": "local-gpt-oss:20b-64k", "size": 16000000000, "size_vram": 7500000000, "context_length": 65536}
TAGS = {"models": [{"name": "cpu-gpt-oss:20b", "size": 13800000000}, {"name": "cpu-qwen2.5:3b", "size": 1900000000}]}


class Titles(unittest.TestCase):
    def test_a_loaded_model_reads_name_ram_gpu_context(self):
        self.assertEqual(local.title({"models": [ON_CPU]}), "cpu-gpt-oss:20b · 14.0G RAM · 0.0G GPU · 16k")
        self.assertEqual(local.title({"models": [SPLIT]}), "local-gpt-oss:20b-64k · 8.5G RAM · 7.5G GPU · 64k")

    def test_ram_is_what_is_not_on_the_gpu_and_a_missing_context_is_left_out(self):
        self.assertEqual(local.title({"models": [ON_GPU]}), "mistral:latest · 0.0G RAM · 5.1G GPU")

    def test_more_than_one_loaded_is_the_first_and_a_count(self):
        self.assertEqual(local.title({"models": [ON_CPU, ON_GPU, SPLIT]}), "cpu-gpt-oss:20b · 14.0G RAM · 0.0G GPU · 16k +2")

    def test_nothing_loaded_and_unreachable(self):
        self.assertEqual(local.title({"models": []}), "ollama: nothing loaded")
        self.assertEqual(local.title({}), "ollama: nothing loaded")
        self.assertEqual(local.title(None), "ollama: unreachable")

    def test_the_menu_has_the_toggle_on_l(self):
        lines = [line for line in MENU.read_text().splitlines() if line.startswith("local models ")]
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^local models +l +tmux: run-shell -b '~/\.config/tmux/scripts/local-models --toggle'$")


class Ollama:
    """A stand-in for Ollama on a free port: /api/ps answers with self.loaded, /api/tags with TAGS; down() stops it."""

    def __init__(self):
        outer = self
        self.loaded, self.asked = [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.asked.append(self.path)
                body = json.dumps({"models": outer.loaded} if self.path == "/api/ps" else TAGS).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # anything that could load a model
                outer.asked.append("POST " + self.path)
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args):
                pass
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = str(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def down(self):
        self.server.shutdown()
        self.server.server_close()


class Window(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="local-models-check-"))
        self.sock = str(self.tmp / "tmux.sock")
        self.ollama = Ollama()
        self.relay_ran, self.workmux_ran = self.tmp / "relay.pids", self.tmp / "workmux.log"
        relay = self.tmp / "relay"
        relay.write_text(f"#!/bin/sh\necho $$ $RELAY_PORT >> {self.relay_ran}\nexec sleep 600\n")
        workmux = self.tmp / "workmux"
        workmux.write_text(f'#!/bin/sh\necho "$TMUX_PANE $* [$(tmux display-message -p -t "$TMUX_PANE" "#{{pane_title}}")]" >> {self.workmux_ran}\n')
        for stub in (relay, workmux):
            stub.chmod(0o755)
        self.env = {"LOCAL_MODELS_PORT": self.ollama.port, "LOCAL_MODELS_RELAY": str(relay),
                    "LOCAL_MODELS_WORKMUX": str(workmux), "LOCAL_MODELS_EVERY": "0.3"}
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "elsewhere", "-n", "view", "-x", "100", "-y", "20", "cat")
        for name, value in self.env.items():
            self.tmux("set-environment", "-g", name, value)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.tmux, "kill-server")
        self.addCleanup(self.ollama.down)

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

    def relays(self):
        """Pids of the stub relays started so far that are still running."""
        pids = [line.split()[0] for line in self.relay_ran.read_text().splitlines()] if self.relay_ran.exists() else []
        return [pid for pid in pids if os.path.exists(f"/proc/{pid}")]

    def test_the_title_follows_what_is_loaded_and_workmux_is_told(self):
        self.ollama.loaded = [ON_CPU]
        self.toggle()
        loaded = "cpu-gpt-oss:20b · 14.0G RAM · 0.0G GPU · 16k"
        self.wait_title(loaded)
        pane = self.of("#{pane_id}")
        self.wait(lambda: len(self.workmux()) >= 2, self.workmux)
        self.assertEqual(self.workmux()[:2], [f"{pane} register-agent [{loaded}]",  # registered with its title set:
                                              f"{pane} set-window-status working [{loaded}]"])  # workmux records that one
        self.ollama.loaded = []
        self.wait_title("ollama: nothing loaded")
        self.wait(lambda: self.workmux()[-1] == f"{pane} set-window-status done [ollama: nothing loaded]", self.workmux)
        self.ollama.loaded = [SPLIT, ON_GPU]
        self.wait_title("local-gpt-oss:20b-64k · 8.5G RAM · 7.5G GPU · 64k +1")
        self.wait(lambda: self.workmux()[-1].startswith(f"{pane} set-window-status working ["), self.workmux)
        self.ollama.loaded = [ON_CPU]  # another model, the same status: the title moves, workmux is not called
        self.wait_title(loaded)
        time.sleep(1.5)
        self.assertEqual(len(self.workmux()), 4)
        self.assertEqual(set(self.ollama.asked), {"/api/ps", "/api/tags"})  # reads only: nothing that loads a model
        self.assertIn("cpu-qwen2.5:3b", self.tmux("capture-pane", "-p", "-t", self.window()))  # the body lists the tags
        self.assertEqual(self.relays(), [])  # Ollama answered all along: no relay of ours

    def test_unreachable_starts_the_relay_and_closing_the_window_stops_it_and_the_poller(self):
        self.ollama.down()
        self.toggle()
        self.wait_title("ollama: unreachable")
        self.wait(lambda: len(self.relays()) == 1, lambda: f"relays {self.relays()}")
        self.assertEqual(self.relay_ran.read_text().split()[1], self.ollama.port)  # on the port it reads
        time.sleep(1.5)
        self.assertEqual(len(self.relay_ran.read_text().splitlines()), 1)  # one relay, not one per poll
        poller = self.of("#{pane_pid}")
        self.toggle()
        self.wait(lambda: not any(w.endswith(":local-models") for w in self.windows()), self.windows)
        self.wait(lambda: self.relays() == [] and not os.path.exists(f"/proc/{poller}"),
                  lambda: f"left: relays {self.relays()}, poller alive {os.path.exists(f'/proc/{poller}')}")

    def test_a_poller_killed_outright_takes_its_relay_with_it(self):
        self.ollama.down()
        self.toggle()
        self.wait(lambda: len(self.relays()) == 1, lambda: f"relays {self.relays()}")
        poller = self.of("#{pane_pid}")
        os.kill(int(poller), 9)
        self.wait(lambda: self.relays() == [], lambda: f"relays {self.relays()}")

    def test_the_toggle_opens_it_in_main_behind_the_view_and_closes_it_again(self):
        self.ollama.loaded = [ON_CPU]
        self.tmux("new-session", "-d", "-s", "main", "-n", "coord", "cat")
        view = lambda: self.tmux("list-windows", "-a", "-F", "#{session_name}:#{window_name} #{window_active}")
        before = view()
        self.toggle()
        self.assertIn("main:local-models", self.windows())
        self.assertEqual([row for row in view().split("\n") if "local-models" not in row], before.split("\n"))
        self.assertIn("main:local-models 0", view())  # not the window in view
        self.assertEqual(self.of("#{pane_current_path}"), str(Path.home()))
        self.toggle()
        self.wait(lambda: not any(w.endswith(":local-models") for w in self.windows()), self.windows)
        self.assertEqual(view(), before)

    def test_a_relay_it_did_not_start_is_still_running_after_the_window_closes(self):
        """Someone else's relay is doing the work (the stub Ollama answers): used, never stopped."""
        theirs = self.tmp / "ollama-relay"
        theirs.write_text("#!/bin/sh\nexec sleep 600\n")
        theirs.chmod(0o755)
        other = subprocess.Popen([str(theirs)], start_new_session=True)
        self.addCleanup(other.kill)
        self.ollama.loaded = [ON_CPU]
        self.toggle()
        self.wait_title("cpu-gpt-oss:20b · 14.0G RAM · 0.0G GPU · 16k")
        poller = self.of("#{pane_pid}")
        self.toggle()
        self.wait(lambda: not os.path.exists(f"/proc/{poller}"), lambda: "the poller is still running")
        time.sleep(0.5)
        self.assertIsNone(other.poll())  # theirs: still running
        self.assertEqual(self.relays(), [])  # and none of ours was ever started

    def test_with_no_session_called_main_it_opens_where_the_toggle_ran(self):
        self.toggle()
        self.assertIn("elsewhere:local-models", self.windows())


if __name__ == "__main__":
    unittest.main(verbosity=2)
