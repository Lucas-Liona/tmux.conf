#!/usr/bin/env python3
"""prefix+T: hand the pane's conversation to a fresh claude in a new window of the same session.

    handoff.py <pane_id> <pane_pid> <pane_current_path> <session_id> <client>
    handoff.py --launch <stamp-sid.launch.md>  what the popup runs after :wq
    handoff.py --export <transcript.jsonl>     print the export only (for checking)

The pane's Claude is found the way agent-send finds its socket: the nearest `claude` process below the
pane's shell, whose ~/.claude/sessions/<pid>.json names the session id and cwd. Its transcript is written
out in full to ~/.cache/handoff/, wrapped in <previous-session> so the new agent reads it as context, not
as instructions. A pane with no Claude hands over its scrollback instead.

Then a popup opens nvim on a launch buffer, laid out like git's commit buffer:

    the new session's first prompt; /name anywhere in it loads that skill
    ━━━━━━━━
    model:  opus        every field, in handoff.toml's order; the ones it does not show are in the
    preset: auto        file too, hidden by handoff-buffer.lua, so the file holds every value

    detected skills: …  the prompt's /names and the preset's skills; drawn, never read
    ━━━━━━━━

    # lines starting with # are dropped: what each field accepts and means

A preset (handoff-presets.md, laid out the same way) fills in the fields it sets; handoff-buffer.lua does
that as you type, with completion and checks. :wq launches claude in a new window with those settings and
the prompt (empty is fine: context only); :cq is the only way out without launching.
"""
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from collections import Counter
from datetime import datetime
from importlib.machinery import SourceFileLoader
from pathlib import Path

HOME = Path.home()
OUT = HOME / ".cache/handoff"
SKILLS = HOME / ".claude/skills"
CONFIG = Path(__file__).parent.parent / "handoff.toml"
PRESETS = Path(__file__).parent.parent / "handoff-presets.md"
BAR = "━" * 40  # handoff-buffer.lua draws it across the window; any line of ━ alone is a bar
# A /name: after a space, a bracket or a quote, so "and/or" is not one. A known skill only, and not when
# a / follows (/mnt/c is a path). handoff-buffer.lua matches the same way.
SKILL_NAME = re.compile(r"(?<![^\s(\"'])/([A-Za-z0-9_][A-Za-z0-9_.:-]*)")
# panel.is_injected drops every line starting with "<"; these are Lucas's own: a paste, and a `!` command.
LUCAS_TAGS = ("<pasted_content", "<bash-input", "<bash-stdout")
# skillOverrides' states have no code source to read; they are the table at
# https://code.claude.com/docs/en/skills#skilloverrides (checked 2026-10-25).
OVERRIDE_STATES = ["name-only", "on", "user-invocable-only", "off"]
# Codex's model_reasoning_effort has no max: minimal low medium high xhigh, per
# https://developers.openai.com/codex/config-sample (checked 2026-10-25).
CODEX_EFFORT = {"max": "xhigh"}
MODELS_SKILL = SKILLS / "models/SKILL.md"
BUFFER_LUA = Path(__file__).with_name("handoff-buffer.lua")
# panel.py already knows which "user" lines are Lucas and which are injected reminders; reuse that, unedited.
_loader = SourceFileLoader("panel", str(HOME / ".local/bin/panel.py"))
panel = sys.modules["panel"] = importlib.util.module_from_spec(importlib.util.spec_from_loader("panel", _loader))
_loader.exec_module(panel)  # registered first: its dataclasses look their module up in sys.modules
_loader = SourceFileLoader("skill_badge", str(Path(__file__).with_name("skill-badge")))  # for description()
badge = importlib.util.module_from_spec(importlib.util.spec_from_loader("skill_badge", _loader))
_loader.exec_module(badge)


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout.strip()


def parent_of(pid):
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return int(st[st.rindex(")") + 2:].split()[1])  # comm can hold spaces and parens; ppid follows the last ")"


def depth_below(pid, ancestor):
    """How many hops up from pid to ancestor, or None if ancestor is not above it."""
    for depth in range(64):
        if pid == ancestor:
            return depth
        if not pid or pid <= 1:
            return None
        pid = parent_of(pid)
    return None


def pane_claude(pane_pid):
    """The session json of the Claude nearest below the pane's shell, or None. The nearest wins: a
    `claude -p` that session started sits further down and has its own session file."""
    best = None
    for f in (HOME / ".claude/sessions").glob("*.json"):
        if not f.stem.isdigit():
            continue
        depth = depth_below(int(f.stem), pane_pid)
        if depth is not None and (best is None or depth < best[0]):
            best = (depth, f)
    if not best:
        return None
    try:
        return json.loads(best[1].read_text())
    except (OSError, ValueError):
        return None


def tool_line(block):
    inp = block.get("input") or {}
    name = block.get("name", "?")
    hint = (inp.get("description") or inp.get("file_path") or inp.get("command") or inp.get("pattern")
            or inp.get("query") or inp.get("prompt") or inp.get("url") or "")
    hint = " ".join(str(hint).split())
    return f"- tool {name}: {hint[:160]}" if hint else f"- tool {name}"


def export(path):
    """Every prompt and reply in full, compaction summaries, and one line per tool call, in order."""
    out = []
    for d in panel.records(path, ('"type":"user"', '"type":"assistant"')):
        content = d.get("message", {}).get("content")
        if d.get("type") == "assistant":
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append(f"## Claude\n\n{block['text'].strip()}\n")
                elif block.get("type") == "tool_use":
                    out.append(tool_line(block))
            continue
        text = panel.text_of(content)
        if d.get("isMeta") or not text.strip():
            continue
        if d.get("isCompactSummary") or panel.COMPACTION_MARKER in text[:300]:
            out.append(f"## Compaction summary\n\n{text.strip()}\n")
        elif not panel.is_injected(text) or text.lstrip().startswith(LUCAS_TAGS):
            out.append(f"## Lucas\n\n{text.strip()}\n")
    return "\n".join(out)


def wrap(body, sid, cwd):
    stamp = datetime.now().astimezone().isoformat(timespec="minutes")
    body = body.replace("</previous-session>", "<\\/previous-session>")  # the bracket must close only once
    return f'<previous-session id="{sid}" cwd="{cwd}" exported="{stamp}">\n\n{body}\n\n</previous-session>\n'


def skills_loaded(path):
    """Skill names in the order of their Skill-tool calls in one transcript."""
    names = []
    for d in panel.records(path, ('"name":"Skill"',)):
        for block in d.get("message", {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
                names.append((block.get("input") or {}).get("skill", ""))
    return [n for n in names if n]


def skill_defaults(cwd, old_transcript):
    """(skills for the new session, others seen here): the top two by loads in this directory's
    transcripts plus whatever the old session loaded, then the next three."""
    project = HOME / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)  # how Claude Code names the dir
    counts = Counter(n for f in project.glob("*.jsonl") for n in skills_loaded(f))
    ranked = [n for n, _ in counts.most_common()]
    chosen = list(dict.fromkeys(ranked[:2] + (skills_loaded(old_transcript) if old_transcript else [])))
    return chosen, [n for n in ranked if n not in chosen][:3]


def personal_skills():
    """What skillOverrides can reach: ~/.claude/skills/<name>/SKILL.md. Plugin skills it cannot."""
    return sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))


def skill_docs():
    """{name: [description, SKILL.md]} for completion and gf: ~/.claude/skills, then each installed
    plugin's skills under plugin:skill, the name a prompt loads them by."""
    found = {p.parent.name: p for p in sorted(SKILLS.glob("*/SKILL.md"))}
    try:
        installed = json.loads((HOME / ".claude/plugins/installed_plugins.json").read_text())["plugins"]
    except (OSError, ValueError, KeyError):
        installed = {}
    for key, installs in installed.items():
        for install in installs:
            for p in sorted(Path(install.get("installPath", "")).glob("skills/*/SKILL.md")):
                found[f"{key.split('@')[0]}:{p.parent.name}"] = p
    return {name: [badge.description(p), str(p)] for name, p in found.items()}


def presets(auto):
    """{name: (fields, prompt)}: auto (the press's usual skills) and none, then handoff-presets.md's,
    each laid out like a launch buffer."""
    found = {"auto": ({"skills": " ".join(auto)}, ""), "none": ({"skills": ""}, "")}
    try:
        text = PRESETS.read_text()
    except OSError:
        return found
    for m in re.finditer(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        found[m[1].strip()] = parse_buffer(m[2])
    return found


def allowed_values():
    """What each field accepts, read fresh on every press: effort levels and model aliases from
    `claude --help` and the models skill (Codex's too), skills from ~/.claude/skills. An empty list means
    "could not read it", and that line is then not checked."""
    try:
        help_text = subprocess.run([claude_bin(), "--help"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, TypeError, subprocess.TimeoutExpired):
        help_text = ""
    effort = re.search(r"--effort <level>.*?\(([^)]*)\)", help_text, re.S)
    model_help = re.search(r"--model <model>(.*?)\n  -", help_text, re.S)
    models = re.findall(r"'([\w.-]+)'", model_help.group(1)) if model_help else []
    try:  # the skill's Claude table: | `haiku` | `claude-haiku-5-5` | ...
        models += [n for row in re.findall(r"^\| `([\w-]+)` \| `(claude-[\w-]+)`", MODELS_SKILL.read_text(), re.M)
                   for n in row]
    except OSError:
        pass
    codex = codex_models()
    return {
        "model": list(dict.fromkeys(models + list(codex))),
        "codex": codex,
        "effort": [e.strip() for e in effort.group(1).split(",")] if effort else [],
        "skills": personal_skills(),
        "others": OVERRIDE_STATES,
        "old": ["keep", "close"],
        "preset": list(presets([])),
    }


def codex_models():
    """{name: id} from the models skill's Codex table, | Astra | `gpt-6-astra` | ...: astra and gpt-6-astra
    both name gpt-6-astra. A model here, or any gpt-... id, starts codex instead of claude."""
    try:
        rows = re.findall(r"^\| ([^|`]+?) \| `(gpt-[\w.-]+)` \|", MODELS_SKILL.read_text(), re.M)
    except OSError:
        return {}
    return {name: model for label, model in rows for name in (label.split()[-1].lower(), model)}


def problems(fields, values):
    """Field values that would make a bad launch; the Lua side checks the same rules as you type."""
    bad = []
    for key in ("model", "effort", "preset", "others", "old"):
        value, allowed = fields.get(key, ""), values.get(key) or []
        full_model_id = key == "model" and value.startswith(("claude-", "gpt-"))
        if allowed and value not in allowed and not full_model_id:
            bad.append(f"{key}: {value or '(empty)'} is not one of {' '.join(allowed)}")
    return bad


def press(cwd, chosen, values):
    """What one press builds the buffer from, written beside it for handoff-buffer.lua: the allowed
    values, handoff.toml's fields and defaults, the presets, and each skill's description and path."""
    cfg = tomllib.loads(CONFIG.read_text())
    defaults = {key: value.replace("{dir}", Path(cwd).name) for key, value in cfg["defaults"].items()}
    defaults["skills"] = " ".join(chosen)  # the auto preset's
    return {**values, "config": cfg, "defaults": defaults, "order": list(dict.fromkeys([*cfg["show"], *defaults])),
            "presets": {name: {"fields": f, "prompt": p} for name, (f, p) in presets(chosen).items()},
            "skill_docs": skill_docs()}


def row(key, value):
    return f"{key + ':':<8}{value}".rstrip()  # handoff-buffer.lua writes fields the same way


def detected_line(skills):
    return "detected skills: " + (" ".join(skills) or "(none)")


def comment_block(p, seen):
    """The # lines under the second bar: handoff.toml's words, then each field with its values."""
    cfg, show = p["config"], p["config"]["show"]
    short_models = [m for m in p["model"] if not m.startswith(("claude-", "gpt-"))]
    accepts = {"model": " ".join(short_models) or "could not read claude --help"}
    lines = cfg["text"]["top"].strip("\n").replace("{seen}", " ".join(seen) or "none").split("\n") + [""]
    for key in show:
        lines.append(f"{key:<8} " + cfg["help"].get(key, "").replace(
            "{values}", accepts.get(key) or " ".join(p.get(key) or []) or "could not read claude --help"))
        if key == "preset":
            width = max(map(len, p["presets"]))
            for name, preset in p["presets"].items():
                sets = [f"{k} {v or '(none)'}" for k, v in preset["fields"].items()]
                sets += ["a first prompt"] if preset["prompt"] else []
                lines.append(f"{'':<11}{name:<{width}}  {' · '.join(sets)}")
    hidden = [key for key in p["order"] if key not in show]
    if hidden:
        lines += ["", cfg["text"]["hidden"]]
        lines += [f"{key:<8} {p['defaults'].get(key) or '(none)'} · " + cfg["help"].get(key, "").replace(
            "{values}", " ".join(p.get(key) or [])) for key in hidden]
    return ["# " + line if line else "#" for line in lines]


def render(p, seen):
    """The buffer as it opens: an empty prompt, a bar, every field (the hidden ones too), the detected
    skills a blank line below them, a bar, and the # block a blank line below that. Opening it in nvim and
    writing it changes nothing."""
    d = p["defaults"]
    return "\n".join([
        "",
        BAR,
        *(row(key, d.get(key, "")) for key in p["order"]),
        "",
        detected_line(detected("", d.get("skills", ""), set(p["skill_docs"]))),
        BAR,
        "",
        *comment_block(p, seen),
    ]) + "\n"


def launch_buffer(cwd, chosen, seen, values):
    return render(press(cwd, chosen, values), seen)


def main(pane_id, pane_pid, pane_path, session_id, client):
    info = pane_claude(int(pane_pid))
    transcript = None
    if info and info.get("sessionId"):
        sid, cwd = info["sessionId"], info.get("cwd") or pane_path
        transcript = next((HOME / ".claude/projects").glob(f"*/{sid}.jsonl"), None)
    if transcript:
        body = export(transcript)
    else:
        sid, cwd = f"pane{pane_id.lstrip('%')}", pane_path
        body = "## Pane scrollback (no Claude session found)\n\n" + tmux("capture-pane", "-p", "-J", "-S", "-", "-t", pane_id)

    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"{datetime.now():%Y-%m-%d-%H%M}-{sid[:8]}"
    exported = stem.with_suffix(".md")
    exported.write_text(wrap(body, sid, cwd))

    if os.path.realpath(cwd) != os.path.realpath(pane_path):
        tmux("display-message", "-c", client, "-d", "8000",
             f"handoff: session cwd {cwd} differs from pane {pane_path}; using the pane's")

    chosen, seen = skill_defaults(pane_path, transcript)
    p = press(pane_path, chosen, allowed_values())
    buffer = stem.with_suffix(".launch.md")
    buffer.write_text(render(p, seen))
    stem.with_suffix(".launch.values.json").write_text(json.dumps(p))  # read by handoff-buffer.lua
    stem.with_suffix(".launch.json").write_text(json.dumps({
        "dir": pane_path, "session": session_id, "client": client, "transcript": str(exported),
        "old_pid": info.get("pid") if transcript else None}))
    # nvim lives in /opt, which only the interactive shell puts on PATH; the popup runs with the server's.
    nvim = shutil.which("nvim") or "/opt/nvim-linux64/bin/nvim"
    edit = [nvim, f"+luafile {BUFFER_LUA}", "+startinsert", str(buffer)]
    aborted = ["tmux", "display-message", "-c", client, "-d", "8000", "handoff: :cq, nothing launched"]
    run = (f"if {shlex.join(edit)}; then {shlex.join([sys.executable, __file__, '--launch', str(buffer)])}; "
           f"else {shlex.join(aborted)}; fi")
    # -EE: the popup closes only on success, so a failed launch or a traceback stays readable.
    tmux("display-popup", "-c", client, "-w", "80%", "-h", "60%", "-d", pane_path,
         "-T", " handoff: :wq launches, :cq aborts ", "-EE", run)


def parse_buffer(text):
    """(fields, prompt). Lines starting with # are dropped first, as in git's commit buffer. The prompt is
    the text above the first bar; the fields are the `key: value` lines from there to the second bar, with a
    one-word key, so "detected skills:" is not one. That line is never read: launch() works the skills out
    again from the prompt and skills:. Nothing after the second bar is read either."""
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    bars = [i for i, line in enumerate(lines) if line and set(line) == {"━"}] + [len(lines)] * 2
    fields = {}
    for line in lines[bars[0] + 1:bars[1]]:
        key, sep, value = line.partition(":")
        if sep and key.isalnum():  # handoff-buffer.lua: ^%w+:
            fields[key] = value.strip()
    return fields, "\n".join(lines[:bars[0]]).strip()


def detected(prompt, skills, known):
    """The skills a launch loads: each /name in the prompt that is a known skill, then skills: (the
    preset's). handoff-buffer.lua draws the same list as you type."""
    named = [m[1].rstrip(".:-") for m in SKILL_NAME.finditer(prompt) if prompt[m.end():m.end() + 1] != "/"]
    return list(dict.fromkeys([n for n in named if n in known] + [s for s in re.split(r"[,\s]+", skills) if s]))


def newest(name):
    """claude and codex come from nvm, which only the interactive shell puts on PATH: take the newest node's."""
    return shutil.which(name) or max(map(str, HOME.glob(f".nvm/versions/node/*/bin/{name}")), default=None,
                                     key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("/node/")[1])[:3]])


def claude_bin():
    return os.environ.get("HANDOFF_CLAUDE") or newest("claude")  # HANDOFF_CLAUDE, HANDOFF_CODEX: stubs, for checking


def codex_bin():
    return os.environ.get("HANDOFF_CODEX") or newest("codex")


def launch(buffer):
    buffer = Path(buffer)
    ctx = json.loads(buffer.with_name(buffer.name.replace(".launch.md", ".launch.json")).read_text())
    fields, note = parse_buffer(buffer.read_text())
    values = json.loads(buffer.with_name(buffer.name.replace(".launch.md", ".launch.values.json")).read_text())
    # the buffer refuses to write these; this catches :q on an unedited bad buffer
    bad = ["no ━ bar under the prompt, so no fields"] if not fields else []
    fields = {**values.get("defaults", {}), **fields}  # a field line deleted by hand keeps its default
    bad = bad or problems(fields, values)
    if bad:
        return tmux("display-message", "-c", ctx["client"], "-d", "8000", f"handoff: not launched; {'; '.join(bad)}")

    chosen = detected(note, fields.get("skills", ""), {*values.get("skills", []), *values.get("skill_docs", {})})
    overrides = {} if fields["others"] == "on" else {
        s: fields["others"] for s in personal_skills() if s not in chosen}
    # An empty note is fine: the new session gets the transcript as context and waits.
    context = f"The previous session's transcript is {ctx['transcript']} (bracketed, context only)."
    if chosen:
        context += f" Load these skills: {', '.join(chosen)}."
    # A prompt starting with / would run as a slash command (`claude -p "/usage"` does), so the context goes first.
    prompt = (f"{context}\n\n{note}" if note.startswith("/") else f"{note}\n\n{context}") if note else context
    codex = values.get("codex", {})
    if fields["model"] in codex or fields["model"].startswith("gpt-"):
        # Codex takes no name and no skillOverrides; it reads the same skills through ~/.agents/skills.
        effort = CODEX_EFFORT.get(fields["effort"], fields["effort"])
        argv = [codex_bin(), "-m", codex.get(fields["model"], fields["model"]),
                "-c", f'model_reasoning_effort="{effort}"', prompt]
    else:
        argv = [claude_bin(), "--model", fields["model"], "--effort", fields["effort"]]
        if fields.get("name"):
            argv += ["-n", fields["name"]]
        if overrides:
            argv += ["--settings", json.dumps({"skillOverrides": overrides})]
        argv.append(prompt)

    # claude or codex runs as the window's command, not typed into a shell; a shell takes over when it exits
    # so the window stays. Its bin dir goes on PATH for what it starts itself (npx MCP servers, node).
    env_path = f"{Path(argv[0]).parent}:{os.environ.get('PATH', '')}"
    made = subprocess.run(["tmux", "new-window", "-P", "-F", "#{pane_id}", "-t", ctx["session"], "-c", ctx["dir"],
                           "-e", f"PATH={env_path}", "zsh", "-c", '"$0" "$@"; exec zsh', *argv],
                          capture_output=True, text=True)
    if made.returncode:  # printed too: the popup (-EE) stays open on a non-zero exit
        why = made.stderr.strip() or f"tmux new-window exited {made.returncode}"
        print(f"handoff: not launched; {why}")
        tmux("display-message", "-c", ctx["client"], "-d", "8000", f"handoff: not launched; {why}")
        sys.exit(1)

    if chosen:  # the pane's skill badge (scripts/skill-badge); best effort, the window is already up
        try:
            subprocess.run([str(Path(__file__).with_name("skill-badge")), "set", made.stdout.strip(), *chosen],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    if fields["old"] == "close" and ctx.get("old_pid"):
        close_old(ctx["old_pid"], ctx["client"])


def close_old(pid, client):
    """End the old Claude only if it is idle by its own session file, read again right before."""
    time.sleep(2)  # the new window is up once new-window returns; give claude a moment to start in it
    try:
        status = json.loads((HOME / f".claude/sessions/{pid}.json").read_text()).get("status")
    except (OSError, ValueError):
        status = None
    if status != "idle":
        return tmux("display-message", "-c", client, "-d", "8000", f"handoff: old session {pid} is {status or 'gone'}, left running")
    os.kill(pid, signal.SIGTERM)
    tmux("display-message", "-c", client, f"handoff: closed old session {pid}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--export"]:
        p = Path(sys.argv[2])
        print(wrap(export(p), p.stem, "?"))
    elif sys.argv[1:2] == ["--launch"]:
        launch(sys.argv[2])
    else:
        main(*sys.argv[1:6])
