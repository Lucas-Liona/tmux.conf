# Presets for prefix+T. Each `## name` is a value of the launch buffer's preset: field.
# A preset is laid out like the buffer: the new session's first prompt, a ━ bar (copy this one),
# then the fields it sets by the same names; a field left out keeps its default.
# auto and none are built in: auto is this directory's usual skills, none is no skills.

## red-team
Red-team the previous session in the transcript. Read-only except one report.
1. Every claim that something works, is fixed, is free, or belongs to someone: check it against the file,
   commit or command output; mark it confirmed, wrong or unverified.
2. Where it was casual, overreached or stopped short: writes beyond what Lucas allowed, work handed off
   that it should have finished, tools or rules left undocumented.
3. Rank by what would cost Lucas most if wrong.
Write /mnt/c/Users/lucas/PhotoCraft-work/reports/<today> Red-team of <session name>.md and stop.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
model:  fable
skills:
name:   redteam
