---
name: phase-6
description: Phase 6 of phased development, invariants and the remaining gaps. Close what the phase 5 audit found and add assertions for the impossible states, NASA Power of 10 rule 5. Needs a gate proof. Started by the approval of the phase 5 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

# Phase 6: Invariants and phase 5 fixes

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 6).

## Work

1. **Close the gaps.** Every phase 2, 3 or 4 gap from the audit that is not applied yet, in that phase's shape.
2. **Assertions**, following NASA Power of 10 rule 5:
   - An assertion checks a state that must never happen if the code is correct: a nil where the contract
     forbids it, a count that cannot be negative, a status outside the allowed set, an ordering that is
     violated. It never checks ordinary input validation and never carries a side effect.
   - Aim for two per new or changed method: one on entry, one on the result, where the state is cheap to check.
   - A failed assertion raises with a message that names the violated invariant and the offending value.
   Ruby:
   ```ruby
   raise IPaaS::Error, "runbook #{reference} has no trigger after load" if trigger.nil?
   ```
   TypeScript:
   ```ts
   if (steps.length === 0) throw new Error(`runbook ${reference}: a loaded runbook has no steps`);
   ```
   Use the error class the surrounding code raises for its own contract violations; fall back to
   `IPaaS::Error` (Ruby) and `Error` (TypeScript).
3. **Prove one.** Declare in `.claude/proof/declaration.json` a spec example that trips an assertion:
   it must fail with the assertion reverted and pass with it. That is the gate proof for this phase.
4. Nothing from phase 7: no TODO marker removed, no implementation, no refactor.

`agent_task_finalize --phase 6` runs rubocop, yarn, the specs of the changed files, requires the proof to
be `pass` and fresh, and fails when a TODO marker was removed.

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] Every phase 5 gap is applied.
- [ ] The impossible states the feature relies on are covered by focused assertions.
- [ ] No phase 7 implementation, TODO removal, structural or unrelated refactor.
- [ ] Specs reviewed by the spec-reviewer agent before the commit.
- [ ] `agent_task_finalize --phase 6` exits 0 with the proof `pass`.
- [ ] Phase 6 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.

Then the handoff in `ceremony.md`.
