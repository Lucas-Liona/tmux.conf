#!/usr/bin/env python3
"""Pins for pane-junk's flags: --help prints the docstring and asks tmux nothing.

    python3 ~/.config/tmux/scripts/pane-junk-check.py

clidocs FINDINGS item 1 (2026-11-21): `pane-junk --help` ran the whole scan and printed pane ids, the input
`pane-tour - --instant` kills. A stub tmux on PATH writes down every call; the live server is never asked.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).with_name("pane-junk.py")


class Help(unittest.TestCase):
    def run_junk(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp, "calls")
            stub = Path(tmp, "tmux")
            stub.write_text(f'#!/bin/sh\necho "$*" >> {calls}\necho "@1|0|%9|1|zsh||s:1.0"\n')
            stub.chmod(0o755)
            env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
            out = subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True, timeout=20)
            return out, calls.read_text() if calls.exists() else ""

    def test_help_prints_the_docstring_and_calls_no_tmux(self):
        for flag in ("--help", "-h"):
            out, calls = self.run_junk(flag)
            self.assertEqual(out.returncode, 0)
            self.assertIn("pane-junk | pane-tour -", out.stdout)
            self.assertNotIn("%9", out.stdout)
            self.assertEqual(calls, "", f"{flag} asked tmux: {calls}")


if __name__ == "__main__":
    unittest.main()
