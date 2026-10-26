#!/usr/bin/env python3
"""Pins for prefix+T: the launch step, the presets file, and the launch buffer's preset: field.

    python3 ~/.config/tmux/scripts/handoff-check.py

The seam is the launch buffer (~/.cache/handoff/<stamp>.launch.md). Whatever edits it, nvim today or another
front end later, rewrites it and exits 0 to launch or non-zero to abort; `handoff.py --launch` then reads it.
Launches go to a throwaway tmux server and a stub claude, never the live server; nvim runs headless.
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

import yaml
from functools import cache
from importlib.machinery import SourceFileLoader
from pathlib import Path

sys.dont_write_bytecode = True  # importing handoff.py must not leave __pycache__ in the repo
HERE = Path(__file__).resolve().parent
HANDOFF = HERE / "handoff.py"
LUA = HERE / "handoff-buffer.lua"
NVIM = shutil.which("nvim") or "/opt/nvim-linux64/bin/nvim"
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
    """{name: its section} from handoff-presets.md; each `## name` heading with --- under it starts one."""
    text = PRESETS.read_text()
    return {m[1].strip(): m[2].strip("\n")
            for m in re.finditer(r"^## ([^\n]+)\n(---\n.*?)(?=^## [^\n]+\n---\n|\Z)", text, re.M | re.S)}


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
        """Run handoff.py --launch on buf and return the argv the stub claude or codex was started with;
        self.ran says which of the two it was."""
        out = self.dir / "argv.json"
        for name in ("claude", "codex"):
            stub = self.dir / name
            stub.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                            f"open({str(out)!r}, 'w').write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]))\n")
            stub.chmod(0o755)
        run = subprocess.run([sys.executable, str(HANDOFF), "--launch", str(buf)], capture_output=True, text=True,
                             env={**self.env, "HANDOFF_CLAUDE": str(self.dir / "claude"),
                                  "HANDOFF_CODEX": str(self.dir / "codex")}, timeout=30)
        if run.returncode:
            raise AssertionError(f"handoff.py --launch exited {run.returncode}: {run.stdout}{run.stderr}")
        for _ in range(100):
            if out.exists() and out.stat().st_size:
                self.ran, *argv = json.loads(out.read_text())
                return argv
            time.sleep(0.1)
        raise AssertionError("the stub claude never ran in the new window")


def press(buf, project="/home/lucas/pin"):
    """The default buffer and the values.json beside it that handoff-buffer.lua reads, as a press writes them."""
    codex = handoff.codex_models()
    p = handoff.press(str(project), ["obsidian", "perf"], {**VALUES, "preset": list(handoff.presets([])),
                                                           "model": VALUES["model"] + list(codex), "codex": codex})
    buf.write_text(handoff.render(p, []))
    buf.with_name(buf.name.replace(".launch.md", ".launch.values.json")).write_text(json.dumps(p))


def edit(buf, *commands):
    """Open buf in nvim the way prefix+T does (handoff-buffer.lua, without Lucas's config), run commands, then
    :wq, and :cq if the write was refused. Exit 0 launches, non-zero aborts."""
    return subprocess.run([NVIM, "--headless", "--clean", f"+luafile {LUA}", *(a for c in commands for a in ("-c", c)),
                           "-c", "wq", "-c", "cq", str(buf)], capture_output=True, text=True, timeout=20)


def choose(buf, preset):
    return edit(buf, f"%s/^preset:.*/preset: {preset}/")


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
                self.assertTrue(fields, "no fields read: its frontmatter is missing")
                self.assertLessEqual(set(fields), set(defaults), "a field the launch buffer does not have")
                self.assertEqual(handoff.problems({**defaults, **fields}, live_values()), [])


class Preset(unittest.TestCase):
    """The preset: field, as nvim with handoff-buffer.lua fills it in. Retargeted from the Picker class,
    which pinned a handoff-pick.py --choose that buffer v2 dropped; same four checks, auto in place of
    defaults. The screen itself is checked by hand."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="handoff-preset-"))
        self.buf = self.tmp / "pin.launch.md"
        press(self.buf)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_leaves_the_buffer_alone(self):
        before = self.buf.read_text()
        self.assertEqual(choose(self.buf, "auto").returncode, 0)
        self.assertEqual(self.buf.read_text(), before)

    def test_unknown_preset_aborts_and_changes_nothing(self):
        before = self.buf.read_text()
        self.assertNotEqual(choose(self.buf, "no-such-preset").returncode, 0)
        self.assertEqual(self.buf.read_text(), before)

    def test_each_preset_changes_only_what_it_sets(self):
        defaults, _ = handoff.parse_buffer(default_buffer())
        for name, section in presets().items():
            with self.subTest(preset=name):
                press(self.buf)
                run = choose(self.buf, name)
                self.assertEqual(run.returncode, 0, run.stderr)
                want, want_note = handoff.parse_buffer(section)
                got, got_note = handoff.parse_buffer(self.buf.read_text())
                self.assertEqual(handoff.problems(got, live_values()), [])
                for key, default in defaults.items():
                    self.assertEqual(got.get(key), {**want, "preset": name}.get(key, default), key)
                self.assertEqual(got_note, want_note)

    def test_red_team_reaches_claude(self):
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer(default_buffer(str(project)), project)
            press(buf, project)
            run = choose(buf, "red-team")
            self.assertEqual(run.returncode, 0, run.stderr)
            argv = s.launch(buf)
        self.assertEqual(argv[:6], ["--model", "fable", "--effort", "xhigh", "-n", "redteam"])
        self.assertTrue(argv[-1].startswith("Red-team the previous session"), argv[-1][:80])
        self.assertNotIn("Load these skills", argv[-1])


class Buffer(unittest.TestCase):
    """What launch reads from the buffer: the fields in its YAML frontmatter, the prompt in the markdown after it."""

    def test_frontmatter_comments_drop_and_body_headings_stay(self):
        fields, prompt = handoff.parse_buffer("---\nmodel: opus\nothers: off\n\n# detected skills: perf\n\n# help\n---\n\n"
                                              "do this\n## Task\nthen that\n")
        self.assertEqual(prompt, "do this\n## Task\nthen that")
        self.assertEqual(fields, {"model": "opus", "others": "off"})  # a bare off stays text

    def test_a_heading_in_the_prompt_reaches_claude(self):
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer("", project)
            press(buf, project)
            run = edit(buf, "call setline('$', '## Task')", "call append('$', 'fix the badge')")
            self.assertEqual(run.returncode, 0, run.stderr)
            argv = s.launch(buf)
        self.assertTrue(argv[-1].startswith("## Task\nfix the badge\n\nThe previous session's"), argv[-1])

    def test_yaml_reads_the_frontmatter_as_parse_buffer_does(self):
        """PyYAML's default loader on files both writers produced: handoff.py's, then handoff-buffer.lua's
        after it filled in red-team and none (skills: "")."""
        for value in [*VALUES["model"], *VALUES["effort"], *VALUES["others"], "keep", "close", "", "auto", "none",
                      "red-team", "obsidian perf", "pin-2", "5.5", "2026-10-25"]:
            self.assertEqual(yaml.safe_load(handoff.row("k", value)), {"k": value}, handoff.row("k", value))
        with tempfile.TemporaryDirectory() as tmp:
            buf = Path(tmp) / "pin.launch.md"
            for preset in (None, "red-team", "none"):
                with self.subTest(preset=preset):
                    press(buf)
                    if preset:
                        self.assertEqual(choose(buf, preset).returncode, 0)
                    text = buf.read_text()
                    _, frontmatter, _ = text.split("---\n", 2)
                    self.assertEqual(yaml.safe_load(frontmatter), handoff.parse_buffer(text)[0])

    def test_a_slash_name_loads_its_skill_and_the_written_line_is_not_read(self):
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer("", project)
            press(buf, project)
            text = buf.read_text().replace("detected skills: obsidian perf", "detected skills: debug", 1)
            buf.write_text(text + "take a /screenshot of /mnt/c/x\n")
            argv = s.launch(buf)
        self.assertTrue(argv[-1].startswith("take a /screenshot of /mnt/c/x\n\nThe previous session's"), argv[-1])
        self.assertTrue(argv[-1].endswith(" Load these skills: screenshot, obsidian, perf."), argv[-1])

    def test_a_prompt_starting_with_a_slash_never_reaches_claude_first(self):
        """claude runs a leading /name as a slash command (`claude -p "/usage"` did), so the context goes first."""
        typed = "/share this, then take a /screenshot"
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer("", project)
            press(buf, project)
            run = edit(buf, f"call setline('$', {typed!r})")
            self.assertEqual(run.returncode, 0, run.stderr)
            loaded = [n for n in ("share", "screenshot") if n in VALUES["skills"]] + ["obsidian", "perf"]  # share is one now
            self.assertIn(f"detected skills: {' '.join(loaded)}\n", buf.read_text())
            argv = s.launch(buf)
        self.assertIn("screenshot", loaded)
        self.assertFalse(argv[-1].startswith("/"), argv[-1])
        self.assertEqual(argv[-1], f"The previous session's transcript is {TRANSCRIPT} (bracketed, context only)."
                                   f" Load these skills: {', '.join(loaded)}.\n\n{typed}")


class Codex(unittest.TestCase):
    """model: a Codex model from the models skill starts codex instead, with the same prompt."""

    def test_astra_starts_codex_with_the_prompt(self):
        self.assertEqual(handoff.codex_models().get("astra"), "gpt-6-astra", "the models skill's Codex table")
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer("", project)
            press(buf, project)
            run = edit(buf, "%s/^model:.*/model:  astra/", "%s/^effort:.*/effort: max/", "call setline('$', 'review the badge')")
            self.assertEqual(run.returncode, 0, run.stderr)
            argv = s.launch(buf)
        self.assertEqual(s.ran, "codex")
        self.assertEqual(argv, ["-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',  # codex has no max
                                f"review the badge\n\nThe previous session's transcript is {TRANSCRIPT} (bracketed,"
                                " context only). Load these skills: obsidian, perf."])


if __name__ == "__main__":
    unittest.main(verbosity=2)
