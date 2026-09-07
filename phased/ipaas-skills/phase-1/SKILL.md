---
name: phase-1
description: Phase 1 of phased development, discovery. Understand the request, the prior work, the similar code and every file the change will touch, then write the plan for phases 2 to 7 into the draft PR. No code, no tests.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Agent
---

# Phase 1: Discovery

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 1).

## Work

1. Read the request end to end: subject, requirement, design, risk analysis, notes. Name the outcome the
   requester wants in one sentence; that sentence heads the PR description.
2. Prior work: `git log --oneline --grep=<key words> origin/main | head`, closed PRs on the topic
   (`GH_HOST=git.4me.com gh pr list --state merged --search "<key words>"`), and the notes on the request.
3. Similar code: find the closest existing feature and read it whole. Use Serena (`find_symbol`,
   `find_referencing_symbols`) and `python3 ~/personal/scripts/gate/gate.py references --name <symbol>` for
   every symbol the change will touch. Read every file involved from top to bottom, not the hits alone.
4. Read `AGENTS.md` and the `AGENTS.md` of each sub-project involved.
5. Write the scratch plan by phase:
   - Phase 2: which structures, models, types, migrations change, in which files.
   - Phase 3: which methods and functions are new or change signature, which constants, which abstract classes.
   - Phase 4: the change points in running code that get a TODO marker, by file.
   - Phase 6: the impossible states the feature must assert.
   - Phase 7: what proves the feature works: the spec example the gate proof will declare, the live check.
6. Do not create tests. Do not change code.

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] The request was selected (or created) and is assigned to me, In Progress.
- [ ] `AGENTS.md` and the sub-project rules were read.
- [ ] Every file involved in the change was read and understood.
- [ ] Prior and similar work was found and named with paths.
- [ ] The plan by phase is written in the PR description.

## Handoff

The PR description: the outcome sentence, `## Request` (link), `## Discovery` (prior work, similar code,
files involved with paths, risks), `## Plan by phase`, `## Phase 1` checklist. Commit empty:
`Request#<id> Phase 1: discovery`. Then the handoff in `ceremony.md`.
