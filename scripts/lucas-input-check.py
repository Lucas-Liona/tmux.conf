#!/usr/bin/env python3
"""Pins for scripts/lucas-input-job: status-left's #() job that sets @lucas_state from Lucas's last input.

    python3 ~/.config/tmux/scripts/lucas-input-check.py

Built with scripts/scene (TEST=1, its own -L server, temp state). `state` is a stub that writes down each
call and sleeps, so nothing reads Windows and no real state.json is written; the gate is 2 s, not 30.
"""
import importlib.util
import re
import sys
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
_loader = SourceFileLoader("scene", str(HERE / "scene"))
scene = importlib.util.module_from_spec(importlib.util.spec_from_loader("scene", _loader))
_loader.exec_module(scene)
LINES = re.compile(r"^set -g (status-left|status-interval) ")


class Job(unittest.TestCase):
    def setUp(self):
        self.scene = s = scene.Scene()
        self.addCleanup(s.close)
        self.calls = s.dir / "state-calls.log"
        stub = s.dir / "state"
        stub.write_text(f'#!/bin/sh\necho "$(date +%s.%N) $*" >> {self.calls}\nsleep 3\n')  # slow, like powershell
        stub.chmod(0o755)
        s.env.update({"STATE": str(stub), "LUCAS_INPUT_EVERY": "2"})
        s.start("work", "w", "cat", size=(100, 20), lines=LINES, extra=["set -g status-interval 1"])

    def calls_so_far(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_the_bar_runs_state_lucas_input_at_most_once_per_gate_and_never_waits_for_it(self):
        end = time.monotonic() + 7
        drawn = []
        while time.monotonic() < end:
            drawn.append(self.scene.screen().split("\n")[-1])
            time.sleep(0.2)
        calls = self.calls_so_far()
        self.assertTrue(calls, "the job never ran state")
        self.assertTrue(all(line.split(" ", 1)[1] == "lucas --input" for line in calls), calls)
        times = [float(line.split()[0]) for line in calls]
        self.assertLessEqual(len(calls), 4, calls)  # 7 s with a 2 s gate, though tmux redraws every second
        # the gate counts whole seconds (date +%s): a 2 s gate opens after 1 to 2 real seconds, 30 s after 29 to 30
        self.assertTrue(all(b - a >= 1.0 for a, b in zip(times, times[1:])), times)
        # while the 3 s stub ran, the status line kept drawing (the window list), never "not ready"
        first = next(i for i, row in enumerate(drawn) if row.strip())
        self.assertTrue(all("0:w" in row for row in drawn[first:]), [row for row in drawn[first:] if "0:w" not in row][:3])
        self.assertFalse(any("not ready" in row for row in drawn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
