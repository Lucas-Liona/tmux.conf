#!/usr/bin/env bash
# Show a list of panes one by one on Lucas's terminal, and kill them if asked.
#   pane-tour.sh <idfile|-> [--kill | --instant] [--dwell 0.75] [--client NAME]
#
# --instant kills without the tour: no switching, painting or waiting, the
# same checks at kill time (and still nothing in a window with claude/codex,
# whose pane would be widened into the freed space).
#
# For each pane id in <idfile> (one per line, e.g. %15): switch the client to
# its window, select it, paint it red, and say "32/131 chezmoi:2.4 idle zsh
# ~/street-fighter" in the status line for the dwell. --kill then kills it,
# after checking again that it is still a bare idle shell with no children and
# not the last non-sidebar pane of its window.
#
# Any key stops the tour, and no key ever reaches a pane while it runs: the
# client sits in a temporary key table, pane-tour, whose Any binding raises a
# stop flag (and swallows mouse movement, which would otherwise count as
# activity on every twitch). The table is emptied on exit.
#
# Skipped: windows with a claude or codex pane; sessions another client is
# viewing (switching there would change that client's screen); and sessions
# holding a claude/codex window of another width than the client, because a
# client in a session resizes all its windows and Claude's scrollback does not
# survive a width change. On any exit the
# client, every session's current window, every window's active pane and every
# painted pane's style go back to how they were.
#
# --client defaults to the one attached terminal (non-control) client and
# refuses to guess when there are several.
set -euo pipefail

idfile="" kill=0 instant=0 dwell=0.75 client=""
while [ $# -gt 0 ]; do
    case $1 in
        --kill) kill=1 ;;
        --instant) kill=1 instant=1 ;;
        --dwell) dwell=$2; shift ;;
        --client) client=$2; shift ;;
        -) idfile=/dev/stdin ;;
        -*) echo "unknown option $1" >&2; exit 2 ;;
        *) idfile=$1 ;;
    esac
    shift
done
[ -r "$idfile" ] || { echo "usage: pane-tour.sh <idfile|-> [--kill | --instant] [--dwell 0.75] [--client NAME]" >&2; exit 2; }
mapfile -t ids < <(grep -o '%[0-9]*' "$idfile")  # tolerates a missing final newline

# display-message -p -t on a pane that is gone still exits 0 (empty fields), so
# existence is a list-panes question.
exists() { grep -qxF "$1" <<<"$(tmux list-panes -a -F '#{pane_id}')"; }
children() { cat "/proc/$1/task/$1/children" 2>/dev/null; }
total=${#ids[@]} shown=0 killed=0 gone=0 stopped=""
declare -A skipped_claude=() skipped_viewed=() skipped_width=()
refused=()

# Kill a listed pane only if it is, right now, still a bare idle shell with no
# children and not the last non-sidebar pane of its window: the list can be
# hours old and a pane can come back to life.
try_kill() {  # pane window where
    local cmd pid real
    IFS='|' read -r cmd pid <<<"$(tmux display-message -p -t "$1" '#{pane_current_command}|#{pane_pid}')"
    real=$(tmux list-panes -t "$2" -F '#{@workmux_role}' | grep -cvx sidebar || true)
    if ! exists "$1"; then gone=$((gone + 1))
    elif [ "$cmd" != zsh ] && [ "$cmd" != bash ]; then refused+=("$3: runs $cmd")
    elif [ -n "$(children "$pid")" ]; then refused+=("$3: has a child process")
    elif [ "$real" -le 1 ]; then refused+=("$3: last real pane in its window")
    else tmux kill-pane -t "$1"; killed=$((killed + 1))
    fi
}
in_agent_window() {  # window -- captured first: grep -q exiting early can SIGPIPE
    local commands       # list-panes, and under pipefail that reads as "no claude here"
    commands=$(tmux list-panes -t "$1" -F '#{pane_current_command}')
    grep -qxE 'claude|codex' <<<"$commands"
}
report() {
    if [ "$instant" = 1 ]; then echo "pane-tour --instant: killed $killed of $total, gone $gone"
    else echo "pane-tour: shown $shown of $total$([ "$kill" = 1 ] && echo ", killed $killed"), gone $gone"; fi
    [ -n "$stopped" ] && echo "stopped by a key at $stopped"
    [ ${#refused[@]} -gt 0 ] && printf 'refused: %s\n' "${refused[@]}"
    [ ${#skipped_claude[@]} -gt 0 ] && echo "skipped, a window with claude/codex: ${!skipped_claude[*]}"
    [ ${#skipped_viewed[@]} -gt 0 ] && echo "skipped, another client is viewing: ${!skipped_viewed[*]}"
    [ ${#skipped_width[@]} -gt 0 ] && echo "skipped, would resize a claude window in the session: ${!skipped_width[*]}"
    true
}

if [ "$instant" = 1 ]; then
    for pane in "${ids[@]}"; do
        exists "$pane" || { gone=$((gone + 1)); continue; }
        IFS='|' read -r window where <<<"$(tmux display-message -p -t "$pane" '#{window_id}|#{session_name}:#{window_index}.#{pane_index}')"
        if in_agent_window "$window"; then skipped_claude[${where%.*}]=1; continue; fi
        try_kill "$pane" "$window" "$where"
    done
    report
    exit 0
fi

if [ -z "$client" ]; then
    mapfile -t terminals < <(tmux list-clients -F '#{client_name} #{client_control_mode}' | awk '$2 == 0 {print $1}')
    if [ ${#terminals[@]} -ne 1 ]; then
        echo "found ${#terminals[@]} terminal clients (${terminals[*]:-none}); pass --client NAME" >&2
        exit 2
    fi
    client=${terminals[0]}
fi
client_field() {  # list-clients, not display -c: the latter reads the most recent client
    tmux list-clients -F "#{client_name}|$1" | awk -F'|' -v c="$client" '$1 == c {print $2}'
}
home=$(client_field '#{pane_id}')
[ -n "$home" ] || { echo "no client named $client" >&2; exit 2; }

# ---- snapshot, and put everything back on any exit
snap=$(mktemp -d)
tmux list-sessions -F '#{session_id}|#{window_id}' > "$snap/sessions"   # current window
tmux list-windows -a -F '#{window_id}|#{pane_id}' > "$snap/windows"     # active pane
: > "$snap/styles"                                                       # pane|window-style|window-active-style

restore() {
    set +e
    tmux unbind-key -T pane-tour Any 2>/dev/null
    [ "$(client_field '#{client_key_table}')" = pane-tour ] && tmux switch-client -c "$client" -T root
    tmux set-option -gu @pane_tour_stop
    while IFS='|' read -r pane ws was; do
        exists "$pane" || continue  # killed
        if [ -n "$ws" ]; then tmux set-option -p -t "$pane" window-style "$ws"
        else tmux set-option -p -u -t "$pane" window-style; fi
        if [ -n "$was" ]; then tmux set-option -p -t "$pane" window-active-style "$was"
        else tmux set-option -p -u -t "$pane" window-active-style; fi
    done < "$snap/styles"
    # Only what differs, so untouched windows keep their last-pane history.
    tmux list-windows -a -F '#{window_id}|#{pane_id}' | sort > "$snap/windows.now"
    sort "$snap/windows" | comm -23 - "$snap/windows.now" | while IFS='|' read -r _ pane; do
        tmux select-pane -t "$pane" 2>/dev/null   # gone if it was a killed stand-in
    done
    tmux list-sessions -F '#{session_id}|#{window_id}' | sort > "$snap/sessions.now"
    sort "$snap/sessions" | comm -23 - "$snap/sessions.now" | while IFS='|' read -r _ window; do
        tmux select-window -t "$window" 2>/dev/null
    done
    tmux switch-client -c "$client" -t "$home" 2>/dev/null
    rm -rf "$snap"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

# ---- the stop key
tmux set-option -gu @pane_tour_stop
tmux bind-key -T pane-tour Any if -F '#{!=:#{mouse_x},}' 'switch-client -T pane-tour' 'set-option -g @pane_tour_stop 1'

width=$(client_field '#{client_width}')
others=$(tmux list-clients -F '#{client_name}|#{session_id}' | awk -F'|' -v c="$client" '$1 != c {print $2}' | sort -u)
dwell_ms=$(awk -v d="$dwell" 'BEGIN {printf "%d", d * 1000}')
for i in "${!ids[@]}"; do
    pane=${ids[$i]} n=$((i + 1))
    exists "$pane" || { gone=$((gone + 1)); continue; }
    info=$(tmux display-message -p -t "$pane" \
        '#{session_id}|#{window_id}|#{session_name}:#{window_index}.#{pane_index}|#{pane_current_command}|#{pane_current_path}|#{pane_pid}')
    IFS='|' read -r session window where cmd path pid <<<"$info"
    wname=${where%.*}
    if in_agent_window "$window"; then
        skipped_claude[$wname]=1; continue
    fi
    if grep -qxF "$session" <<<"$others"; then
        skipped_viewed[$wname]=1; continue
    fi
    agents=$(tmux list-panes -s -t "$session" -F '#{pane_current_command} #{window_width}' | grep -E '^(claude|codex) ' || true)
    if [ -n "$agents" ] && [ "$(grep -cvx "[a-z]* $width" <<<"$agents")" -gt 0 ]; then
        skipped_width[$wname]=1; continue
    fi

    state=$([ -z "$(children "$pid")" ] && echo idle || echo busy)
    printf '%s|%s|%s\n' "$pane" "$(tmux show-options -p -t "$pane" -qv window-style)" \
        "$(tmux show-options -p -t "$pane" -qv window-active-style)" >> "$snap/styles"
    tmux switch-client -c "$client" -t "$pane"
    tmux select-window -t "$window"
    tmux select-pane -t "$pane" -P 'bg=#ed8796'
    tmux switch-client -c "$client" -T pane-tour
    tmux display-message -c "$client" -d "$dwell_ms" "$n/$total $where $state $cmd ${path/#$HOME/\~}"
    shown=$((shown + 1))

    end=$(( $(date +%s%N) + dwell_ms * 1000000 ))
    while [ "$(date +%s%N)" -lt "$end" ]; do
        if [ "$(tmux show-option -gqv @pane_tour_stop)" = 1 ]; then stopped="$n/$total ($where)"; break 2; fi
        sleep 0.05
    done
    tmux set-option -p -u -t "$pane" window-style
    tmux set-option -p -u -t "$pane" window-active-style

    [ "$kill" = 1 ] && try_kill "$pane" "$window" "$where"
done

report | tee "$snap/report"
tmux display-message -c "$client" -d 4000 "$(head -1 "$snap/report")"
