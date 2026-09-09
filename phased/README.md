# phased: the phase loop daemon

Drives 7-phase development in Claude Code from the approver's GitHub reviews. Standalone Python 3,
standard library only; `gh` for GitHub, `tmux` for the sessions. Live copy `~/personal/scripts/phased/`,
mirrored here. Plan and decisions: `docs/plan.md`, `../ipaas-verification-gate/docs/decisions.md`.

## What it does, per minute

1. Reads every registered pull request of a repository in one GraphQL call: head commit, latest review
   per author with state, time and the commit it was given on, review threads with resolved and outdated
   flags and their last comments, and the last thirty conversation comments.
2. `decide.py` turns the local state and those facts into one action. Your conversation comment whose first
   line is `Approved` (GitHub refuses a review approval from the PR author; a real `APPROVED` review by you
   counts too), newer than the last handoff and than the last commit, not consumed, with no unresolved
   current thread, starts the next phase. `Approved, but rename X` is feedback, not a release. A blocking thread with a comment newer than the handoff sends the session back to address it.
   Phase 7 approved, or a merged or closed PR, ends the loop.
3. The action is typed as one line into the Claude window named `pr<number>` in the repository's tmux
   session when a Claude process is alive there; otherwise a new window starts `claude` with the brief.
   The line points at a brief file under `~/.local/state/phased/<owner>__<repo>/pr-<n>/`.
4. A live ghmention (`@claude`) run on the same pull request makes phased wait.

## State

`~/.local/state/phased/<owner>__<repo>/pr-<n>/state.json` is the truth: phase, status
(`working | waiting_approval | addressing_comments | done`), the head at handoff, the handoff time, the
consumed approval ids, the window id, the worktree. Writes are temp file plus rename with a revision check.
`history.jsonl` records every transition. A `phase:N` label on the PR mirrors the phase for humans and is
never read back. The state also carries `claude_session_id`, the id the session was launched with, so a
lost session can be reopened with its conversation.

## tmux persistence, and who owns Claude

tmux-resurrect and tmux-continuum keep the terminal side across a restart: sessions, windows, their
names, the layout and the working directory of every pane. They restore the shape. They must never
restore Claude.

A restored `claude` is a fresh process with no conversation and no brief. The daemon reads that window as
a healthy session and leaves the pull request alone, which is worse than an empty window. So `claude`
stays out of `@resurrect-processes`, and the daemon reopens the session itself with `claude --resume`.
Verified on this machine: a restore brought back every window, including `pr1027` with its worktree, and
every Claude pane came back as a plain shell.

Two rules in the daemon follow from that split.

A restart only joins a tmux server that already runs. It never creates one. After a reboot the daemon
starts long before the user opens a terminal, and creating the server there would fire continuum's
restore against a server the daemon made, whose next auto-save would overwrite the good save. Waiting
also means a session restarts when the user is at the machine.

A restart reuses the window resurrect brought back. `find_window` matches the name whatever runs inside,
and `respawn-pane -k` puts Claude into that window, so the loop never leaves two windows called `pr1027`.

The tmux side lives in `~/.tmux.conf`, which is in neither repository:

```tmux
set -g @plugin 'tmux-plugins/tmux-resurrect'
set -g @plugin 'tmux-plugins/tmux-continuum'
set -g @resurrect-processes 'vi vim view nvim emacs man less more tail top htop lazygit'
set -g @resurrect-capture-pane-contents 'on'
set -g @resurrect-hook-post-save-all '~/.local/bin/tmux-resurrect-prune'
set -g @continuum-save-interval '5'
set -g @continuum-restore 'on'
```

`tmux-resurrect-prune` (in this directory, installed to `~/.local/bin`) keeps the last 120 saves, because
resurrect never deletes one and continuum writes a save every five minutes. Saves land in
`~/.local/share/tmux/resurrect` unless `~/.tmux/resurrect` exists.

## When a session is lost

A machine restart kills every tmux window, and a pull request in `working` or `addressing_comments` is
invisible to `decide`, which only reads a pull request that waits for approval. Every tick therefore checks
the registered pull requests for a live Claude window before it polls GitHub, and restarts the ones that
have none. `claude --resume <id>` reopens the same conversation when its transcript is still on disk;
otherwise a new session starts with the same brief. The restart brief never says "carry on": it makes the
session read `git status`, `git log`, and whether HEAD is already on the remote, and continue from what it
finds, so a phase that was already committed and pushed ends in a handoff instead of a second commit.

Four rules hold the restart back. Claude already running anywhere inside the worktree stops it, even when
the window was renamed, so two sessions can never edit the same files. A missing worktree stops it. Nothing
restarts in the first `RESUME_GRACE_SECONDS` (180) after the daemon starts, because at boot the network is
not up and the user is not at the machine. Attempts stop at `RESUME_MAX_ATTEMPTS` (3), one per
`RESUME_COOLDOWN_SECONDS` (600), and the last one notifies. `phased restart --pr N` overrides all four.

## Commands

```
phased handoff --pr N --phase P   at the end of a phase, from the PR worktree (registers on first use)
phased status                     every registered pull request
phased adopt --pr N --phase P [--worktree PATH]   rebuild a lost state file
phased restart --pr N [--fresh] [--force]   restart a session by hand (--fresh drops the conversation)
phased pause | resume             stop or restart acting; polling continues
phased logs [-f]
phased run [--once] [--dry-run]   the loop; the LaunchAgent runs `phased run`
phased init                       write ~/.config/phased/config.json
```

Config: `{"me": "<github login>", "gh_host": "git.4me.com", "poll_seconds": 60, "labels": true,
"notify": true, "repos": {"4me/ipaas": {"tmux": "ipaas", "path": "~/work/ipaas"}}}`.

## Tests

`python3 -m unittest tests.test_decide` covers the decision: approval on the head, on an older commit,
before the handoff, consumed, by a teammate, changes requested, new comment wins over approval, blocking
thread without new comment holds, outdated and resolved threads do not block, phase 7 finishes, working
and addressing wait, done stays done, merged or closed finishes. `decide_resume` is covered too: a dead
window while working or addressing restarts, a live window does not, waiting and done do not, a missing
worktree does not, another Claude in the worktree blocks it, the grace period holds, attempts stop at the
cap, and the cooldown holds the second attempt back.
