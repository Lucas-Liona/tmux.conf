#!/usr/bin/env python3
"""Pins for gen (~/.local/bin/gen, chezmoi): the global event counter, its ring, and the number on the border.

    python3 ~/.config/tmux/scripts/gen-check.py
    GEN_BIN=<another gen> python3 ~/.config/tmux/scripts/gen-check.py Counter   # e.g. a copy without the lock

Every pin has its own GEN_DIR in a temp directory; the tmux ones use a throwaway server and a client of
it nested in a second throwaway server, never the live one.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CONF = HERE.parent / "tmux.conf"
GEN = os.environ.get("GEN_BIN", str(Path.home() / ".local/bin/gen"))
LINES = re.compile(r"^(set -g (mouse|pane-border-format|pane-border-status|status-right) |bind -n MouseDown1Status )")


def isolated(env: dict, paths: tuple) -> dict:
    """env for the commands under test, with TEST=1. Refuses (SystemExit) unless every state path in it is set
    and inside the temp directory, and TMUX names a socket there: a missing one means the live file or server.
    On 2026-11-18 an unset NEEDS_FILE put 4 test asks on Lucas's live badge."""
    tmp = Path(tempfile.gettempdir()).resolve()
    inside = lambda value: bool(value) and tmp in Path(value).resolve().parents
    bad = [name for name in (*paths, "TMUX") if not inside((env.get(name) or "").split(",")[0])]
    if bad:
        raise SystemExit(f"refusing to run: {', '.join(bad)} not set to a path under {tmp}")
    return {**env, "TEST": "1"}


class Guard(unittest.TestCase):
    """With TEST=1, gen refuses the live ring and the live tmux server. HOME and TMUX_TMPDIR are temp dirs."""

    def run_gen(self, home, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "GEN_DIR")}
        return subprocess.run([GEN, "bump", "startup"], capture_output=True, text=True,
                              env={**base, "HOME": home, "TMUX_TMPDIR": home, "TEST": "1", **env})

    def test_gen_under_test_refuses_an_unset_dir_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            run = self.run_gen(home, TMUX=f"{home}/none,0,0")
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((Path(home) / ".cache").exists(), "it wrote the default (live) ring")

    def test_gen_under_test_refuses_the_live_tmux_server(self):
        with tempfile.TemporaryDirectory() as home:
            run = self.run_gen(home, GEN_DIR=f"{home}/gen", TMUX_PANE="%0")  # no TMUX: the default server
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((Path(home) / "gen").exists())


class Counter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = isolated({**{k: v for k, v in os.environ.items() if k != "TMUX_PANE"}, "GEN_DIR": self.dir.name,
                             "TMUX": f"{self.dir.name}/none,0,0"}, ("GEN_DIR",))

    def tearDown(self):
        self.dir.cleanup()

    def gen(self, *args, stdin=None):
        return subprocess.run([GEN, *args], capture_output=True, text=True, env=self.env, input=stdin)

    def ring(self):
        return json.loads(self.gen("--json").stdout)["ring"]

    def test_fifty_at_once_end_at_plus_fifty_with_fifty_distinct_numbers(self):
        self.gen("bump", "startup")
        start = json.loads(self.gen("--json").stdout)["n"]
        procs = [subprocess.Popen([GEN, "bump", "compact", "--session", f"s{i}"], stdout=subprocess.PIPE, text=True,
                                  env=self.env) for i in range(50)]
        printed = [int(p.communicate()[0]) for p in procs]
        ring = self.ring()
        self.assertEqual(ring[-1]["n"], start + 50)
        self.assertEqual(sorted(printed), list(range(start + 1, start + 51)))  # each bump got its own number
        self.assertEqual(sorted(e["n"] for e in ring), list(range(start, start + 51)))
        self.assertEqual(len({e["session"] for e in ring if e["event"] == "compact"}), 50)  # none lost

    def test_the_ring_keeps_the_last_256(self):
        for _ in range(300):
            self.gen("bump", "resume")
        ring = self.ring()
        self.assertEqual(len(ring), 256)
        self.assertEqual([e["n"] for e in ring], list(range(45, 301)))
        self.assertIn("is not in the ring", self.gen("around", "10").stdout)
        around = self.gen("around", "200", "2").stdout.splitlines()
        self.assertEqual([int(row[1:7]) for row in around], [198, 199, 200, 201, 202])
        self.assertTrue(around[2].startswith("▶"))

    def test_the_hook_counts_its_source_and_says_nothing(self):
        hook = {"session_id": "abc12345-x", "source": "compact", "hook_event_name": "SessionStart", "cwd": "/tmp"}
        run = self.gen("hook", stdin=json.dumps(hook))
        self.assertEqual((run.returncode, run.stdout), (0, ""))  # stdout would land in the agent's context
        event = self.ring()[-1]
        self.assertEqual((event["n"], event["event"], event["session"]), (1, "compact", "abc12345-x"))


class Border(unittest.TestCase):
    """Two panes, each bumped from inside itself: each border shows its own latest value at the right."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        tmp = Path(cls.dir.name)
        cls.inner, cls.outer = str(tmp / "inner"), str(tmp / "outer")
        cls.env = {k: v for k, v in isolated({"GEN_DIR": str(tmp / "gen"), "PATH": f"{Path(GEN).parent}:{os.environ['PATH']}",
                                              "TMUX": f"{cls.inner},0,0"}, ("GEN_DIR",)).items() if k != "TMUX"}
        lines = [line.replace("~/.config/tmux/scripts/", f"{HERE}/")
                 for line in CONF.read_text().splitlines() if LINES.match(line)]
        conf = tmp / "gen.conf"
        conf.write_text("\n".join(lines + ['set -g @thm_overlay_1 "#8087a2"', "set -s escape-time 20"]) + "\n")
        cls.tmux(cls.inner, "-f", "/dev/null", "new-session", "-d", "-s", "work", "-n", "two", "-x", "120", "-y", "30",
                 "sh", env=cls.env)
        cls.tmux(cls.inner, "split-window", "-h", "-t", "work:two", "sh")
        cls.tmux(cls.inner, "source-file", str(conf))
        cls.tmux(cls.outer, "-f", "/dev/null", "new-session", "-d", "-s", "terminal", "-x", "120", "-y", "30",
                 f"env -u TMUX tmux -S {cls.inner} attach -t work")

    @classmethod
    def tearDownClass(cls):
        for sock in (cls.outer, cls.inner):
            cls.tmux(sock, "kill-server")
        cls.dir.cleanup()

    @staticmethod
    def tmux(sock, *args, env=None):
        base = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        return subprocess.run(["tmux", "-S", sock, *args], capture_output=True, text=True,
                              env={**base, **(env or {})}).stdout.rstrip("\n")

    def screen(self):
        return self.tmux(self.outer, "capture-pane", "-p", "-t", "terminal:").split("\n")

    def wait_for(self, test, said, seconds=6.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return
            time.sleep(0.05)
        self.fail(said() + "\n" + "\n".join(self.screen()))

    def test_each_border_shows_its_own_panes_last_value(self):
        left, right = self.tmux(self.inner, "list-panes", "-t", "work:two", "-F", "#{pane_id}").split("\n")
        base = int(json.loads(subprocess.run([GEN, "--json"], capture_output=True, text=True,
                                             env={**os.environ, **self.env, "TMUX": f"{self.inner},0,0"}).stdout)["n"])
        for pane, times in ((left, 3), (right, 1)):  # left: base+1..base+3; then right: base+4
            for _ in range(times):
                self.tmux(self.inner, "send-keys", "-t", pane, "gen bump startup >/dev/null", "Enter")
                time.sleep(0.3)
        self.tmux(self.inner, "send-keys", "-t", left, "clear", "Enter")
        self.wait_for(lambda: self.tmux(self.inner, "show-options", "-pqv", "-t", right, "@gen") == str(base + 4),
                      lambda: f"right pane's @gen is not {base + 4}")
        self.assertEqual(self.tmux(self.inner, "show-options", "-pqv", "-t", left, "@gen"), str(base + 3))
        top = self.screen()[0]
        middle = len(top) // 2
        # the top border row: the left pane's number ends its half, the right pane's ends the row
        self.wait_for(lambda: re.search(rf"\b{base + 3}\b", self.screen()[0][:middle]) and self.screen()[0].rstrip("─ ").endswith(str(base + 4)),
                      lambda: f"border row: {self.screen()[0]!r}")


    def test_clicking_the_number_on_the_status_bar_opens_the_ring_around_it(self):
        left = self.tmux(self.inner, "list-panes", "-t", "work:two", "-F", "#{pane_id}").split("\n")[0]
        self.tmux(self.inner, "select-pane", "-t", left)
        self.tmux(self.inner, "send-keys", "-t", left, "gen bump resume >/dev/null; clear", "Enter")
        self.wait_for(lambda: self.tmux(self.inner, "show-options", "-pqv", "-t", left, "@gen"), lambda: "no @gen yet")
        value = self.tmux(self.inner, "show-options", "-pqv", "-t", left, "@gen")
        last = self.screen()[-1]
        at = re.search(rf"(?<![0-9]){value} ", last)
        self.assertIsNotNone(at, f"{value} not on the status line: {last!r}")
        col, row = at.start() + 1, len(self.screen())
        time.sleep(0.35)
        for button, down in ((35, True), (0, True), (0, False)):
            self.tmux(self.outer, "send-keys", "-t", "terminal:", "-l", f"\033[<{button};{col};{row}{'M' if down else 'm'}")
            time.sleep(0.05)
        self.wait_for(lambda: any(line.lstrip("│ ").startswith(f"▶{int(value):>6}") for line in self.screen()),
                      lambda: f"no ring popup marking {value}")
        self.tmux(self.outer, "send-keys", "-t", "terminal:", "q")  # less ends, the popup closes: the throwaway client only
        self.wait_for(lambda: not any("▶" in line for line in self.screen()), lambda: "the popup is still open")
        self.assertEqual(self.tmux(self.inner, "display-message", "-p", "-t", left, "#{pane_in_mode}"), "0")  # no "returned 1" view


if __name__ == "__main__":
    unittest.main(verbosity=2)
