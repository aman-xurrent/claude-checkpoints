---
name: phase-5
description: Phase 5 of phased development, the exploratory implementation audit. Implement the whole feature inside the TODO markers in the checks worktree, run it, check it in the browser, then bring back only the audit and the phase 2 to 4 gaps. Started by the approval of the phase 4 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

# Phase 5: Exploratory implementation audit

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 5).

The point of this phase is to find out whether phases 2 to 4 captured every change the feature needs,
by implementing it once and throwing that implementation away. The PR worktree never holds exploratory code.

## Procedure

`GATE=python3 ~/personal/scripts/gate/gate.py`, `CHECKS=~/work/ipaas_worktrees/checks`.

1. **Clean start.** In the PR worktree `git status --porcelain` prints nothing. Record `PR_HEAD=$(git rev-parse HEAD)`.
2. **Take the checks worktree.** From the PR worktree: `$GATE checks apply`. It puts the branch state into
   `$CHECKS` (one permanent slot: own databases, Redis DBs, ports), regenerates the routes, and prepares the
   databases when the branch carries migrations. `checks status` names who holds it; when another branch
   does, stop and hand off with "phase 5 waits for the checks worktree". Never use
   `~/work/ipaas_worktrees/gate` and never create or delete a worktree.
3. **Implement, in `$CHECKS` only.** The full feature, inside the markers:
   - Do not remove a TODO marker. Do not add a new function or type. Do not edit outside a marker.
   - Run the specs of the touched files there. Start the app: `cd $CHECKS && source .claude/worktree.env &&
     cd platform && bin/dev`, open `http://127.0.0.1:$WEB_PORT`, verify the feature with the Chrome MCP
     (or `rails runner` for backend-only work).
   - Missing test credentials or data: say exactly what is missing in the audit; do not invent them.
   Do not commit in `$CHECKS`. Nothing there is ever pushed.
4. **Capture.** `mkdir -p ~/.local/state/phased/ipaas/pr-<n>/phase-5`, then in `$CHECKS`:
   `git add -N . && git diff > ~/.local/state/phased/ipaas/pr-<n>/phase-5/exploratory.patch`.
   Every hunk is a gap in phases 2 to 4 or the phase 7 implementation. Classify each hunk: structure (2),
   contract (3), change point (4), or implementation (7). Note every contract that felt wrong or awkward.
5. **Release.** Stop `bin/dev`. From the PR worktree: `$GATE checks reset` (`--rebuild-dev-db` when the
   exploration ran a migration). `$CHECKS` is back at `origin/main`, clean; `git status --porcelain` in the
   PR worktree still prints nothing and `git rev-parse HEAD` is `$PR_HEAD`.
6. **Carry the gaps back.** Apply the phase 2, 3 and 4 gaps in the PR worktree, obeying those phases' shapes
   (structures in their locations, stubs for new methods, TODO markers for change points). One commit,
   `Request#<id> Phase 5: gaps from the audit`. No gaps: an empty commit.

## The audit, in the PR description

`## Phase 5 audit`: what was implemented and run, what the browser check showed (page, action, result),
the gap table (hunk → phase → applied yes/no), the awkward contracts for discussion, missing data or
credentials, and the path of the exploratory patch.

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] The exploratory implementation covered the complete scoped flow, not a partial compile path.
- [ ] Every uncaptured change is classified as phase 2, 3, 4 or 7.
- [ ] Every known crash, missing behavior and unresolved marker is classified before phase 6.
- [ ] Awkward contracts are listed for discussion.
- [ ] The checks worktree is released and clean; the PR worktree holds no exploratory edit.
- [ ] `agent_task_finalize --phase 5` exits 0.
- [ ] Phase 5 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.

Then the handoff in `ceremony.md`.
