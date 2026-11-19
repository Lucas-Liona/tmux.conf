#!/usr/bin/env python3
"""Pins for the restore Lucas relies on: close WezTerm entirely, reopen it, and every session is still there
(2026-11-18, he asked "do we have ci to see if that will always happen?").

    python3 ~/.config/tmux/scripts/restore-check.py
    RESTORE_EXTRA='set -g destroy-unattached on' python3 ~/.config/tmux/scripts/restore-check.py   # the red run

A throwaway server (scripts/scene) started with -f a copy of his real tmux.conf, so his options are what is
tested. Taken out of the copy, and counted so a change to tmux.conf is noticed: the @plugin lines, the line
that runs tpm (continuum would auto-save this server into his real resurrect directory) and the run-shell
that repoints his real resurrect "last". Closing WezTerm hangs up the client's terminal: the pin sends the
client SIGHUP and then removes the whole outer terminal, then attaches a new client.
"""
import importlib.util
import os
import re
import signal
import sys
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CONF = HERE.parent / "tmux.conf"
_loader = SourceFileLoader("scene", str(HERE / "scene"))
scene = importlib.util.module_from_spec(importlib.util.spec_from_loader("scene", _loader))
_loader.exec_module(scene)
DROP = [re.compile(r"^set -g @plugin "), re.compile(r"^run '~/\.config/tmux/plugins/tpm/tpm'$"),
        re.compile(r"""^run-shell 'R="\$HOME/\.local/share/tmux/resurrect";""")]
EXPECTED_DROPS = [6, 1, 1]
LAYOUT = "#{session_name}:#{window_index}:#{window_name} #{pane_id} #{pane_pid} #{pane_title}"


def copy_of_his_conf(dest: Path) -> list[int]:
    kept, dropped = [], [0] * len(DROP)
    for line in CONF.read_text().splitlines():
        hit = next((i for i, pattern in enumerate(DROP) if pattern.match(line)), None)
        if hit is None:
            kept.append(line)
        else:
            dropped[hit] += 1
    kept += [line for line in os.environ.get("RESTORE_EXTRA", "").split("\n") if line.strip()]
    dest.write_text("\n".join(kept) + "\n")
    return dropped


class Restore(unittest.TestCase):
    def setUp(self):
        self.scene = s = scene.Scene()
        self.addCleanup(s.close)
        self.conf = s.dir / "tmux.conf"
        self.dropped = copy_of_his_conf(self.conf)
        s.start("main", "lucas2", "sh", size=(153, 39), config=self.conf)
        s.tmux("new-window", "-d", "-t", "main:", "-n", "coord", "sh")
        s.tmux("new-window", "-d", "-t", "main:", "-n", "tmux", "sh")
        # a workmux sidebar: a narrow pane on the left of the first window
        self.sidebar = s.tmux("split-window", "-hb", "-l", "22", "-t", "main:lucas2", "-P", "-F", "#{pane_id}",
                              "sh -c 'while :; do sleep 60; done'")
        s.tmux("select-pane", "-t", self.sidebar, "-T", "workmux-sidebar")  # his conf counts panes from 1: by id
        s.tmux("new-session", "-d", "-s", "homelab", "-n", "homelab", "sh")
        time.sleep(1)

    def layout(self):
        return sorted(self.scene.tmux("list-panes", "-a", "-F", LAYOUT).split("\n"))

    def test_the_copy_is_his_conf_less_only_the_plugin_lines(self):
        self.assertEqual(self.dropped, EXPECTED_DROPS, "tmux.conf's plugin lines changed: look at DROP")
        text = self.conf.read_text()
        self.assertNotIn("plugins/tpm/tpm", text)
        self.assertNotIn(".local/share/tmux/resurrect", text.split("@resurrect-hook")[0].split("@continuum")[0])

    def test_his_options_keep_the_server_and_its_sessions_without_a_client(self):
        s = self.scene
        self.assertEqual(s.tmux("show-options", "-sv", "exit-unattached"), "off")
        self.assertEqual(s.tmux("show-options", "-gv", "destroy-unattached"), "off")
        conf = self.conf.read_text()
        for name in ("exit-unattached", "destroy-unattached"):
            self.assertIsNone(re.search(rf"^\s*set\S* .*\b{name} +on\b", conf, re.M), f"the config sets {name} on")

    def test_closing_the_terminal_keeps_everything_and_a_new_client_gets_it_back(self):
        s = self.scene
        before = self.layout()
        self.assertEqual(len(before), 5, before)  # lucas2 (sidebar + pane), coord, tmux, homelab
        server = s.tmux("display-message", "-p", "#{pid}")
        client = s.tmux("list-clients", "-F", "#{client_pid}")
        self.assertTrue(client, "no client attached")
        os.kill(int(client), signal.SIGHUP)  # WezTerm closing: the terminal hangs up
        s.outer_tmux("kill-server")  # and the whole terminal goes
        end = time.monotonic() + 5
        while time.monotonic() < end and s.tmux("list-clients", "-F", "#{client_pid}"):
            time.sleep(0.1)
        time.sleep(1)
        self.assertTrue(Path(f"/proc/{server}").exists(), "the server went with its last client")
        self.assertEqual(s.tmux("list-clients"), "")
        self.assertEqual(self.layout(), before, "a window, pane or its process changed")
        self.assertTrue(all(Path(f"/proc/{row.split()[2]}").exists() for row in before), "a pane's process died")
        # WezTerm reopening: the launcher's new-session -A -s main attaches to what is there
        attach = f"env -u TMUX TMUX_TMPDIR={s.dir} tmux -L inner new-session -A -s main"
        s.outer_tmux("-f", "/dev/null", "new-session", "-d", "-s", "terminal", "-x", "153", "-y", "39", attach)
        end = time.monotonic() + 5
        while time.monotonic() < end and not s.tmux("list-clients", "-F", "#{client_session}"):
            time.sleep(0.1)
        self.assertEqual(s.tmux("list-clients", "-F", "#{client_session}"), "main")
        self.assertEqual(self.layout(), before, "the new client did not get the same layout")
        self.assertEqual(s.tmux("display-message", "-p", "-t", self.sidebar, "#{pane_title} #{pane_width}"), "workmux-sidebar 22")



class Top(unittest.TestCase):
    """Lucas, 2026-11-18: "when will status move to top?". With his config the bar is the first row, the pane
    border line the third (2026-11-19: the badge's line first, the windows' and the ⌘'s second), and the ⌘ menu
    still opens on screen when clicked."""

    def test_the_bar_is_on_top_above_the_border_and_its_menu_opens(self):
        s = scene.Scene()
        self.addCleanup(s.close)
        conf = s.dir / "tmux.conf"
        copy_of_his_conf(conf)
        s.start("main", "lucas2", "sh", size=(153, 39), config=conf, live_colours=True)
        for n in range(3):  # the bar's needs job recounts from the scene's own needs file
            s.run("needs", "add", f"scene ask {n}", capture_output=True)
        time.sleep(1.5)
        rows = s.screen().split("\n")
        self.assertIn("⌘", rows[1], f"row 1 is not the bar's second line: {rows[1]!r} (last row: {rows[-1]!r})")
        self.assertTrue(rows[2].lstrip().startswith("─"), f"row 2 is not the border: {rows[2]!r}")
        self.assertTrue(rows[0].lstrip().startswith("! 3"), rows[0])  # the badge: on the bar, top left, once
        self.assertNotIn("! 3", rows[1] + rows[2])  # not again below (Lucas, needs 49: "A")
        col = rows[1].index("⌘") + 1
        time.sleep(0.35)
        for button, down in ((35, True), (0, True), (0, False)):
            s.outer_tmux("send-keys", "-t", "terminal:", "-l", f"\033[<{button};{col};2{'M' if down else 'm'}")
            time.sleep(0.05)
        end, menu = time.monotonic() + 6, ""
        while time.monotonic() < end and "panel" not in menu:
            time.sleep(0.1)
            menu = s.screen()
        self.assertIn("panel", menu, "the ⌘ menu did not open from the top bar")
        self.assertIn("aq", menu)  # the menu's rows are on screen, not cut off above the top


if __name__ == "__main__":
    unittest.main(verbosity=2)
