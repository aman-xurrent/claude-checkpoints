---
name: phase-2
description: Phase 2 of phased development, structures only. Data shapes, models, interfaces, types and migrations for the feature, no behavior. Started by the approval of the phase 1 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write
---

# Phase 2: Structures only

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 2).

## Allowed locations

- Ruby DSL, `connector/lib/ipaas/connector/`: core structures (`action.rb`, `trigger.rb`, `runbook.rb`,
  `connection.rb`, `schema.rb`), the type system under `types/`, the DSL builders under `dsl/`, schema
  generation under `schema/`.
- Job execution `connector/lib/ipaas/job/`, test case structures `connector/lib/ipaas/test_case/`.
- Platform models `platform/app/models/` and their concerns; migrations `platform/db/migrate/` with the
  regenerated `platform/db/schema.rb`.
- Frontend types `platform/app/javascript/types/` (`modelTypes.ts`, `workPerformedTypes.ts`).
- SDK structures `connector-sdk/lib/ipaas/`.

`agent_task_finalize --phase 2` fails on a code file outside these locations and on any spec change.

## Work

- Change the structures, models, interfaces and types so they carry what the feature needs.
- Nothing else: no methods with logic, no wiring, no call sites, no specs.
- Do not build and do not run tests. Things break on purpose because the behavior does not exist yet.
- Every symbol you rename or remove: run the impact analysis first (`gate.py references --name`, Serena).

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] Every new or changed type lives in an allowed location.
- [ ] No procedures, logic or behavioral wiring were added.
- [ ] No spec was created or changed.
- [ ] `agent_task_finalize --phase 2` exits 0.
- [ ] Phase 2 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.

Then the handoff in `ceremony.md`.
