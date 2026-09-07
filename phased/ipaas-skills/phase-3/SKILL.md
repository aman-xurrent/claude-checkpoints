---
name: phase-3
description: Phase 3 of phased development, contracts only. Method and function signatures, constants, abstract classes and class-level data, every new body a NotImplementedError stub. Started by the approval of the phase 2 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write
---

# Phase 3: Contracts only

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 3).

## Allowed locations

- DSL and connector: `connector/lib/ipaas/connector/` (`dsl/`, `schema/`, `mapping/`, `authentication/`),
  `connector/lib/ipaas/job/`, `connector/lib/ipaas/test_case/`, SDK base classes `connector-sdk/lib/ipaas/connector/`.
- Rails: `platform/app/models/`, `controllers/`, `services/`, `jobs/`, `presenters/`, `helpers/`, `platform/lib/`.
- TypeScript: `platform/app/javascript/components/`, `hooks/`, `common/`, `pages/`, `layouts/`.

## Work

This changeset is the interface of the feature, and only that.

- Identify every method or function the feature needs, new or changed.
- A new method or function gets a stub body and nothing else:
  Ruby `raise NotImplementedError`, TypeScript `throw new Error("not implemented")`.
- A changed signature keeps its body. Do not delete it, do not edit it.
- Create or change constants, class-level and module-level data: private where possible, zero or default
  value, no behavior.
- Create abstract classes and abstract methods with contracts only.
- No structure, model, interface or type edits (that was phase 2; say so in the handoff if one is missing).
- No wiring, no call sites, no logic, no specs. Do not build, do not run tests.

`agent_task_finalize --phase 3` fails on: a code file outside the allowed locations, a spec change, a new
method whose body is not the stub, and any added line that is not a signature, a constant, class-level data
or a stub.

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] Every new method or function has a stub body only.
- [ ] Changed signatures kept their bodies untouched.
- [ ] Abstract classes and methods carry contracts only.
- [ ] New constants and class-level data are private where applicable and behavior-free.
- [ ] No structure, wiring, call site or logic was added; no spec changed.
- [ ] `agent_task_finalize --phase 3` exits 0.
- [ ] Phase 3 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.

Then the handoff in `ceremony.md`.
