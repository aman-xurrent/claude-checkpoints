# phased: the phase loop daemon

Drives 7-phase development in Claude Code from the approver's GitHub reviews. Standalone Python 3,
standard library only; `gh` for GitHub, `tmux` for the sessions. Live copy `~/personal/scripts/phased/`,
mirrored here. Plan and decisions: `docs/plan.md`, `../ipaas-verification-gate/docs/decisions.md`.

## What it does, per minute

1. Reads every registered pull request of a repository in one GraphQL call: head commit, latest review
   per author with state, time and the commit it was given on, review threads with resolved and outdated
   flags and their last comments.
2. `decide.py` turns the local state and those facts into one action. Your `APPROVED` review on the current
   head, dated after the last handoff and not consumed, with no unresolved current thread, starts the next
   phase. A blocking thread with a comment newer than the handoff sends the session back to address it.
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
never read back.

## Commands

```
phased handoff --pr N --phase P   at the end of a phase, from the PR worktree (registers on first use)
phased status                     every registered pull request
phased adopt --pr N --phase P [--worktree PATH]   rebuild a lost state file
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
and addressing wait, done stays done, merged or closed finishes.
