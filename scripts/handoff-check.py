#!/usr/bin/env python3
"""Pins for prefix+T: the launch step, the presets file, the launch buffer's preset: field, and --branch.

    python3 ~/.config/tmux/scripts/handoff-check.py

The seam is the launch buffer (~/.cache/handoff/<stamp>.launch.md). Whatever edits it, nvim today or another
front end later, writes it and exits 0 to launch; leaving it unwritten, or exiting non-zero, aborts.
`handoff.py --launch` then reads it.
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
# A stand-in for `codex debug models`: its shape as codex-cli 0.162.1 printed it on 2026-10-25, cut down.
CATALOG = {"models": [
    {"slug": "gpt-6-astra", "description": "Frontier intelligence for the most demanding work.", "visibility": "list",
     "priority": 2, "supported_reasoning_levels": [
         {"effort": e, "description": d} for e, d in [("low", "Fast responses with lighter reasoning"),
                                                     ("high", "Greater reasoning depth for complex problems"),
                                                     ("xhigh", "Extra high reasoning depth for complex problems"),
                                                     ("ultra", "Maximum reasoning with automatic task delegation")]]},
    {"slug": "gpt-6-luna", "description": "Fast and affordable model for easier tasks.", "visibility": "list",
     "priority": 4, "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh", "max")]},
    {"slug": "codex-auto-review", "description": "Automatic approval review model for Codex.", "visibility": "hide",
     "priority": 9, "supported_reasoning_levels": [{"effort": "medium"}]},
]}


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
        # -c: its pane sits in a directory no Claude ever ran in, so a branch from it finds no usual skills
        subprocess.run(["tmux", "-S", self.sock, "-f", "/dev/null", "new-session", "-d", "-s", "pin", "-c", str(self.dir)],
                       check=True)
        self.env = {**os.environ, "TMUX": f"{self.sock},0,0", "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("HANDOFF_BRANCH", None)  # these pins also run from inside a branch
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

    def launch(self, buf, then=""):
        """Run handoff.py --launch on buf and return the argv the stub claude or codex was started with;
        self.ran says which of the two it was. then is what the stub claude does after recording that:
        BOX draws an input box, STAY keeps it in front of the window's shell."""
        run = subprocess.run([sys.executable, str(HANDOFF), "--launch", str(buf)], capture_output=True, text=True,
                             env=self.stubs(then), timeout=30)
        if run.returncode:
            raise AssertionError(f"handoff.py --launch exited {run.returncode}: {run.stdout}{run.stderr}")
        return self.started()

    def stubs(self, then=""):
        """Write a stub claude and codex and return the environment that makes handoff.py start them. Each
        answers what a press asks it (claude --help, codex debug models) and records any other start."""
        out = self.dir / "argv.json"
        out.unlink(missing_ok=True)
        for name in ("claude", "codex"):
            stub = self.dir / name
            stub.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                            f"if sys.argv[1:] == ['--help']:\n    sys.exit(print({HELP!r}))\n"
                            f"if sys.argv[1:] == ['debug', 'models']:\n    sys.exit(print({json.dumps(CATALOG)!r}))\n"
                            f"open({str(out)!r}, 'w').write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]))\n"
                            + (then.replace("TYPED", repr(str(self.dir / "typed"))) if name == "claude" else ""))
            stub.chmod(0o755)
        return {**self.env, "HANDOFF_CLAUDE": str(self.dir / "claude"), "HANDOFF_CODEX": str(self.dir / "codex")}

    def started(self):
        """The argv the stub in the new window was started with; self.ran says whether it was claude or codex."""
        out = self.dir / "argv.json"
        for _ in range(100):
            if out.exists() and out.stat().st_size:
                self.ran, *argv = json.loads(out.read_text())
                return argv
            time.sleep(0.1)
        raise AssertionError("the stub claude never ran in the new window")

    def branch(self, text, **env):
        """Run handoff.py --branch on a file holding text, as an agent in the server's first pane would.
        Its files go to self.dir / "out", not ~/.cache/handoff. Returns the finished process."""
        asked = self.dir / "asked.md"
        asked.write_text(text)
        pane = self.tmux("list-panes", "-t", "pin:0", "-F", "#{pane_id}")
        return subprocess.run([sys.executable, str(HANDOFF), "--branch", str(asked)], capture_output=True, text=True,
                              env={**self.stubs(), "TMUX_PANE": pane, "HANDOFF_OUT": str(self.dir / "out"), **env},
                              timeout=30)

    def pressed(self):
        """What prefix+T runs, from the server's first pane, and the launch buffer it leaves in self.dir /
        "out". No client is attached, so the popup does not open: the pin edits the buffer in its place."""
        pane = self.tmux("list-panes", "-t", "pin:0", "-F", "#{pane_id} #{pane_pid} #{pane_current_path}").split(" ", 2)
        run = subprocess.run([sys.executable, str(HANDOFF), *pane, self.session, ""], capture_output=True, text=True,
                             env={**self.stubs(), "HANDOFF_OUT": str(self.dir / "out")}, timeout=30)
        if run.returncode:
            raise AssertionError(f"handoff.py (the press) exited {run.returncode}: {run.stdout}{run.stderr}")
        return next((self.dir / "out").glob("*.launch.md"))

    def tmux(self, *args):
        return subprocess.run(["tmux", "-S", self.sock, *args], capture_output=True, text=True).stdout.strip()

    def windows(self):
        """["<window id> <1 if it is the one in view>", ...] of the pin session."""
        return self.tmux("list-windows", "-t", "pin", "-F", "#{window_id} #{window_active}").splitlines()


# The two parts of `claude --help` a press reads: the efforts, and the model aliases.
HELP = """  --effort <level>   Effort level for the current session (low, medium, high, xhigh, max)
  --model <model>    Model for the current session: an alias ('fable', 'opus', 'sonnet', 'haiku') or a full name
  -n, --name <name>  Set a display name for this session"""

# The rest of a stub claude that stays in front, the terminal left as the shell had it, so a Ctrl+C is a
# SIGINT to everything in the pane's foreground process group. claude is in that state while it exits.
STAY = """import time
time.sleep(30)
"""

# The rest of a stub claude that draws claude's input box, a ❯ line between ─ rules, and records what it is sent.
BOX = """import time, tty
time.sleep(0.5)
tty.setraw(0)
typed = b""
while True:
    rule = "\\u2500" * 40
    sys.stdout.write("\\x1b[2J\\x1b[H" + rule + "\\r\\n\\u276f\\xa0" + typed.decode().replace("\\n", "\\r\\n  ") + "\\r\\n" + rule + "\\r\\n")
    sys.stdout.flush()
    typed += os.read(0, 4096)
    open(TYPED, "wb").write(typed)
"""


def stub_values(catalog=CATALOG):
    """allowed_values() as a press reads it, with a stub codex printing catalog (a dict, or raw text)."""
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "codex"
        text = json.dumps(catalog) if isinstance(catalog, dict) else catalog
        stub.write_text(f"#!/usr/bin/env python3\nprint({text!r})\n")
        stub.chmod(0o755)
        saved, os.environ["HANDOFF_CODEX"] = os.environ.get("HANDOFF_CODEX"), str(stub)
        try:
            return handoff.allowed_values()
        finally:
            os.environ.pop("HANDOFF_CODEX") if saved is None else os.environ.update(HANDOFF_CODEX=saved)


def press(buf, project="/home/lucas/pin", values=None):
    """The default buffer and the values.json beside it that handoff-buffer.lua reads, as a press writes them."""
    p = handoff.press(str(project), ["obsidian", "perf"], values or stub_values())
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
            typed = buf.with_name("pin.launch.prompt.md").read_text()  # red-team sets send: no: typed, not passed
        self.assertEqual(argv[:6], ["--model", "fable", "--effort", "xhigh", "-n", "redteam"])
        self.assertTrue(typed.startswith("Red-team the previous session"), typed[:80])
        self.assertNotIn("Load these skills", typed)
        self.assertFalse(any(a.startswith("Red-team") for a in argv), "the prompt is not in claude's argv")


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
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer("", project)
            press(buf, project)
            run = edit(buf, "%s/^model:.*/model:  astra/", "%s/^effort:.*/effort: ultra/", "call setline('$', 'review the badge')")
            self.assertEqual(run.returncode, 0, run.stderr)
            argv = s.launch(buf)
        self.assertEqual(s.ran, "codex")
        self.assertEqual(argv, ["-m", "gpt-6-astra", "-c", 'model_reasoning_effort="ultra"',  # astra's own level
                                f"review the badge\n\nThe previous session's transcript is {TRANSCRIPT} (bracketed,"
                                " context only). Load these skills: obsidian, perf."])


class Send(unittest.TestCase):
    """send: yes passes the prompt to claude (LaunchToday); no types it into claude's box and leaves it unsent."""

    def test_no_types_the_prompt_into_the_box_without_enter(self):
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            buf = s.buffer("", project)
            press(buf, project)
            run = edit(buf, "%s/^send:.*/send:   no/", "call setline('$', 'two lines')", "call append('$', 'of prompt')")
            self.assertEqual(run.returncode, 0, run.stderr)
            argv = s.launch(buf, then=BOX)
            want = (f"two lines\nof prompt\n\nThe previous session's transcript is {TRANSCRIPT} (bracketed, context only)."
                    " Load these skills: obsidian, perf.").encode()
            typed = s.dir / "typed"
            for _ in range(100):  # the typer waits for the box, then types
                if typed.exists() and typed.read_bytes() == want:
                    break
                time.sleep(0.1)
            self.assertEqual(typed.read_bytes() if typed.exists() else b"", want)
        self.assertNotIn(b"\r", want)  # no Enter: Ctrl+J between lines is \n
        self.assertFalse(argv[-1].startswith("two lines"), "the prompt is not in claude's argv")

    def test_no_with_a_codex_model_is_refused(self):
        v = stub_values()
        fields = {"model": "astra", "effort": "high", "preset": "auto", "others": "name-only", "old": "keep", "send": "no"}
        self.assertEqual(handoff.problems(fields, v), [handoff.NO_SEND_CODEX])
        self.assertEqual(handoff.problems({**fields, "send": "yes"}, v), [])
        with tempfile.TemporaryDirectory() as tmp:  # and the buffer refuses it as you type
            buf = Path(tmp) / "pin.launch.md"
            press(buf, values=v)
            run = edit(buf, "%s/^model:.*/model:  astra/", "%s/^send:.*/send:   no/")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn(handoff.NO_SEND_CODEX, run.stderr)


class Catalog(unittest.TestCase):
    """Models and efforts from the installed CLIs: claude --help, and `codex debug models` (a stub here)."""

    def test_effort_follows_the_model(self):
        v = stub_values()
        self.assertEqual(v["models"]["astra"]["id"], "gpt-6-astra")  # the last word of the id names it
        self.assertIn(["ultra", "Maximum reasoning with automatic task delegation"], v["models"]["astra"]["efforts"])
        self.assertNotIn("ultra", handoff.efforts_for("luna", v))
        self.assertEqual(handoff.efforts_for("opus", v), v["effort"])
        self.assertNotIn("codex-auto-review", v["model"], "a hidden model is not offered")
        base = {"preset": "auto", "others": "name-only", "old": "keep"}
        self.assertEqual(handoff.problems({**base, "model": "astra", "effort": "ultra"}, v), [])
        self.assertEqual(handoff.problems({**base, "model": "luna", "effort": "ultra"}, v),
                         ["effort: ultra is not one of low medium high xhigh max (what luna supports)"])

    def test_an_effort_the_model_lacks_blocks_wq(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = Path(tmp) / "pin.launch.md"
            press(buf)
            before = buf.read_text()
            run = edit(buf, "%s/^model:.*/model:  luna/", "%s/^effort:.*/effort: ultra/")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("effort: ultra is not one of low medium high xhigh max (what luna supports)", run.stderr)
            self.assertEqual(buf.read_text(), before)

    def test_a_broken_catalog_falls_back_and_says_so(self):
        v = stub_values("codex debug models: unknown subcommand")
        self.assertTrue(v["codex_warning"].startswith("codex debug models failed"), v["codex_warning"])
        self.assertEqual(v["models"]["astra"]["id"], "gpt-6-astra", "the models skill's Codex table")
        self.assertEqual(handoff.efforts_for("astra", v), [], "efforts unchecked")
        with tempfile.TemporaryDirectory() as tmp:
            buf = Path(tmp) / "pin.launch.md"
            press(buf, values=v)
            self.assertIn("# ⚠ codex debug models failed", buf.read_text())
            fields, _ = handoff.parse_buffer(buf.read_text())
            self.assertEqual(handoff.problems(fields, v), [], "a claude launch does not wait on codex")


class Export(unittest.TestCase):
    """The exported transcript shows each Bash call's command, under its description."""

    def test_bash_calls_show_their_command(self):
        commands = ["agent-claim list", "cat <<'EOF' > notes.md\n```\nEOF", "\n".join(f"echo {i}" for i in range(30))]
        calls = [{"name": "Bash", "input": {"command": c, "description": "Show claims"}} for c in commands]
        calls.append({"name": "Read", "input": {"file_path": "/home/lucas/x.md"}})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.jsonl"
            path.write_text("".join(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", **call}]}},
                                               separators=(",", ":")) + "\n" for call in calls))  # Claude's own spacing
            md = handoff.export(path)
        self.assertIn("- tool Bash: Show claims\n```bash\nagent-claim list\n```\n", md)
        self.assertIn("````bash\ncat <<'EOF' > notes.md\n```\nEOF\n````", md)  # a longer fence than the command's
        self.assertIn("echo 19\n# … 10 more lines in the transcript\n```", md)
        self.assertNotIn("echo 20", md)
        self.assertIn("- tool Read: /home/lucas/x.md", md)  # other tools keep their one line


class Quit(unittest.TestCase):
    """Only a written buffer launches. nvim exits 0 on :q and :q! as it does on :wq, so the exit code cannot say."""

    def leave(self, buf, how):
        return subprocess.run([NVIM, "--headless", "--clean", f"+luafile {LUA}", "-c", how, str(buf)],
                              capture_output=True, text=True, timeout=20)

    def test_q_bang_launches_nothing(self):
        for how in ("q!", "q"):
            with self.subTest(how=how), Server() as s:
                buf, before = s.pressed(), s.windows()
                self.assertEqual(self.leave(buf, how).returncode, 0)  # which is why the popup went on to --launch
                run = subprocess.run([sys.executable, str(HANDOFF), "--launch", str(buf)], capture_output=True, text=True,
                                     env=s.stubs(), timeout=30)
                time.sleep(0.5)  # a window would be up by now
                self.assertEqual(s.windows(), before)
                self.assertFalse((s.dir / "argv.json").exists(), "claude was started")
                self.assertIn("quit without :wq, nothing launched", run.stdout)

    def test_wq_on_an_untouched_buffer_still_launches(self):
        """An empty prompt is a launch too (the transcript as context only), so the content cannot say either."""
        with Server() as s:
            buf = s.pressed()
            before = buf.read_text()
            self.assertEqual(self.leave(buf, "wq").returncode, 0)
            self.assertEqual(buf.read_text(), before)
            argv = s.launch(buf)
        self.assertIn("The previous session's transcript is", argv[-1])


class Window(unittest.TestCase):
    """The new window outlives its claude, whatever ends it."""

    def test_a_ctrl_c_that_reaches_the_wrapper_leaves_a_shell(self):
        """Lucas's Ctrl+C spam on 2026-11-10: the third one arrived after claude had handed the terminal back,
        killed the zsh -c that was to become the window's shell, and the window closed with it."""
        with Server() as s:
            project = s.dir / "pin"
            project.mkdir()
            s.launch(s.buffer(default_buffer(str(project)), project), then=STAY)
            pane, pid = s.tmux("list-panes", "-t", "pin:1", "-F", "#{pane_id} #{pane_pid}").split()
            s.tmux("send-keys", "-t", pane, "C-c")
            # Until nothing names the stub: it is gone, and the wrapper (it is in zsh -c's arguments) has died
            # or become the shell. Not pane_current_command: that says zsh the whole time (tmux-wsl).
            for _ in range(100):
                if subprocess.run(["pgrep", "-f", str(s.dir / "claude")], capture_output=True).returncode:
                    break
                time.sleep(0.05)
            alive = pane in s.tmux("list-panes", "-a", "-F", "#{pane_id}").split()
            shell = Path(f"/proc/{pid}/cmdline").read_bytes() if alive else b""
        self.assertTrue(alive, "the window closed with its claude")
        self.assertEqual(shell, b"zsh\0", "the pane's process is the shell the wrapper was to become")


class Branch(unittest.TestCase):
    """handoff.py --branch: an agent hands over its own pane, with a file where the popup would be."""

    ASKED = "---\nmodel: sonnet\neffort: low\nname: progress\n---\n\nbuild the progress view\n"

    def test_a_file_opens_a_window_behind_the_one_in_view(self):
        with Server() as s:
            before = s.windows()
            run = s.branch(self.ASKED)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            argv, after = s.started(), s.windows()
            out = s.dir / "out"
            stem = next(out.glob("*.launch.md")).name.removesuffix(".launch.md")
            left = sorted(f.name.removeprefix(stem) for f in out.iterdir())
            fields, prompt = handoff.parse_buffer((out / f"{stem}.launch.md").read_text())
            new_pane = run.stdout.split("in pane ")[1].split()[0]
            shell = dict(line.split() for line in s.tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid}").splitlines())
            environ = Path(f"/proc/{shell[new_pane]}/environ").read_bytes().split(b"\0")
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[0], before[0], "the window in view is still the one in view")
        self.assertEqual(argv[:6], ["--model", "sonnet", "--effort", "low", "-n", "progress"])
        self.assertEqual(argv[-1], f"build the progress view\n\nThe previous session's transcript is {out}/{stem}.md"
                                   " (bracketed, context only). You are a branch of pane %0, which is still working on"
                                   " its own task: do only what is asked here. When you finish, write the result to"
                                   f" {out}/{stem}.result.md.")
        self.assertEqual(left, [".launch.json", ".launch.md", ".launch.values.json", ".md"])  # what a press leaves
        self.assertEqual((fields["model"], fields["old"], prompt), ("sonnet", "keep", "build the progress view"))
        self.assertIn(f"result, when the branch is done: {out}/{stem}.result.md", run.stdout)
        self.assertIn(b"HANDOFF_BRANCH=%0", environ, "the new window knows it is a branch")

    def test_a_branch_does_not_branch(self):
        with Server() as s:
            before = s.windows()
            run = s.branch(self.ASKED, HANDOFF_BRANCH="control-b5")
            self.assertEqual(s.windows(), before)
            self.assertFalse((s.dir / "out").exists(), "nothing exported either")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("itself a branch of control-b5", run.stderr)

    def test_a_preset_and_a_codex_model_make_a_red_teamer_from_another_provider(self):
        with Server() as s:
            run = s.branch('---\npreset: red-team\nmodel: astra\neffort: high\nsend: "yes"\n---\n\nstart with the restore numbers\n')
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            argv = s.started()
        self.assertEqual(s.ran, "codex")
        self.assertEqual(argv[:4], ["-m", "gpt-6-astra", "-c", 'model_reasoning_effort="high"'])
        self.assertTrue(argv[-1].startswith("Red-team the previous session"), argv[-1][:80])  # the preset's prompt first
        self.assertIn("and stop.\n\nstart with the restore numbers\n\nThe previous session's transcript is", argv[-1])
        self.assertNotIn("Load these skills", argv[-1])  # red-team sets skills: ""

    def test_what_cannot_launch_is_refused_with_the_reason(self):
        for asked, why in [("---\ndir: /tmp\n---\n\nx\n", "no field named dir"),
                           ("---\nold: close\n---\n\nx\n", "old: close would end the session that is asking"),
                           ("---\neffort: enormous\n---\n\nx\n", "effort: enormous is not one of low medium high xhigh max"),
                           ("---\npreset: no-such\n---\n\nx\n", "preset: no-such is not one of")]:
            with self.subTest(asked=asked), Server() as s:
                before = s.windows()
                run = s.branch(asked)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn(why, run.stdout + run.stderr)
                self.assertEqual(s.windows(), before)

    def test_two_branches_in_one_minute_keep_both_bookmarks(self):
        with Server() as s:
            for _ in range(2):
                self.assertEqual(s.branch(self.ASKED).returncode, 0)
            self.assertEqual(len(list((s.dir / "out").glob("*.launch.md"))), 2)

    def test_a_named_parent_is_told_by_name(self):
        note = handoff.branch_note({"pane": "%522", "name": "control-b5", "result": "/x/r.md"})
        self.assertIn("a branch of the session named control-b5 (pane %522)", note)
        self.assertIn("write the result to /x/r.md and tell control-b5 with SendMessage", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
