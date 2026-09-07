---
name: phase-5
description: Phase 5 of phased development, the exploratory implementation audit. Implement the whole feature inside the TODO markers in a throwaway worktree, run it, check it in the browser, then bring back only the audit and the phase 2 to 4 gaps. Started by the approval of the phase 4 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

# Phase 5: Exploratory implementation audit

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 5).

The point of this phase is to find out whether phases 2 to 4 captured every change the feature needs,
by implementing it once and throwing that implementation away. The PR worktree never holds exploratory code.

## Procedure

1. **Clean start.** In the PR worktree `git status --porcelain` prints nothing. Record `PR_BRANCH=$(git branch --show-current)`.
2. **Throwaway worktree.**
   `git worktree add ~/work/ipaas_worktrees/explore-<request> -b explore/<request> HEAD`, then in it
   `.claude/bin/setup-worktree` for a slot (own database, Redis database, ports, Vite build). No slot free:
   stop, hand off with "phase 5 waits for a worktree slot", and touch nothing else. Never use
   `~/work/ipaas_worktrees/gate` and never delete another worktree.
3. **Implement, in the throwaway only.** The full feature, inside the markers:
   - Do not remove a TODO marker. Do not add a new function or type. Do not edit outside a marker.
   - Build. Run the specs of the touched files. Start the local instance on the slot's ports and verify
     the feature in the browser with the Chrome MCP (or `rails runner` for backend-only work).
   - Missing test credentials or data: say exactly what is missing in the audit; do not invent them.
   Commit checkpoints on `explore/<request>` as often as you like. Never push that branch.
4. **Capture.** `mkdir -p ~/.local/state/phased/ipaas/pr-<n>/phase-5` and
   `git diff <PR_BRANCH>...HEAD > ~/.local/state/phased/ipaas/pr-<n>/phase-5/exploratory.patch`
   (add `git diff HEAD >> ...` for uncommitted work). Every hunk in that patch is a gap in phases 2 to 4 or
   the phase 7 implementation. Classify each hunk: structure (2), contract (3), change point (4), or
   implementation (7). Note every contract that felt wrong or awkward.
5. **Tear down.** In the main clone: `git worktree remove --force ~/work/ipaas_worktrees/explore-<request>`
   and `git branch -D explore/<request>`. Release the slot the way `.claude/skills/setup-worktree/SKILL.md`
   describes. Back in the PR worktree `git status --porcelain` must print nothing.
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
- [ ] The throwaway worktree and branch are gone; the PR worktree holds no exploratory edit.
- [ ] `agent_task_finalize --phase 5` exits 0.
- [ ] Phase 5 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.

Then the handoff in `ceremony.md`.
