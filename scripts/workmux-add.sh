#!/usr/bin/env bash
# Create a workmux worktree from a popup.
# Usage: workmux-add.sh [plain|prompt|auto]
#   plain  - branch name only
#   prompt - branch name plus a prompt injected into the agent pane
#   auto   - prompt only; an LLM names the branch (workmux add -A)
set -euo pipefail

mode="${1:-plain}"

case "${mode}" in
  auto)
    read -r -p "task (LLM will name the branch): " task
    [ -n "${task}" ] || exit 0
    workmux add -A -p "${task}"
    ;;
  prompt)
    read -r -p "branch: " branch
    [ -n "${branch}" ] || exit 0
    read -r -p "prompt: " task
    [ -n "${task}" ] && workmux add "${branch}" -p "${task}" || workmux add "${branch}"
    ;;
  *)
    read -r -p "branch: " branch
    [ -n "${branch}" ] || exit 0
    workmux add "${branch}"
    ;;
esac
