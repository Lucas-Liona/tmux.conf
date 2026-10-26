#!/usr/bin/env python3
"""prefix+T: hand the pane's conversation to a fresh claude in a new window of the same session.

    handoff.py <pane_id> <pane_pid> <pane_current_path> <session_id> <client>
    handoff.py --launch <stamp-sid.launch.md>  what the popup runs after :wq
    handoff.py --export <transcript.jsonl>     print the export only (for checking)
    handoff.py --type <pane> <prompt> <client> <claude>   send: no, in the background: type into claude's box

The pane's Claude is found the way agent-send finds its socket: the nearest `claude` process below the
pane's shell, whose ~/.claude/sessions/<pid>.json names the session id and cwd. Its transcript is written
out in full to ~/.cache/handoff/, wrapped in <previous-session> so the new agent reads it as context, not
as instructions. A pane with no Claude hands over its scrollback instead.

Then a popup opens nvim on a launch buffer: markdown with YAML frontmatter, which any YAML parser reads
the way parse_buffer does.

    ---                   drawn as a full-width bar
    model:  opus          every field, in handoff.toml's order; the ones it does not show are in the
    preset: auto          file too, hidden by handoff-buffer.lua, so the file holds every value

    # detected skills: …  the prompt's /names and the preset's skills; drawn without its #, never read

    # what each field accepts and means: YAML comments, so no parser reads them
    ---

    the new session's first prompt, markdown; /name anywhere in it loads that skill

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

import yaml  # PyYAML, Ubuntu's python3-yaml

HOME = Path.home()
OUT = HOME / ".cache/handoff"
SKILLS = HOME / ".claude/skills"
CONFIG = Path(__file__).parent.parent / "handoff.toml"
PRESETS = Path(__file__).parent.parent / "handoff-presets.md"
# Words YAML 1.1 (PyYAML's default loader) reads as booleans or null: a field holding one is written quoted.
YAML_WORDS = {"y", "yes", "n", "no", "true", "false", "on", "off", "null", "~"}
# A /name: after a space, a bracket or a quote, so "and/or" is not one. A known skill only, and not when
# a / follows (/mnt/c is a path). handoff-buffer.lua matches the same way.
SKILL_NAME = re.compile(r"(?<![^\s(\"'])/([A-Za-z0-9_][A-Za-z0-9_.:-]*)")
# panel.is_injected drops every line starting with "<"; these are Lucas's own: a paste, and a `!` command.
LUCAS_TAGS = ("<pasted_content", "<bash-input", "<bash-stdout")
BASH_LINES = 20  # of a command, in the export; a heredoc that writes a file can run to hundreds
# skillOverrides' states have no code source to read; they are the table at
# https://code.claude.com/docs/en/skills#skilloverrides (checked 2026-10-25).
OVERRIDE_STATES = ["name-only", "on", "user-invocable-only", "off"]
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
    if name == "Bash" and inp.get("command"):
        return bash_call(inp)
    hint = (inp.get("description") or inp.get("file_path") or inp.get("command") or inp.get("pattern")
            or inp.get("query") or inp.get("prompt") or inp.get("url") or "")
    hint = " ".join(str(hint).split())
    return f"- tool {name}: {hint[:160]}" if hint else f"- tool {name}"


def bash_call(inp):
    """The description, then the command as it ran, in a fence longer than any backtick run inside it.
    The fence is not indented under the list item, so a heredoc copied out of the raw file still ends."""
    command = inp["command"].rstrip()
    lines = command.splitlines()
    if len(lines) > BASH_LINES:
        lines = lines[:BASH_LINES] + [f"# … {len(lines) - BASH_LINES} more lines in the transcript"]
    fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", command)), default=0))
    label = " ".join(str(inp.get("description", "")).split())
    return "\n".join([f"- tool Bash: {label}" if label else "- tool Bash", fence + "bash", *lines, fence])


def export(path):
    """Every prompt and reply in full, compaction summaries, one line per tool call and each Bash call's
    command, in order."""
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
    # a preset starts at a `## name` with --- under it, so a ## heading in its prompt stays in the prompt
    for m in re.finditer(r"^## ([^\n]+)\n(---\n.*?)(?=^## [^\n]+\n---\n|\Z)", text, re.M | re.S):
        found[m[1].strip()] = parse_buffer(m[2])
    return found


def allowed_values():
    """What each field accepts, read fresh on every press. Models and efforts come from the installed CLIs
    (see models()), skills from ~/.claude/skills. An empty list means "could not read it", and that field
    is then not checked."""
    try:  # codex answers while claude does: 0.1-0.5 s and 0.2 s measured, together 0.25 s, so no cache
        catalog = subprocess.Popen([codex_bin() or "codex", "debug", "models"], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, env=bin_on_path(codex_bin()))
    except OSError:
        catalog = None
    try:
        help_text = subprocess.run([claude_bin(), "--help"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, TypeError, subprocess.TimeoutExpired):
        help_text = ""
    effort = re.search(r"--effort <level>.*?\(([^)]*)\)", help_text, re.S)
    efforts = [e.strip() for e in effort.group(1).split(",")] if effort else []
    try:
        out = catalog.communicate(timeout=5)[0] if catalog else ""
    except subprocess.TimeoutExpired:
        catalog.kill()
        out = ""
    found, warning = models(help_text, efforts, out)
    return {
        "model": [name for name, m in found.items() if m["listed"]],
        "models": found,
        "codex_warning": warning,
        "effort": efforts,
        "skills": personal_skills(),
        "others": OVERRIDE_STATES,
        "old": ["keep", "close"],
        "send": ["yes", "no"],
        "preset": list(presets([])),
    }


def bin_on_path(binary):
    """The environment with binary's dir first on PATH: codex is a node script, and node comes from nvm too."""
    return {**os.environ, "PATH": f"{Path(binary or '.').parent}:{os.environ.get('PATH', '')}"}


def models(help_text, claude_efforts, catalog_json):
    """({name: model}, warning), every model: value with what it is. model is {provider, id, description,
    listed (offered by completion), efforts: [[effort, description]]}.

    Claude lists its aliases in --help and its efforts there for every model, without descriptions; the
    models skill's table is the only place that pairs alias, id and Anthropic's description. Codex prints
    its catalog with `codex debug models`: per model a description and its own efforts, each described.
    Each listed model also answers to the last word of its id (astra), the first in codex's priority
    order winning (sol is gpt-6.1-sol). That command sits under debug: if it fails or changes shape, the
    models skill's Codex table stands in, efforts unchecked, and the warning says so."""
    named = re.search(r"--model <model>(.*?)\n  -", help_text, re.S)
    claude = {name: "" for name in (re.findall(r"'([\w.-]+)'", named.group(1)) if named else [])}
    try:
        skill = MODELS_SKILL.read_text()
    except OSError:
        skill = ""
    for alias, model, description in re.findall(r"^\| `([\w-]+)` \| `(claude-[\w-]+)` \| ([^|]*) \|", skill, re.M):
        claude[alias] = claude[model] = description.strip()
    described = [[e, "listed by claude --help, which describes none"] for e in claude_efforts]
    found = {name: {"provider": "claude", "id": name, "description": d, "listed": True, "efforts": described}
             for name, d in claude.items()}
    try:
        catalog = [{"id": m["slug"], "description": m.get("description") or "", "listed": m.get("visibility") == "list",
                    "efforts": [[level["effort"], level.get("description") or ""] for level in m["supported_reasoning_levels"]]}
                   for m in sorted(json.loads(catalog_json)["models"], key=lambda m: m.get("priority", 99))]
        warning = "" if catalog else "codex debug models listed no models"
    except (ValueError, KeyError, TypeError, AttributeError):
        catalog, warning = [], "codex debug models failed or changed shape"
    if warning:
        catalog = [{"id": model, "description": f"{label.strip()}, from the models skill", "listed": True, "efforts": []}
                   for label, model in re.findall(r"^\| ([^|`]+?) \| `(gpt-[\w.-]+)` \|", skill, re.M)]
        warning += ": codex models come from the models skill, their efforts unchecked"
    for m in catalog:
        alias = m["id"].rsplit("-", 1)[-1]
        for name in ([alias] if m["listed"] and alias not in found else []) + [m["id"]]:
            found[name] = {"provider": "codex", **m}
    return found, warning


def efforts_for(model, values):
    """What effort: may be with this model; [] means not checked (a codex model the catalog did not describe)."""
    m = values.get("models", {}).get(model)
    if m:
        return [effort for effort, _ in m["efforts"]]
    return values.get("effort", []) if model.startswith("claude-") or "models" not in values else []


def problems(fields, values):
    """Field values that would make a bad launch; the Lua side checks the same rules as you type."""
    bad = [f"{key}: {value!r} is a YAML list or map; write it as one line of text"
           for key, value in fields.items() if not isinstance(value, str)]
    model = fields.get("model", "") if isinstance(fields.get("model", ""), str) else ""
    for key in ("model", "effort", "preset", "others", "old"):
        value, allowed = fields.get(key, ""), values.get(key) or []
        if not isinstance(value, str):
            continue
        if key == "effort":
            allowed = efforts_for(model, values)
        # claude takes any full model name; a gpt- one is codex's to know, unless its catalog could not be read
        known = key == "model" and (value in values.get("models", {}) or value.startswith("claude-")
                                    or value.startswith("gpt-") and ("models" not in values or values.get("codex_warning")))
        if allowed and value not in allowed and not known:
            bad.append(f"{key}: {value or '(empty)'} is not one of {' '.join(allowed)}"
                       + (f" (what {model} supports)" if key == "effort" and model else ""))
    if fields.get("send") == "no" and provider(model, values) == "codex":
        bad.append(NO_SEND_CODEX)
    return bad


# handoff-buffer.lua says the same
NO_SEND_CODEX = "send: no works with claude only: codex shows no input box this can tell is ready"


def provider(model, values):
    known = values.get("models", {}).get(model)
    return known["provider"] if known else "codex" if model.startswith("gpt-") else "claude"


def press(cwd, chosen, values):
    """What one press builds the buffer from, written beside it for handoff-buffer.lua: the allowed
    values, handoff.toml's fields and defaults, the presets, and each skill's description and path."""
    cfg = tomllib.loads(CONFIG.read_text())
    defaults = {key: value.replace("{dir}", Path(cwd).name) for key, value in cfg["defaults"].items()}
    defaults["skills"] = " ".join(chosen)  # the auto preset's
    # "keep: leaves the old session running; close: ..." in the help is what completion shows beside each value
    value_docs = {key: {v: d.strip() for v, d in re.findall(r"([\w-]+): ([^;]+)", text) if v in (values.get(key) or [])}
                  for key, text in cfg["help"].items()}
    return {**values, "config": cfg, "defaults": defaults, "order": list(dict.fromkeys([*cfg["show"], *defaults])),
            "presets": {name: {"fields": f, "prompt": p} for name, (f, p) in presets(chosen).items()},
            "skill_docs": skill_docs(), "value_docs": value_docs}


def yaml_value(value):
    """value as YAML reads it back as that string: plain, or double-quoted where YAML would read a boolean,
    null, number or date, or stop at a # or : in it. handoff-buffer.lua quotes by the same rule."""
    ambiguous = (not value or value.lower() in YAML_WORDS or re.match(r"[-+.\d\[\]{}#&*!|>'\"%@`,?:]", value)
                 or ": " in value or " #" in value)
    return json.dumps(value, ensure_ascii=False) if ambiguous else value


def row(key, value):
    return f"{key + ':':<8}{yaml_value(value)}"  # handoff-buffer.lua writes fields the same way


def detected_line(skills):
    return "# detected skills: " + (" ".join(skills) or "(none)")


def comment_block(p, seen):
    """The # lines at the end of the frontmatter: handoff.toml's words, then each field with its values."""
    cfg, show = p["config"], p["config"]["show"]
    short_models = [m for m in p["model"] if not m.startswith(("claude-", "gpt-"))]
    accepts = {"model": " ".join(short_models) or "could not read claude --help"}
    lines = cfg["text"]["top"].strip("\n").replace("{seen}", " ".join(seen) or "none").split("\n") + [""]
    if p.get("codex_warning"):
        lines += [f"⚠ {p['codex_warning']}", ""]
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
    """The buffer as it opens: the frontmatter (every field, the hidden ones too, then the detected skills
    and the # block, each a blank line apart), a blank line, and an empty prompt. Opening it in nvim and
    writing it changes nothing."""
    d = p["defaults"]
    return "\n".join([
        "---",
        *(row(key, d.get(key, "")) for key in p["order"]),
        "",
        detected_line(detected("", d.get("skills", ""), set(p["skill_docs"]))),
        "",
        *comment_block(p, seen),
        "---",
        "",
        "",
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
    text = render(p, seen)
    buffer.write_text(text)
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
    # Tall enough for the whole frontmatter and some prompt, so the fields are on screen with the cursor
    # below them; list-clients, as display -c reads some other client.
    rows = [int(h) for c, h in (line.rsplit(" ", 1) for line in tmux("list-clients", "-F", "#{client_name} #{client_height}")
                                .splitlines()) if c == client]
    height = str(min(text.count("\n") + 10, rows[0] - 2)) if rows else "90%"
    tmux("display-popup", "-c", client, "-w", "80%", "-h", height, "-d", pane_path,
         "-T", " handoff: :wq launches, :cq aborts ", "-EE", run)


def parse_buffer(text):
    """(fields, prompt): the frontmatter read as YAML, and the markdown after it as the prompt, # headings
    and all. BaseLoader reads every value as text, so a bare off stays "off" and an empty one "", and no
    value can break a launch; the writers quote those, so YAML's default loader reads the same. The
    frontmatter's comments, the help and the detected skills line, are YAML's to drop: launch() works the
    skills out again from the prompt and skills:."""
    m = re.match(r"---\n(.*?)^---$\n?(.*)", text, re.S | re.M)
    try:
        data = yaml.load(m[1], Loader=yaml.BaseLoader) if m else None
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return {}, (m[2] if m else text).strip()
    return data, m[2].strip()


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
    bad = ["no YAML frontmatter (--- lines) above the prompt, so no fields"] if not fields else []
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
    send = fields.get("send") != "no"
    if provider(fields["model"], values) == "codex":
        # Codex takes no name and no skillOverrides; it reads the same skills through ~/.agents/skills.
        model = values.get("models", {}).get(fields["model"], {}).get("id", fields["model"])
        argv = [codex_bin(), "-m", model, "-c", f'model_reasoning_effort="{fields["effort"]}"', prompt]
    else:
        argv = [claude_bin(), "--model", fields["model"], "--effort", fields["effort"]]
        if fields.get("name"):
            argv += ["-n", fields["name"]]
        if overrides:
            argv += ["--settings", json.dumps({"skillOverrides": overrides})]
        if send:
            argv.append(prompt)

    # claude or codex runs as the window's command, not typed into a shell; a shell takes over when it exits
    # so the window stays. Its bin dir goes on PATH for what it starts itself (npx MCP servers, node).
    made = subprocess.run(["tmux", "new-window", "-P", "-F", "#{pane_id}", "-t", ctx["session"], "-c", ctx["dir"],
                           "-e", f"PATH={bin_on_path(argv[0])['PATH']}", "zsh", "-c", '"$0" "$@"; exec zsh', *argv],
                          capture_output=True, text=True)
    if made.returncode:  # printed too: the popup (-EE) stays open on a non-zero exit
        why = made.stderr.strip() or f"tmux new-window exited {made.returncode}"
        print(f"handoff: not launched; {why}")
        tmux("display-message", "-c", ctx["client"], "-d", "8000", f"handoff: not launched; {why}")
        sys.exit(1)

    if not send:  # claude has no flag that fills its box unsent (claude --help 2.1.310, the CLI reference)
        typed = buffer.with_name(buffer.name.replace(".launch.md", ".launch.prompt.md"))
        typed.write_text(prompt)
        subprocess.Popen([sys.executable, __file__, "--type", made.stdout.strip(), str(typed), ctx["client"], argv[0]],
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)  # its own session: it outlives the popup

    if chosen:  # the pane's skill badge (scripts/skill-badge); best effort, the window is already up
        try:
            subprocess.run([str(Path(__file__).with_name("skill-badge")), "set", made.stdout.strip(), *chosen],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    if fields["old"] == "close" and ctx.get("old_pid"):
        close_old(ctx["old_pid"], ctx["client"])


def running_in(pane, binary):
    """Whether binary still runs in pane, or None once the pane is gone. Not pane_current_command: under the
    window's zsh -c it says zsh while claude runs (tmux-wsl). A process below the pane's shell instead, with
    binary in its command line (a script's is its second word)."""
    rows = (line.split(" ", 1) for line in tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid}").splitlines())
    shell = next((int(pid) for p, pid in rows if p == pane), None)
    if shell is None:
        return None
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            words = (proc / "cmdline").read_bytes().split(b"\0")[:2]
        except OSError:
            continue
        if binary.encode() in words and depth_below(int(proc.name), shell) is not None:
            return True
    return False


def input_box(pane):
    """Whether pane shows claude's input box: a ❯ line between two ─ rules. A dialog's ❯ has no rules."""
    lines = tmux("capture-pane", "-p", "-t", pane).splitlines()
    rule = lambda line: len(line.strip()) >= 10 and set(line.strip()) == {"─"}
    return any(line.startswith("❯") and rule(lines[i - 1]) and rule(lines[i + 1])
               for i, line in enumerate(lines[1:-1], 1))


def type_prompt(pane, prompt_file, client, binary, timeout=60):
    """send: no. Wait until the new pane shows claude's input box with claude still in front, type the prompt
    into it with Ctrl+J between lines and no Enter, then look for it in the box. Ctrl+J is claude's newline
    that works in any terminal (https://code.claude.com/docs/en/interactive-mode#multiline-input); a paste
    would collapse into [Pasted text]. Nothing is typed unless the box is there."""
    prompt = Path(prompt_file).read_text()
    kept = f"The prompt is in {prompt_file}."
    def say(text):
        tmux("display-message", "-c", client, "-d", "10000", f"handoff: {text}")
    deadline = time.monotonic() + timeout
    while not input_box(pane):
        running = running_in(pane, binary)
        if not running:
            return say(f"send: no, but {'the new pane is gone' if running is None else 'claude exited'}; "
                       f"nothing typed. {kept}")
        if time.monotonic() > deadline:
            return say(f"send: no, but claude showed no input box in {timeout} s; nothing typed. {kept}")
        time.sleep(0.2)
    for i, line in enumerate(prompt.split("\n")):
        if i:
            tmux("send-keys", "-t", pane, "C-j")
        if line:
            tmux("send-keys", "-t", pane, "-l", "--", line)
    start = " ".join(prompt.split())[:24]
    for _ in range(15):
        if start in " ".join(tmux("capture-pane", "-p", "-J", "-t", pane).replace("\xa0", " ").split()):
            return say("the prompt is in the new session's box, unsent: Enter sends it")
        time.sleep(0.2)
    say(f"typed the prompt into pane {pane} but cannot see it in the box; look before pressing Enter. {kept}")


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
    elif sys.argv[1:2] == ["--type"]:
        type_prompt(*sys.argv[2:6])
    else:
        main(*sys.argv[1:6])
