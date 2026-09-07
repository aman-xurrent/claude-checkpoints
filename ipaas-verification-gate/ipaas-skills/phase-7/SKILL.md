---
name: phase-7
description: Phase 7 of phased development, the implementation. Replace every TODO marker with real code, fill the stubs, wire the feature, prove it with specs and the gate, verify it live, and take the PR out of draft. Started by the approval of the phase 6 commit.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

# Phase 7: Implementation

Start with the phase start in `.claude/skills/phase/ceremony.md` (N = 7).

## Work

1. Replace each `TODO(phase-4 #<request>)` marker with its implementation and remove the marker.
2. Fill every phase 3 stub with real code. Wire the feature through the running code paths as scoped.
3. Specs: 100 percent coverage of the new code (`AGENTS.md`), grouped by subject, explicit expectations that
   match their names. Run the spec-reviewer agent over them before the commit.
4. Declare the gate proof: `.claude/proof/declaration.json` names the spec example that proves the feature;
   it must fail with the code hunks reverted and pass with them. Read the findings before claiming done.
5. Verify live in the checks worktree: from the PR worktree `python3 ~/personal/scripts/gate/gate.py checks apply`,
   then `cd ~/work/ipaas_worktrees/checks && source .claude/worktree.env && cd platform && bin/dev` and open
   `http://127.0.0.1:$WEB_PORT` with the Chrome MCP; `rails runner` there for backend paths; `odiff` against
   the design where one was provided. Stop `bin/dev` and run `gate.py checks reset` before finalize.
   Screenshots go on a `review-assets/<n>-<slug>` orphan branch, never on the PR branch, and are linked
   from the PR description.
6. Keep the phase 6 assertions true: they still describe the illegal states of the implementation.
7. No new phase 2 to 4 contract change beyond what the phase 5 audit accepted. One that turns out to be
   needed is a finding for the PR description, then made here.

`agent_task_finalize --phase 7` fails while a `TODO(phase-4 #<request>)` marker or a stub body remains,
and on rubocop, yarn, spec, proof or reference failures.

## Done criteria

- [ ] Branch is on the latest `origin/main`.
- [ ] Every phase 3 stub has a real implementation.
- [ ] Every phase 4 marker is implemented and removed.
- [ ] The feature is wired through the running code paths as scoped and works end to end.
- [ ] No unapproved phase 2 to 4 contract change was needed.
- [ ] Phase 6 assertions still hold.
- [ ] Specs reviewed by the spec-reviewer agent; `agent_task_finalize --phase 7` exits 0 with the proof `pass`.
- [ ] Live check done and recorded with screenshots in the PR description.
- [ ] PR taken out of draft: `GH_HOST=git.4me.com gh pr ready <n>`.
- [ ] Xurrent request fields (review, requirement, design, risk analysis, risk level) and the test plan
      filled following steps 3 to 7 of `.claude/skills/create-pr/SKILL.md`.
- [ ] Phase 7 commit pushed, PR description updated, request in Review and assigned to me, internal note posted.
- [ ] The final PR description lists the completed work, the proof id, the live check, and the follow-ups.

Then the handoff in `ceremony.md`. From here the normal review and `/check-ci` flow applies.
