# Presets for prefix+T. Each `## name` is a row in the picker, after Defaults.
# Its lines set launch-buffer fields by the same names; a field left out keeps its default.
# Below `---`: the new session's first message.

## red-team
model:  fable
skills:
name:   redteam
---
Red-team the previous session in the transcript. Read-only except one report.
1. Every claim that something works, is fixed, is free, or belongs to someone: check it against the file,
   commit or command output; mark it confirmed, wrong or unverified.
2. Where it was casual, overreached or stopped short: writes beyond what Lucas allowed, work handed off
   that it should have finished, tools or rules left undocumented.
3. Rank by what would cost Lucas most if wrong.
Write /mnt/c/Users/lucas/PhotoCraft-work/reports/<today> Red-team of <session name>.md and stop.
