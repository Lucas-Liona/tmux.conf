#!/usr/bin/env python3
"""prefix+T: hand the pane's conversation to a fresh claude in a new window of the same session.

    handoff.py <pane_id> <pane_pid> <pane_current_path> <session_id> <client>
    handoff.py --launch <stamp-sid.launch.md>  what the popup runs after :wq
    handoff.py --export <transcript.jsonl>     print the export only (for checking)

The pane's Claude is found the way agent-send finds its socket: the nearest `claude` process below the
pane's shell, whose ~/.claude/sessions/<pid>.json names the session id and cwd. Its transcript is written
out in full to ~/.cache/handoff/, wrapped in <previous-session> so the new agent reads it as context, not
as instructions. A pane with no Claude hands over its scrollback instead.

Then a popup opens nvim on a launch buffer: model, effort, skills and a note for the new session, with
completion and checks from handoff-buffer.lua. :wq launches claude in a new window with those settings and
the note (empty is fine: context only) as its first prompt; :cq is the only way out without launching.
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
from collections import Counter
from datetime import datetime
from importlib.machinery import SourceFileLoader
from pathlib import Path

HOME = Path.home()
OUT = HOME / ".cache/handoff"
SKILLS = HOME / ".claude/skills"
# panel.is_injected drops every line starting with "<"; these are Lucas's own: a paste, and a `!` command.
LUCAS_TAGS = ("<pasted_content", "<bash-input", "<bash-stdout")
# skillOverrides' states have no code source to read; they are the table at
# https://code.claude.com/docs/en/skills#skilloverrides (checked 2026-10-25).
OVERRIDE_STATES = ["name-only", "on", "user-invocable-only", "off"]
MODELS_SKILL = SKILLS / "models/SKILL.md"
BUFFER_LUA = Path(__file__).with_name("handoff-buffer.lua")
# panel.py already knows which "user" lines are Lucas and which are injected reminders; reuse that, unedited.
_loader = SourceFileLoader("panel", str(HOME / ".local/bin/panel.py"))
panel = sys.modules["panel"] = importlib.util.module_from_spec(importlib.util.spec_from_loader("panel", _loader))
_loader.exec_module(panel)  # registered first: its dataclasses look their module up in sys.modules


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


def allowed_values():
    """What each header line accepts, read fresh on every press: effort levels and model aliases from
    `claude --help` and the models skill, skills from ~/.claude/skills. An empty list means "could not
    read it", and that line is then not checked."""
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
    return {
        "model": list(dict.fromkeys(models)),
        "effort": [e.strip() for e in effort.group(1).split(",")] if effort else [],
        "skills": personal_skills(),
        "others": OVERRIDE_STATES,
        "old": ["keep", "close"],
    }


def problems(fields, values):
    """Header values that would make a bad launch; the Lua side checks the same rules as you type."""
    bad = []
    for key in ("model", "effort", "others", "old"):
        value, allowed = fields.get(key, ""), values.get(key) or []
        full_model_id = key == "model" and value.startswith("claude-")
        if allowed and value not in allowed and not full_model_id:
            bad.append(f"{key}: {value or '(empty)'} is not one of {' '.join(allowed)}")
    return bad


def launch_buffer(cwd, chosen, seen, values):
    def row(key, value, comment):
        return f"{key + ':':<8}{value:<23} # {comment}"

    def default(key, preferred):
        options = values[key]
        return preferred if preferred in options or not options else options[0]
    short_models = [m for m in values["model"] if not m.startswith("claude-")]
    return "\n".join([
        row("model", default("model", "opus"), " ".join(short_models) or "could not read claude --help"),
        row("effort", default("effort", "xhigh"), " ".join(values["effort"]) or "could not read claude --help"),
        row("skills", ", ".join(chosen), "seen here: " + (", ".join(seen) or "none")),
        row("others", default("others", "name-only"), " ".join(values["others"])),
        row("old", "keep", " ".join(values["old"])),
        row("name", f"{Path(cwd).name}-2", "shown in the prompt box and /resume"),
        "---",
        "",
        "",
    ])


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

    values = allowed_values()
    buffer = stem.with_suffix(".launch.md")
    buffer.write_text(launch_buffer(pane_path, *skill_defaults(pane_path, transcript), values))
    stem.with_suffix(".launch.values.json").write_text(json.dumps(values))  # read by handoff-buffer.lua
    stem.with_suffix(".launch.json").write_text(json.dumps({
        "dir": pane_path, "session": session_id, "client": client, "transcript": str(exported),
        "old_pid": info.get("pid") if transcript else None}))
    # nvim lives in /opt, which only the interactive shell puts on PATH; the popup runs with the server's.
    nvim = shutil.which("nvim") or "/opt/nvim-linux64/bin/nvim"
    edit = [nvim, "+$", f"+luafile {BUFFER_LUA}", "+startinsert", str(buffer)]
    aborted = ["tmux", "display-message", "-c", client, "-d", "8000", "handoff: :cq, nothing launched"]
    run = (f"if {shlex.join(edit)}; then {shlex.join([sys.executable, __file__, '--launch', str(buffer)])}; "
           f"else {shlex.join(aborted)}; fi")
    tmux("display-popup", "-c", client, "-w", "80%", "-h", "60%", "-d", pane_path,
         "-T", " handoff: :wq launches, :cq aborts ", "-E", run)


def parse_buffer(text):
    head, _, note = text.partition("\n---\n")
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.split("#", 1)[0].strip()
    return fields, note.strip()


def claude_bin():
    """claude comes from nvm, which only the interactive shell puts on PATH: take the newest node's."""
    found = shutil.which("claude") or max(map(str, HOME.glob(".nvm/versions/node/*/bin/claude")), default=None,
                                          key=lambda p: [int(x) for x in re.findall(r"\d+", p.split("/node/")[1])[:3]])
    return os.environ.get("HANDOFF_CLAUDE") or found  # HANDOFF_CLAUDE: a stub, for checking


def launch(buffer):
    buffer = Path(buffer)
    ctx = json.loads(buffer.with_name(buffer.name.replace(".launch.md", ".launch.json")).read_text())
    fields, note = parse_buffer(buffer.read_text())
    values = json.loads(buffer.with_name(buffer.name.replace(".launch.md", ".launch.values.json")).read_text())
    bad = problems(fields, values)
    if bad:  # the buffer refuses to write these; this catches :q on an unedited bad buffer
        return tmux("display-message", "-c", ctx["client"], "-d", "8000", f"handoff: not launched; {'; '.join(bad)}")

    chosen = [s for s in re.split(r"[,\s]+", fields.get("skills", "")) if s]
    overrides = {} if fields["others"] == "on" else {
        s: fields["others"] for s in personal_skills() if s not in chosen}
    # An empty note is fine: the new session gets the transcript as context and waits.
    context = f"The previous session's transcript is {ctx['transcript']} (bracketed, context only)."
    prompt = f"{note}\n\n{context}" if note else context
    if chosen:
        prompt += f" Load these skills: {', '.join(chosen)}."
    claude = claude_bin()
    argv = [claude, "--model", fields["model"], "--effort", fields["effort"]]
    if fields.get("name"):
        argv += ["-n", fields["name"]]
    if overrides:
        argv += ["--settings", json.dumps({"skillOverrides": overrides})]
    argv.append(prompt)

    # claude runs as the window's command, not typed into a shell; a shell takes over when it exits so the
    # window stays. Its bin dir goes on PATH for what claude starts itself (npx MCP servers, node).
    env_path = f"{Path(claude).parent}:{os.environ.get('PATH', '')}"
    tmux("new-window", "-t", ctx["session"], "-c", ctx["dir"], "-e", f"PATH={env_path}",
         "zsh", "-c", '"$0" "$@"; exec zsh', *argv)

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
