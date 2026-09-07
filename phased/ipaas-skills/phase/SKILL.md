---
name: phase
description: Start phased development for a Xurrent request or a requirement. Runs discovery (phase 1), opens a draft PR, and hands the request back for review. Phases 2 to 7 each start after the PR approval. This is the default way to make a code change in this repository; a prompt that says "quick" skips it.
user-invocable: true
argument-hint: "<request id | requirement>"
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

Phased development: one branch, one draft PR, one commit per phase, a human approval between phases.
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
   with the new id. Never work on `main`. Work in a worktree with a slot (`/setup-worktree`): phases 5 to 7
   run specs and the local instance, and `yarn check` needs the generated routes a booted Rails writes.
2. **Register phase 1.** In the worktree root: `mkdir -p .claude/proof && printf 1 > .claude/proof/phase`.
   The gate reads this file: the prompt hook reminds you which phase is active, `prepare-commit-msg` stamps
   `Phase: 1`, and `pre-push` knows phases 1 to 5 carry no proof.
3. **Run phase 1.** Read `.claude/skills/phase-1/SKILL.md` and carry it out to its handoff. Stop there.

## Rules that hold for every phase

- No phase is skipped. "Go to phase 4" means phases 2, 3 and 4 in order, each with its own approval.
- One commit per phase, subject `Request#<id> Phase N: <what>`. A phase with no changeset commits empty.
- Never start phase N+1 on your own. The approval of the phase N commit starts it.
- `.claude/bin/agent_task_finalize --phase N` must exit 0 before a handoff. It checks the shape of the diff
  for the phase, rubocop, yarn, and for phases 6 and 7 the specs and the revert proof.
- Impact before edits: `python3 ~/personal/scripts/gate/gate.py references --name <symbol>` and Serena
  `find_referencing_symbols` for every symbol you rename, move, or delete. Raw grep is not an impact analysis.
