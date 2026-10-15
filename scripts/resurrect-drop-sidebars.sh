#!/usr/bin/env bash
# tmux-resurrect post-restore-all hook: remove the stand-in panes that the
# workmux sidebar leaves behind after a restore.
#
# The sidebar is an ordinary pane (pane 0) running `workmux _sidebar-run`.
# resurrect saves it, can't relaunch `workmux`, and brings it back as a bare
# zsh. If workmux's after-new-window hook is installed it ALSO adds a fresh
# sidebar, and the stand-in is then saved again on the next cycle -- so
# windows grow an extra shell on every restore.
#
# The save file still records which panes were running `workmux`, so kill
# exactly those stand-ins. Guards: it must be a bare shell, and not the
# window's last pane.
set -u

# The save file describes the server as it was BEFORE it went down. Run against
# a live server (a manual prefix+C-r, or the hook firing twice) it would match
# real shells by index and kill them. So: once per server, and only while the
# server is young enough to still be mid-restore.
[ "$(tmux show-option -gqv @sidebar_standins_dropped)" = 1 ] && exit 0
age=$(( $(date +%s) - $(tmux display-message -p '#{start_time}') ))
[ "$age" -lt 300 ] || exit 0
tmux set-option -g @sidebar_standins_dropped 1

# workmux's after-new-window sync runs asynchronously (run-shell -b) and can
# insert a sidebar, shifting indexes, while restore is still building windows.
# Let those settle before matching anything by index.
sleep 3

log="$HOME/.local/share/tmux/resurrect-drop-sidebars.log"
note() { printf '%s %s\n' "$(date +%T)" "$*" >>"$log"; }

dir=$(tmux show-option -gqv @resurrect-dir)
dir=${dir:-$HOME/.local/share/tmux/resurrect}
dir=${dir/#\~/$HOME}
dir=${dir//\$HOME/$HOME}
save=$(readlink -f "$dir/last") || exit 0
[ -s "$save" ] || exit 0

# pane rows: pane  session  window  active  flags  index  title  dir  active  cmd  fullcmd
awk -F'\t' '$1=="pane" && $10=="workmux" {print $2 "\t" $3 "\t" $6}' "$save" |
while IFS=$'\t' read -r session window idx; do
    t="${session}:${window}.${idx}"
    # With the workmux hook installed, a fresh sidebar took the saved index and
    # pushed the stand-in down one. Without it, the stand-in is still at idx.
    role=$(tmux display-message -p -t "$t" '#{@workmux_role}' 2>/dev/null) || continue
    [ "$role" = sidebar ] && t="${session}:${window}.$((idx + 1))"

    # Pin the target by pane id so nothing can shift between check and kill.
    info=$(tmux display-message -p -t "$t" '#{pane_id} #{pane_current_command} #{window_panes} #{@workmux_role}' 2>/dev/null) || { note "skip $t: gone"; continue; }
    read -r id cmd panes prole <<<"$info"
    case $cmd in zsh|bash|sh) ;; *) note "skip $t ($id): running $cmd"; continue ;; esac
    if [ "$panes" -gt 1 ] && [ -z "$prole" ]; then
        note "kill $t ($id): bare $cmd stand-in, $panes panes in window"
        tmux kill-pane -t "$id"
    else
        note "skip $t ($id): panes=$panes role=$prole"
    fi
done

exit 0
