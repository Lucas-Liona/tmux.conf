# Presets for prefix+T. Each `## name` is a value of the launch buffer's preset: field.
# A preset is laid out like the buffer: YAML frontmatter between two --- lines with the fields it sets
# (by the same names; a field left out keeps its default), then the new session's first prompt.
# auto and none are built in: auto is this directory's usual skills, none is no skills.
# A dir: here is filled in on each press like handoff.toml's: {slug} is the old session's name.

## research
---
target: new-dir
dir:    ~/research/{slug}
---

## project
---
target: new-dir
dir:    ~/{slug}
---

## red-team
---
model:  fable
skills: ""
name:   redteam
send:   "no"
---

Red-team the previous session in the transcript. Read-only except one report.
1. Every claim that something works, is fixed, is free, or belongs to someone: check it against the file,
   commit or command output; mark it confirmed, wrong or unverified.
2. Where it was casual, overreached or stopped short: writes beyond what Lucas allowed, work handed off
   that it should have finished, tools or rules left undocumented.
3. Rank by what would cost Lucas most if wrong.
Write /mnt/c/Users/lucas/PhotoCraft-work/reports/<today> Red-team of <session name>.md and stop.
