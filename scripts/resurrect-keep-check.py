#!/usr/bin/env python3
"""Pins for resurrect-keep-contents (Lucas, 2026-11-20: "do both, add the tmux save hook too", after a `clear` in
taps lost its scrollback and resurrect's one pane_contents.tar.gz had already been overwritten by the next save).

    python3 ~/.config/tmux/scripts/resurrect-keep-check.py

Temp resurrect dirs only (RESURRECT_DIR); never the live one, never a tmux server.
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEEP = HERE / "resurrect-keep-contents"
NOW = datetime(2026, 11, 20, 12, 0, 0)


def archive(path, panes):
    """A pane_contents.tar.gz shaped like resurrect's: ./pane_contents/pane-<target> files."""
    with tarfile.open(path, "w:gz") as tar:
        for target, text in panes.items():
            data = text.encode()
            info = tarfile.TarInfo(f"./pane_contents/pane-{target}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def stamp(when):
    return f"pane_contents-{when:%Y%m%dT%H%M%S}.tar.gz"


class Keep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.kept = self.dir / "kept"

    def tearDown(self):
        self.tmp.cleanup()

    def run_keep(self, *args, now=NOW):
        env = dict(os.environ, RESURRECT_DIR=str(self.dir), KEEP_NOW=now.strftime("%Y-%m-%dT%H:%M:%S"))
        return subprocess.run([sys.executable, str(KEEP), *args], env=env, capture_output=True, text=True)

    def test_save_copies_the_archive_under_its_time_and_leaves_resurrects(self):
        archive(self.dir / "pane_contents.tar.gz", {"taps:1.1": "history\n"})
        r = self.run_keep("save")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.kept / stamp(NOW)).exists())
        self.assertTrue((self.dir / "pane_contents.tar.gz").exists())

    def test_two_saves_keep_two_copies(self):
        archive(self.dir / "pane_contents.tar.gz", {"taps:1.1": "one\n"})
        self.run_keep("save", now=NOW - timedelta(minutes=5))
        archive(self.dir / "pane_contents.tar.gz", {"taps:1.1": "two\n"})
        self.run_keep("save")
        self.assertEqual(len(list(self.kept.glob("*.tar.gz"))), 2)

    def test_no_archive_is_not_an_error(self):
        r = self.run_keep("save")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.kept.exists() and any(self.kept.iterdir()))

    def test_prune_keeps_2h_all_then_hourly_to_48h_then_daily_to_14d(self):
        self.kept.mkdir()
        ages = [
            timedelta(minutes=10), timedelta(minutes=15),            # under 2 h: both kept
            timedelta(hours=5, minutes=10), timedelta(hours=5, minutes=40),  # same hour: newest kept
            timedelta(days=3, hours=1), timedelta(days=3, hours=6),  # same day: newest kept
            timedelta(days=20),                                      # past 14 days: dropped
        ]
        for age in ages:
            archive(self.kept / stamp(NOW - age), {"p:1.1": "x\n"})
        archive(self.dir / "pane_contents.tar.gz", {"p:1.1": "now\n"})
        self.run_keep("save")
        left = sorted(p.name for p in self.kept.glob("*.tar.gz"))
        # 06:50 and 06:20 share an hour: 06:50 stays. 11-17 11:00 and 06:00 share a day: 11:00 stays.
        want = [stamp(NOW), stamp(NOW - ages[0]), stamp(NOW - ages[1]), stamp(NOW - ages[2]), stamp(NOW - ages[4])]
        self.assertEqual(left, sorted(want))

    def test_show_prints_a_panes_history_from_the_newest_copy_at_or_before(self):
        self.kept.mkdir()
        archive(self.kept / stamp(NOW - timedelta(hours=1)), {"taps:1.1": "before the clear\n"})
        archive(self.kept / stamp(NOW), {"taps:1.1": "after\n"})
        r = self.run_keep("show", "taps:1.1", "--at", (NOW - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("before the clear", r.stdout)
        self.assertNotIn("after", r.stdout)

    def test_show_unknown_pane_exits_1_and_names_it(self):
        self.kept.mkdir()
        archive(self.kept / stamp(NOW), {"taps:1.1": "x\n"})
        r = self.run_keep("show", "nope:9.9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nope:9.9", r.stderr)

    def test_list_names_each_copy_with_its_time(self):
        self.kept.mkdir()
        archive(self.kept / stamp(NOW), {"taps:1.1": "x\n"})
        r = self.run_keep("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2026-11-20 12:00", r.stdout)

    def test_tmux_conf_runs_the_stripper_then_the_keeper_after_each_save(self):
        conf = (HERE.parent / "tmux.conf").read_text()
        line = next(l for l in conf.splitlines() if l.startswith("set -g @resurrect-hook-post-save-all"))
        self.assertLess(line.index("resurrect-strip-sidebars.py contents"), line.index("resurrect-keep-contents save"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
