#!/usr/bin/env python3
"""Pins for scripts/scene, the one helper for throwaway tmux scenes (pins and screenshots).

    python3 ~/.config/tmux/scripts/scene-check.py
    SCENE_BIN=<another scene> python3 ~/.config/tmux/scripts/scene-check.py   # e.g. a copy that forgets a path

The caller's own environment is the dangerous one: HOME is a temp dir (so a forgotten path defaults there,
not to a real file), NEEDS_FILE, NEEDS_CACHE, GEN_DIR and TMUX are unset. The live files' times and the live
server's @needs_count are read before and after; nothing here writes them.
"""
import json
import os
import types
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCENE = os.environ.get("SCENE_BIN", str(HERE / "scene"))
# the real home from the user database, not $HOME: this file may itself run under a temp HOME
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
# SCENE_LIVE_HOME=<temp dir>: for a red run with a broken scene. That dir is then the scenes' HOME, the "live"
# home whose files are watched, and the tmux socket dir for the "live" @needs_count, so a scene that falls
# back to its live default writes into a temp dir this pin watches, never into the real ~/.cache/panel.
FAKE_LIVE = os.environ.get("SCENE_LIVE_HOME")
LIVE_HOME = Path(FAKE_LIVE) if FAKE_LIVE else REAL_HOME
LIVE_FILES = [LIVE_HOME / ".cache/panel/needs.jsonl", LIVE_HOME / ".cache/panel/gen.jsonl"]


def stamp(path: Path):
    return path.stat().st_mtime_ns if path.exists() else None


class Scenes(unittest.TestCase):
    def setUp(self):
        self.home = types.SimpleNamespace(name=FAKE_LIVE, cleanup=lambda: None) if FAKE_LIVE else tempfile.TemporaryDirectory()
        self.env = {**{k: v for k, v in os.environ.items()
                       if k not in ("TMUX", "TMUX_PANE", "NEEDS_FILE", "NEEDS_CACHE", "GEN_DIR", "TEST", "TMUX_TMPDIR")},
                    "HOME": self.home.name, "PATH": f"{REAL_HOME / '.local/bin'}:{os.environ['PATH']}",
                    **({"TMUX_TMPDIR": FAKE_LIVE} if FAKE_LIVE else {})}
        self.name = f"pin{os.getpid()}"
        self.addCleanup(self.home.cleanup)
        self.addCleanup(subprocess.run, [SCENE, "down", self.name], capture_output=True, env=self.env)

    def scene(self, *args):
        return subprocess.run([SCENE, *args], capture_output=True, text=True, env=self.env, timeout=60)

    def live(self):
        count = subprocess.run(["tmux", "show-options", "-gqv", "@needs_count"], capture_output=True, text=True,
                               env={**{k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_TMPDIR")},
                                    **({"TMUX_TMPDIR": FAKE_LIVE} if FAKE_LIVE else {})}).stdout
        return [stamp(f) for f in LIVE_FILES], count

    def test_a_screenshot_scene_with_needs_file_unset_writes_nothing_live(self):
        before = self.live()
        up = self.scene("up", self.name)
        self.assertEqual(up.returncode, 0, up.stderr)
        where = Path(up.stdout.strip())
        added = self.scene("run", self.name, "--", "needs", "add", "a scene ask", "--tier", "0")
        bumped = self.scene("run", self.name, "--", "gen", "bump", "startup")
        wrote = self.scene("run", self.name, "--", "needs", "--write")
        self.assertEqual(self.scene("tmux", self.name, "show-options", "-gqv", "@needs_count").returncode, 0)
        png = where / "shot.png"
        self.assertEqual(self.scene("shot", self.name, str(png)).returncode, 0)
        self.assertGreater(png.stat().st_size, 1000)
        self.assertEqual(self.live(), before, "a live file or the live @needs_count changed")
        self.assertFalse((Path(self.home.name) / ".cache").exists(), "a forgotten path fell back to $HOME")
        self.assertEqual([added.returncode, bumped.returncode, wrote.returncode], [0, 0, 0], added.stderr)
        self.assertIn("a scene ask", (where / "needs.jsonl").read_text())
        self.assertEqual(len((where / "gen" / "gen.jsonl").read_text().splitlines()), 1)
        env = json.loads(self.scene("run", self.name, "--", "python3", "-c", "import json,os;print(json.dumps(dict(os.environ)))").stdout)
        self.assertEqual(env["TEST"], "1")
        self.assertTrue(env["TMUX"].startswith(str(where)))

    def test_run_on_a_scene_that_is_not_up_refuses(self):
        run = self.scene("run", "nosuch" + self.name, "--", "true")
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(Path(f"/tmp/scene-nosuch{self.name}").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
