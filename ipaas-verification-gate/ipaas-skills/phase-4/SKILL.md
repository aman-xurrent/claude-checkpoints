---
name: phase-4
description: Phase 4 of phased development, TODO markers only. A formatted TODO comment at every change point in running code, no code added or removed. Started by the approval of the phase 3 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit
---

# Phase 4: TODO markers in running code

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 4).

## Allowed locations

- Connector: `connector/lib/ipaas/connector/` (`dsl/`, `mapping/`, `schema/`, `authentication/`, `common/`,
  `types/`, `core_ext/`), `connector/lib/ipaas/job/`, `connector/lib/ipaas/test_case/`, `connector/lib/ipaas/encryption/`.
- Rails: `platform/app/models/`, `services/`, `controllers/`, `jobs/`, `presenters/`.
- TypeScript: `platform/app/javascript/components/`, `hooks/`, `common/`, `pages/`, `layouts/`.

## The marker

```ruby
# TODO(phase-4 #<request>): <what changes here and why>
# <second line if needed>
# <third line at most>
```

```ts
// TODO(phase-4 #<request>): <what changes here and why>
```

`<request>` is the request number from the branch name. Phase 7 removes every marker with that number and
`agent_task_finalize --phase 7` fails while one remains.

## Work

- Add one marker at every place in running code where logic changes to make the feature work: the call
  site that starts using a phase 3 method, the branch that reads a phase 2 structure, the place a stub gets
  wired in.
- Each reason is three lines or fewer.
- No code of any kind is added or removed. Comment lines only.
- When a structure or a contract is missing (phase 2 or 3 forgot it): finish the markers you can place,
  and list the missing pieces with the phase they belong to in the PR section. Do not add them here.
- Do not build, do not run tests.

`agent_task_finalize --phase 4` fails on: a non-comment added line, a removed line of code, a file outside
the allowed locations, a spec change, a reason longer than three lines, or no marker at all.

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] Every required change point in running code carries a formatted marker.
- [ ] Every reason is three lines or fewer.
- [ ] No runtime behavior, structure or signature was added; nothing was removed.
- [ ] Missing phase 2 or 3 pieces, if any, are listed in the PR section with their phase.
- [ ] `agent_task_finalize --phase 4` exits 0.
- [ ] Phase 4 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.

Then the handoff in `ceremony.md`.
