#!/usr/bin/env python3
"""Pins for sixel through tmux: tmux.conf marks Lucas's terminal (WezTerm, TERM xterm-256color) as able to
show sixel, so tmux passes pictures to it and tells programs so.

    python3 ~/.config/tmux/scripts/sixel-check.py

Built with scripts/scene: a throwaway server with tmux.conf's terminal-features lines, its client nested
with TERM=xterm-256color. A program in the pane asks DA1 (ESC [ c), as image tools do; the 4 in the reply's
list is "sixel graphics" (xterm ctlseqs, DA1 attribute 4).
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
LINES = re.compile(r"^set -[ga]*s terminal-features ")
ASK_DA1 = ("import os,select,termios,tty;fd=0;old=termios.tcgetattr(fd);tty.setraw(fd);os.write(1,b'\\x1b[c');r=b''\n"
           "while select.select([fd],[],[],2)[0]:\n r+=os.read(fd,64)\n if r.endswith(b'c'):break\n"
           "termios.tcsetattr(fd,termios.TCSADRAIN,old);open('{out}','w').write(r.decode(errors='replace'))")


class Sixel(unittest.TestCase):
    def setUp(self):
        self.scene = s = scene.Scene()
        self.addCleanup(s.close)
        s.start("work", "w", "sh", size=(100, 20), lines=LINES, client_term="xterm-256color")
        time.sleep(0.5)

    def test_the_client_has_sixel_and_tmux_tells_programs(self):
        features = self.scene.tmux("list-clients", "-F", "#{client_termname} #{client_termfeatures}")
        self.assertTrue(features.startswith("xterm-256color "), features)
        self.assertIn("sixel", features.split(" ", 1)[1].split(","))
        out = self.scene.dir / "da1"
        script = self.scene.dir / "da1.py"
        script.write_text(ASK_DA1.replace("{out}", str(out)))
        self.scene.tmux("send-keys", "-t", "work:w", f"python3 {script}", "Enter")
        end = time.monotonic() + 8
        while time.monotonic() < end and not (out.exists() and out.read_text()):
            time.sleep(0.1)
        reply = out.read_text() if out.exists() else ""
        attributes = re.fullmatch(r"\x1b\[\?([0-9;]*)c", reply)
        self.assertIsNotNone(attributes, repr(reply))
        self.assertIn("4", attributes.group(1).split(";"), repr(reply))


if __name__ == "__main__":
    unittest.main(verbosity=2)
