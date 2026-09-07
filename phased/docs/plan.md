# ipaas phase loop: 7-phase development as the default Claude Code workflow

Status: planned 2026-09-07 after orientation, eight user answers, and four blind debates. Step A built the same day (see `ipaas-verification-gate/docs/decisions.md` section 16); steps B to D not started.
Source of the phases: `~/personal/prime-agent/phases/001-007.md` (the Cursor pipeline that read them is retired).
Home of the daemon and templates: `~/personal/claude-checkpoints` (github.com/aman-xurrent/claude-checkpoints).
Home of the ipaas skills: `~/work/ipaas/.claude/skills/` (committed, team-visible).

## 1. What the loop does

1. In an ipaas session you state a request or requirement. Every code task starts at phase 1 unless you say `quick`.
2. Phase 1 (discovery) ends with an empty commit carrying `Phase: 1`, a draft PR whose description holds the
   discovery notes and the scratch plan, the request assigned to you in the Review column, and one internal note
   `Phase 1: <pr link>`.
3. You review on GitHub. Unresolved review threads come first: the daemon tells the session to address every
   thread, push, and hand the request back to you. When your APPROVED review is newer than the last commit and the
   handoff, and zero threads are unresolved, the daemon starts the next phase in the same tmux Claude window if it
   is alive, else in a new one.
4. Phases 2 to 7 repeat that. After phase 7 the PR leaves draft and the normal team review starts (`check-ci`).
5. The PR is the record. No notes files enter the repository.

## 2. Locked decisions

Source is `user` (your answer) or `adjudicated` (blind debate, my ruling with the reason).

| # | Decision | Source |
|---|----------|--------|
| 1 | Event loop advanced by your GitHub approval on the latest commit, zero unresolved threads; comments addressed first. | user |
| 2 | Every ipaas code task starts at phase 1; opt out per task with the word `quick`. | user |
| 3 | Commits carry a `Phase: N` trailer. Pre-push lets phases 2, 3, 4, 5 through with the reference gate only. Phases 6 and 7 need a passing proof. Correction to the option you chose: phase 5's only commit is the phase 2 to 4 gaps it found (structures, stubs, TODOs), so it has nothing provable; phase 6 adds assertions, which a spec can trip, so it does. | user, adjusted |
| 4 | Phase skills and `agent_task_finalize` are committed in ipaas under `.claude/`. | user |
| 5 | A new small daemon in claude-checkpoints runs the loop. | user |
| 6 | No knowledge files. Your phase reviews and the final code review are the record. Phase 1 notes and the phase 5 audit go into the PR description. | user |
| 7 | `agent_task_finalize` runs the full gate: rubocop on changed Ruby, `yarn check` and lint on changed JS, specs for changed files, the revert proof for the declared example, the reference verdict, and phase shape checks. Non-zero exit on any failure. | user |
| 8 | Phase 6 assertions follow NASA Power of 10 rule 5 in Ruby and TypeScript form. No external guide. | user |
| 9 | Cursor pipeline retired. The phase files can be reshaped freely. | user |
| 10 | Daemon is standalone Python (`phased/`) next to `gate.py`, sharing no code with ghmention. Reason: ghmention is not in git and lives on one machine, so `install.sh` cannot depend on it; the approval decision must be a pure, tested function; the two daemons have different lifecycles. Adopted from the losing side: phased checks ghmention's trigger directory before typing into a window a live `@claude` run owns, and waits. | adjudicated |
| 11 | AMENDED 2026-09-07 by the user: one permanently slotted checks worktree (`gate.py setup-checks`, `checks apply | reset`) replaces the throwaway worktree; it takes the branch state for finalize, phase 5 and the phase 7 live check and returns to a clean `origin/main` after every use. Original ruling: phase 5 explores in a throwaway worktree on a temporary branch with its own slot, deleted afterwards. Reason: deleting a worktree does not depend on a complete revert list; exploratory migrations never touch the PR slot's database; an interruption at any moment leaves the PR worktree untouched. Adopted from the losing side: the exploratory diff is saved as a patch in the daemon's state directory and linked from the audit, and the PR worktree must show `git status --porcelain` empty before the phase commits. If no slot is free, phase 5 waits and says so; it never uses the gate worktree. | adjudicated |
| 12 | No automatic AI review before handoff. You are the reviewer. The phase ends with the done-criteria checklist in the PR, the mechanical gates, and for phases 6 and 7 the spec-reviewer (your standing commit rule) and the gate proof. Reason: an LLM pass rewrites the step before you see it, and in phases 2 to 4 it has nothing real to find, so it invents findings and pulls logic in early. Adopted from the losing side: the closed list of phase 2 to 4 defects becomes mechanical shape checks in `agent_task_finalize` (allowed paths for phase 2, `raise NotImplementedError` bodies for phase 3 via ast-grep, TODO-only added lines for phase 4). `@claude` on the PR still summons the CI reviewer when you want it. | adjudicated |
| 13 | A local state file per PR is the source of truth; GitHub is the event feed; a `phase:N` label is written as a mirror and never read. Reason: GitHub cannot record "consumed", a phase may end without a commit (approval must be newer than the handoff, not only the last commit), the window id and session id live nowhere else, and membership must be explicit (kickoff creates the file). Adopted from the losing side: every tick re-reads facts from GitHub, writes are temp-file plus rename with a revision check, and `phased adopt --pr N --phase N` rebuilds a lost file. | adjudicated |

## 3. Components

### 3.1 ipaas (committed)

- `.claude/skills/phase/SKILL.md`: kickoff. Takes the request id or a requirement. Runs `/work-on` (branch, assignment,
  status), renames the tmux window to `pr<N>` once the PR exists, registers the PR with `phased register`, then runs
  phase 1. `argument-hint: "<request id | requirement>"`.
- `.claude/skills/phase-1/` to `phase-7/`: one skill per phase, body from prime-agent with these changes:
  `project_conventions.md` becomes `AGENTS.md` and the sub-project `AGENTS.md`; `./agent_task_finalize` becomes
  `.claude/bin/agent_task_finalize --phase N`; notes go into the PR description; the Xurrent ceremony uses
  `/commit`, `/create-pr`, and one `xurrent-api` PATCH (member = you, column = Review) plus one internal note;
  phase 5 follows the throwaway-worktree procedure; phase 6 carries the rule 5 text with a Ruby and a TypeScript
  example; every phase ends by telling `phased` it handed off (`phased handoff --pr N --phase N`).
  Frontmatter: `name`, `description`, `paths` (kept per phase), `disable-model-invocation: true` for 2 to 7 so only
  the daemon or you start them.
- `.claude/bin/agent_task_finalize`: bash wrapper that calls `gate.py finalize --phase N`. Exit codes: 0 pass,
  1 a gate failed, 2 a shape check failed. Prints one line per check.
- `CLAUDE.local.md` (yours, uncommitted): a "Phased development" section: every code task starts at `/phase`
  unless the prompt contains `quick`; never skip a phase; one commit per phase with the `Phase: N` trailer.
- `.claude/settings.local.json`: the existing UserPromptSubmit hook (`gate.py surface`) also injects the phase
  protocol line when the branch is registered with phased.

### 3.2 claude-checkpoints (pushed)

- `ipaas-verification-gate/gate.py`: `finalize` subcommand (runs rubocop, yarn check and lint, specs for changed
  files, the proof, the references, the shape checks); `prepare-commit-msg` stamps `Phase: N` from
  `.claude/proof/phase` (written by the phase skill); `pre-push` exempts phases 2 to 5 from the proof requirement
  and still demands a `Used`-free reference verdict.
- `phased/` (Python 3, stdlib only):
  - `main.py`: 60 s loop, one GraphQL call per repository (`reviewDecision`, `isDraft`, `latestReviews` with
    author, state, submittedAt, commit; `reviewThreads` with isResolved; `commits(last:1)` committedDate).
  - `decide.py`: pure `decide(state, facts) -> Action | None`. Tested with pytest fixtures.
  - `state.py`: `~/.local/state/phased/<owner>__<repo>/pr-<n>/state.json` (schema, revision, phase, status
    `working | waiting_approval | addressing_comments | done`, phase_commit_sha, handoff_at,
    consumed_approval_ids, window_id), `history.jsonl`, `phase-<N>/brief.md`, `phase-<N>/exploratory.patch`.
  - `tmux.py`: find window `pr<N>` in the repo's session with a live Claude, `send-keys -l` one line pointing at
    the brief, else `new-window` running `claude -- "$(cat brief)"`. Refuses to type while a ghmention trigger
    for the same PR is at a non-terminal stage.
  - `xurrent.py`: subprocess to `<repo>/.claude/bin/xurrent-api` for the PATCH and the note.
  - CLI: `phased register|handoff|status|adopt|pause|resume|logs`.
  - `launchd/com.aman.phased.plist`, `~/.config/phased/config.json` (repos, tmux session names, your login,
    poll seconds), wired into `install.sh`.
- Templates: `phases/*.md` with a `paths` block marked per repository, so another repository gets its own copy.
- Docs: README section, `docs/decisions.md` section 16 (this table), `docs/phase-loop.md` (state machine).

## 4. State machine

```
register ──> working(1) ──handoff──> waiting_approval(1)
waiting_approval(N): each tick
   unresolved threads > 0 and new thread since handoff ──> addressing_comments(N) [send: address threads, push, handoff]
   your APPROVED review, submittedAt > max(phase_commit_date, handoff_at), not consumed, threads == 0
                                                       ──> working(N+1) [consume approval id, send: start phase N+1]
   N == 7 and approved ──> done [PR ready for review, label phase:done]
addressing_comments(N) ──handoff──> waiting_approval(N)
```

Actions are idempotent on (pr, phase, approval id, head sha). A restart replays nothing: facts are re-read, the
state file says what was consumed. Teammate approvals are ignored (author login filter).

## 5. Build order

A. ipaas skills, `agent_task_finalize`, trailer and pre-push changes, CLAUDE.local.md, hook injection. Verify:
   run `/phase` on a throwaway request in a worktree through phase 1 and phase 2 by hand; pre-push accepts a
   phase 2 commit and refuses a phase 7 commit without proof.
B. `phased` daemon with tests for `decide`, `install.sh` step, LaunchAgent. Verify: `phased --once --dry-run`
   against the phase 2 PR prints the right action; approve on GitHub, watch phase 3 start in the same window.
C. Full loop on one small real request through phase 7. Verify: one draft PR, seven `Phase: N` commits at most,
   every handoff moved the request, no notes files, final PR marked ready.
D. Hardening: collision test with a live `@claude` run on the same PR, `adopt` after deleting a state file,
   behavior when a slot is missing for phase 5, session compaction mid-phase.

## 6. Open items

- Verified 2026-09-07 on git.4me.com (ipaas PRs 1010 and 1015): `isDraft`, `reviewDecision`, `labels`,
  `commits(last:1){commit{oid committedDate}}`, `latestReviews{author{login} state submittedAt commit{oid}}`,
  and `reviewThreads{totalCount nodes{isResolved isOutdated}}` all resolve; one query costs 1 rate-limit point
  (14855 remaining). `latestReviews` carries the head commit the review was submitted on, so the decision uses
  `review.commit.oid == head.oid` plus `submittedAt > handoff_at` plus the consumed id. `reviewDecision` is null
  without branch protection reviews, so it is never used; only the author-filtered review counts. Outdated but
  unresolved threads exist on real PRs (13 on 1010): the rule counts a thread as blocking only when
  `isResolved` is false and `isOutdated` is false, matching the repo's own review-thread query.
- Unattended phases need permission-free commit and push: `.claude/settings.local.json` in the PR worktree must
  carry the same allow rules as the main clone (`setup-worktree` copies `settings.json`, not `settings.local.json`).
- A phase that spans hours may compact the session; the brief must be enough to resume from disk.
- ghmention's libraries are not under version control. Out of scope here; worth its own move later.
