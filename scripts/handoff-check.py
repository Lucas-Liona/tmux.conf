#!/usr/bin/env python3
"""Pins for prefix+T: the launch step as it works today, and the picker that will replace the nvim buffer.

    python3 ~/.config/tmux/scripts/handoff-check.py

The seam is the launch buffer (~/.cache/handoff/<stamp>.launch.md). Whatever edits it, nvim today or the
picker next, rewrites it and exits 0 to launch or non-zero to abort; `handoff.py --launch` then reads it.
Launches go to a throwaway tmux server and a stub claude, never the live server.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from functools import cache
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True  # importing handoff.py must not leave __pycache__ in the repo
HERE = Path(__file__).resolve().parent
HANDOFF = HERE / "handoff.py"
PICKER = HERE / "handoff-pick.py"
PRESETS = HERE.parent / "handoff-presets.md"
_loader = SourceFileLoader("handoff", str(HANDOFF))
handoff = importlib.util.module_from_spec(importlib.util.spec_from_loader("handoff", _loader))
_loader.exec_module(handoff)

# What allowed_values() returned for the real prefix+T at 04:02 on 2026-10-25 (its .launch.values.json).
VALUES = {
    "model": ["fable", "opus", "sonnet", "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "haiku",
              "claude-haiku-5-5"],
    "effort": ["low", "medium", "high", "xhigh", "max"],
    "skills": sorted(p.parent.name for p in (Path.home() / ".claude/skills").glob("*/SKILL.md")),
    "others": ["name-only", "on", "user-invocable-only", "off"],
    "old": ["keep", "close"],
}
TRANSCRIPT = "/tmp/handoff-check-transcript.md"  # only named in the prompt, never read


@cache
def live_values():
    """What claude accepts today: a preset naming a retired model should fail here, not at launch."""
    return handoff.allowed_values()


def default_buffer(project="/home/lucas/pin"):
    return handoff.launch_buffer(project, ["obsidian", "perf"], [], VALUES)


def presets():
    """{name: its section} from handoff-presets.md; each `## name` heading starts one."""
    text = PRESETS.read_text()
    return {m[1].strip(): m[2].strip("\n") for m in re.finditer(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S)}


class Server:
    """A tmux server nobody else uses. Bare `tmux` calls, like launch()'s, reach it through $TMUX."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-check-"))
        self.sock = str(self.dir / "tmux.sock")
        subprocess.run(["tmux", "-S", self.sock, "-f", "/dev/null", "new-session", "-d", "-s", "pin"], check=True)
        self.env = {**os.environ, "TMUX": f"{self.sock},0,0", "PYTHONDONTWRITEBYTECODE": "1"}
        reached = subprocess.run(["tmux", "list-sessions", "-F", "#{socket_path} #{session_id}"], env=self.env,
                                 capture_output=True, text=True).stdout.split()
        if reached[:1] != [self.sock]:
            self.__exit__()
            raise SystemExit(f"refusing to launch: bare tmux reached {reached}, not the throwaway server")
        self.session = reached[1]
        return self

    def __exit__(self, *exc):
        subprocess.run(["tmux", "-S", self.sock, "kill-server"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def buffer(self, text, project):
        """A launch buffer with the two context files handoff.py --launch reads beside it."""
        buf = self.dir / "pin.launch.md"
        buf.write_text(text)
        (self.dir / "pin.launch.values.json").write_text(json.dumps(VALUES))
        (self.dir / "pin.launch.json").write_text(json.dumps(
            {"dir": str(project), "session": self.session, "client": "", "transcript": TRANSCRIPT, "old_pid": None}))
        return buf

    def launch(self, buf):
        """Run handoff.py --launch on buf and return the argv the stub claude was started with."""
        out = self.dir / "argv.json"
        stub = self.dir / "claude"
        stub.write_text(f"#!/usr/bin/env python3\nimport json, sys\nopen({str(out)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
        stub.chmod(0o755)
        run = subprocess.run([sys.executable, str(HANDOFF), "--launch", str(buf)],
                             env={**self.env, "HANDOFF_CLAUDE": str(stub)}, capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise AssertionError(f"handoff.py --launch exited {run.returncode}: {run.stdout}{run.stderr}")
        for _ in range(100):
            if out.exists() and out.stat().st_size:
                return json.loads(out.read_text())
            time.sleep(0.1)
        raise AssertionError("the stub claude never ran in the new window")


def pick(buf, *args):
    return subprocess.run([sys.executable, str(PICKER), str(buf), *args], cwd=buf.parent,
                          capture_output=True, text=True, timeout=10)


class LaunchToday(unittest.TestCase):
    """handoff.py with the default buffer, as it works now. The expected argv matches what debug-2 was
    started with at 04:05 on 2026-10-25: its first prompt and its name-only skills."""

    def test_default_buffer_passes_its_own_checks(self):
        fields, note = handoff.parse_buffer(default_buffer())
        self.assertEqual(handoff.problems(fields, VALUES), [])
        self.assertEqual(note, "")

    def test_default_buffer_reaches_claude(self):
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            argv = s.launch(s.buffer(default_buffer(str(project)), project))
        self.assertEqual(argv[:6], ["--model", "opus", "--effort", "xhigh", "-n", "pin-2"])
        overrides = json.loads(argv[argv.index("--settings") + 1])["skillOverrides"]
        self.assertEqual(set(overrides), set(VALUES["skills"]) - {"obsidian", "perf"})
        self.assertEqual(set(overrides.values()), {"name-only"})
        self.assertEqual(argv[-1], f"The previous session's transcript is {TRANSCRIPT} (bracketed, context only)."
                                   " Load these skills: obsidian, perf.")


class PresetsFile(unittest.TestCase):
    """handoff-presets.md on its own: every preset names real fields and values claude accepts today."""

    def test_presets_exist(self):
        self.assertIn("red-team", presets())

    def test_each_preset_sets_known_fields_to_launchable_values(self):
        defaults, _ = handoff.parse_buffer(default_buffer())
        for name, section in presets().items():
            with self.subTest(preset=name):
                fields, _ = handoff.parse_buffer(section)
                self.assertLessEqual(set(fields), set(defaults), "a field the launch buffer does not have")
                self.assertEqual(handoff.problems({**defaults, **fields}, live_values()), [])


class Picker(unittest.TestCase):
    """The picker's contract. `handoff-pick.py <buffer> --choose <preset>` applies a preset without showing
    anything, so these run with no terminal; the screen itself is checked by hand. Exit 0 launches,
    non-zero aborts, as with nvim's :wq and :cq."""

    def setUp(self):
        if not PICKER.exists():
            self.fail(f"{PICKER.name} does not exist yet; this is its pin")
        self.tmp = Path(tempfile.mkdtemp(prefix="handoff-pick-"))
        self.buf = self.tmp / "pin.launch.md"
        self.buf.write_text(default_buffer())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_leaves_the_buffer_alone(self):
        before = self.buf.read_text()
        self.assertEqual(pick(self.buf, "--choose", "defaults").returncode, 0)
        self.assertEqual(self.buf.read_text(), before)

    def test_unknown_preset_aborts_and_changes_nothing(self):
        before = self.buf.read_text()
        self.assertNotEqual(pick(self.buf, "--choose", "no-such-preset").returncode, 0)
        self.assertEqual(self.buf.read_text(), before)

    def test_each_preset_changes_only_what_it_sets(self):
        defaults, _ = handoff.parse_buffer(default_buffer())
        for name, section in presets().items():
            with self.subTest(preset=name):
                self.buf.write_text(default_buffer())
                run = pick(self.buf, "--choose", name)
                self.assertEqual(run.returncode, 0, run.stderr)
                want, want_note = handoff.parse_buffer(section)
                got, got_note = handoff.parse_buffer(self.buf.read_text())
                self.assertEqual(handoff.problems(got, live_values()), [])
                for key, default in defaults.items():
                    self.assertEqual(got.get(key), want.get(key, default), key)
                self.assertEqual(got_note, want_note)

    def test_red_team_reaches_claude(self):
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer(default_buffer(str(project)), project)
            run = pick(buf, "--choose", "red-team")
            self.assertEqual(run.returncode, 0, run.stderr)
            argv = s.launch(buf)
        self.assertEqual(argv[:6], ["--model", "fable", "--effort", "xhigh", "-n", "redteam"])
        self.assertTrue(argv[-1].startswith("Red-team the previous session"), argv[-1][:80])
        self.assertNotIn("Load these skills", argv[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
