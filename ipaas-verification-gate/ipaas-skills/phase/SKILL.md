---
name: phase
description: Start phased development for a Xurrent request or a requirement. Runs discovery (phase 1), opens a draft PR, and hands the request back for review. Phases 2 to 7 each start after the PR approval. This is the default way to make a code change in this repository; a prompt that says "quick" skips it.
user-invocable: true
argument-hint: "<request id | requirement>"
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

Phased development: one branch, one draft PR, one commit per phase, a human approval between phases. The
approval is a conversation comment on the PR whose first line is `Approved`, by the approver, after the
phase's last commit (GitHub refuses a review approval from the PR author).
The phases are `/phase-1` to `/phase-7`. Each phase skill names what it may change, its done criteria,
and the handoff. The handoff and the phase start are the same for every phase: `.claude/skills/phase/ceremony.md`.

| Phase | Changes | Proof |
|---|---|---|
| 1 | nothing: discovery and the plan in the PR description | none |
| 2 | structures, models, interfaces, types, migrations | none |
| 3 | signatures, constants, abstract classes, `NotImplementedError` stubs | none |
| 4 | `TODO(phase-4 #<request>)` comments at every change point | none |
| 5 | exploratory implementation in a throwaway worktree; only the audit and the phase 2 to 4 gaps come back | none |
| 6 | the remaining gaps and the assertions | gate proof |
| 7 | the implementation, the specs, the live check | gate proof |

## Steps

1. **Resolve the record.** `$ARGUMENTS` is a number: run `/work-on request <id>` (branch `requests/<id>-*`,
   assignment, In Progress). `$ARGUMENTS` is requirement text: run `/create-request` first, then `/work-on`
   with the new id. Never work on `main`. The PR worktree needs no slot: rubocop, yarn, specs and the live
   checks run in the checks worktree (`~/work/ipaas_worktrees/checks`, one permanent slot, created once with
   `python3 ~/personal/scripts/gate/gate.py setup-checks`), which returns to `origin/main` after every use.
2. **Register phase 1.** In the worktree root: `mkdir -p .claude/proof && printf 1 > .claude/proof/phase`.
   The gate reads this file: the prompt hook reminds you which phase is active, `prepare-commit-msg` stamps
   `Phase: 1`, and `pre-push` knows phases 1 to 5 carry no proof.
3. **Run phase 1.** Read `.claude/skills/phase-1/SKILL.md` and carry it out to its handoff. Stop there.

## Rules that hold for every phase

- No phase is skipped. "Go to phase 4" means phases 2, 3 and 4 in order, each with its own approval.
- One commit per phase, subject `Request#<id> Phase N: <what>`. A phase with no changeset commits empty.
  Phases 1 to 5 carry `[skip ci]`, written by the commit hook; phases 6 and 7 run the full CI.
- Never start phase N+1 on your own. The approval of the phase N commit starts it.
- `.claude/bin/agent_task_finalize --phase N` must exit 0 before a handoff. It checks the shape of the diff
  for the phase, then applies the branch state to the checks worktree, runs rubocop, yarn, and for phases 6
  and 7 the specs there, resets that worktree to `origin/main`, and reads the revert proof and the references.
- A live check (phases 5 and 7) uses the same worktree: `gate.py checks apply` puts the branch state there,
  `bin/dev` runs on its slot, `gate.py checks reset` cleans it. Never edit the PR worktree while it holds a
  live check of another branch; `checks status` tells who holds it.
- Every comment you post on the pull request goes through `.claude/bin/pr-comment --pr N --title "..."
  --body-file <path>`. It stamps the phase, the branch, the worktree and the tmux session above a collapsed
  block, so a reader can tell an automated comment from one you typed. The raw calls are denied.
- Impact before edits: `python3 ~/personal/scripts/gate/gate.py references --name <symbol>` and Serena
  `find_referencing_symbols` for every symbol you rename, move, or delete. Raw grep is not an impact analysis.
