# Decisions

The record of what was decided, why, and what was later retracted. Dates are 2026-09-05 to
2026-09-07. Every decision below was made after orientation and, where a real fork existed,
after two blind agents argued opposite sides and the result was adjudicated.

## 1. The problem is obligation, not capability

Claude can already find references with grep. The failure the user described is Claude
declaring done without proof. Only harness-run hooks are unskippable, so the gate lives in
hooks, not in `CLAUDE.md` prose. The single mechanical proof that a spec is not vacuous is:
revert the change, run the spec, require it to fail.

## 2. Enforcement: advisory at Stop, hard block at pre-push

User choice: loud warning, no deadlock. Adjudication of the forced-continuation fork: the Stop
hook never blocks (a blocked stop would also make the user's `notify-stop.sh` announce
"Session complete" over unfinished work, and forced continuation fails open at the harness
block cap). The hard gate moved to a new `pre-push`, because the user's own rules define done
as pushed and no `pre-push` hook existed. `pre-commit` was left alone: it is a symlink to
shared infrastructure.

Residual, stated at the time: this changes what Claude reports, not what Claude can say.

## 3. Timing: gates at Stop, on a coherent diff

Three of the four checks cannot run mid-turn: flakiness needs identical code, the revert proof
needs a revertible diff, and "no spec update" is false on every source edit because the spec
edit comes after. The per-edit ledger the plan first called for was dropped during Phase B:
comparing `ctags` output of the base version against the working version yields the same
removed and renamed definitions, and using the merge-base with `origin/main` also covers work
already committed on the branch.

## 4. Spec binding: declaration first, coverage as a check

Claude must name the example that proves the change. Naming nothing is itself a finding.
Coverage cannot select on its own (a line executing is not a line asserted on) but never
under-nominates, so it is sound as a cross-check. The cross-check is not built yet.

## 5. Who launches the prover: the harness

Fork fought and adjudicated. A verification step the model can decline is not a verification
step; a `CLAUDE.md` line is weighed against everything else in context and loses after
compaction. So `PostToolUse` launches the prover the instant the declaration is written, and
Claude never launches one itself. The agent side kept one thing: for a unit proof Claude reads
the findings before writing the message that says done, and a `UserPromptSubmit` hook injects
any slow result at the start of the next turn.

Two overrides of the winning argument: no `decision: block` at Stop (it would violate
decision 2), and a persistent proof worktree instead of one per proof (a fresh worktree has no
`node_modules` and no generated routes).

## 6. Unprovable never blocks

User choice. `Enumerated` reaches the user through the final message, a commit trailer stamped
by `prepare-commit-msg`, and the `pr-section` block. Trailers and the PR block are produced by
a script, never paraphrased by Claude, because a model that overclaims "tests pass" will
overclaim "the prover confirmed".

## 7. Reference finding: recall from text, precision only where it is real

Ten tools were read at source level (`tool-verdicts.md`). Kept: ripgrep for recall,
universal-ctags for definitions, ast-grep for unprovability, Serena for pre-change navigation.
Serena is not in the gate verdict: on `GitRepo#apply_bundle` LSP returned the 3 real
invocations and text search returned those plus 15 spec mocks that a rename must also update.
Both are right about different things; the verdict needs recall.

## 8. Isolation: every shared resource gets a gate-specific instance

Learned in order, each from a real collision:

- MySQL: own databases. A first attempt inherited `.env.test.local` from the main worktree
  and ran the queue schema against the user's `ipaas_queue_test`, dropping three tables. They
  were rebuilt with the project's own `db:test:prepare`.
- Redis: own container, because every slot of the user's `setup-worktree` allocation was
  taken.
- Git server: own soft-serve container. A separate namespace was not enough; the server's
  SQLite refused concurrent writers and the user's specs failed with `SQLITE_BUSY`.
- Generated routes: regenerated on every reset, after 49 request specs answered 500 without
  them.
- Database state: `db:test:prepare` before every randomized run, because `before(:all)` hooks
  leak committed rows.

Operating rule, learned the hard way: only `gate.py rspec` may run specs in the gate
worktree. Two ad-hoc probes run beside a suite corrupted a clean run into 807 failures.

## 9. Gate epoch

Claude-authored commits from before 2026-09-06 are exempt from the proof requirement and the
nag. Without this the user's existing branches would be refused at push.

## 10. Sourcegraph and Sourcebot

Sourcegraph was rejected: not open source since 2023, the core repository private since
2024, telemetry that cannot be disabled without an enterprise license tag. A free instance
does survive a full airgap (proven with a 40 minute soak), but it offers nothing over ripgrep
for this problem. Torn down. Sourcebot's license was declared irrelevant by the user for
local use; its `POST /api/search` is ungated and its N+1 exhaustiveness trick is the best idea
in it, but neither was needed because ripgrep is not truncated.

## 11. Phase D findings, with both retractions

Seed 11: 4481 examples, 1 failure. Seed 22: 4481 examples, 9 failures.

**Finding 1.** `IntermediateKeyProvider.memcache`, a class-level `MemoryStore`, keeps an
encryption key whose row the transaction rollback removed; the next encrypt-then-decrypt
fails with `Key permanently revoked`. Main passes only because `spec_git_helper.rb:10` leaks
committed key rows in a `before(:all)` and they carry today's date suffix. Fix: clear both key
caches before each example. Proven 18/18.

Applied 2026-09-07 in the worktree `~/work/ipaas_worktrees/spec-cache-leaks` (branch
`worktree/spec-cache-leaks`, base `origin/main` 05688096), written and not committed. The
regression example in `encryptor_provider_spec.rb` ('starts every example with empty key caches')
encrypts in a `before(:context)` inside a transaction it rolls back, so both caches hold keys whose
rows are gone, then round-trips in the example. Proof `20260906T200221Z-ed91c1a796ef`: red fails
with `Key permanently revoked`, green passes, seeds 11/22/33 green. The connector gem's own
`spec/spec_helper.rb:21-22` already clears both caches per example; the platform helper now matches.

Retraction: the first diagnosis blamed `SystemKeyProvider.memcache` alone and was written to
memory before it was proven. The proof failed and the entry was corrected.

**Finding 2.** `ConnectorLoader.connector_cache` is keyed by content, never by path, so
`lib/connectors/debug_connector.rb` and a checkout copy share a slot. `load_default_connectors`
promotes a checkout copy into the registry, `reset_demo_repositories` deletes that folder, and
`ConnectorImporter#connector_source_location` dereferences the dead path. Nine examples fail.

Retraction: the first fix put the path into the cache key. It removed the nine failures but
broke two specs that guard cross-path sharing as a designed feature. The second fix sat in the
consumer: `ConnectorImporter#connector_source_location` checks that the path exists and falls back
to the copy under `lib/connectors`. It was validated (85/85 on the guard and degrade specs, then
4481 examples, 0 failures on seed 22) and withdrawn on 2026-09-07.

Why withdrawn: the diagnosis ran against `5a9614b2`. `origin/main` had already fixed the flake at
the isolation layer in `ffa78c4a`: `spec/unit/support/default_connector_state_isolation.rb`,
wrapped around every example by `rails_helper.rb:39-44`, evicts every cache entry whose source
file vanished and reinstates the uuid registries. On that base the fallback is redundant for the
flake, it breaks `default_connector_state_isolation_spec.rb:99`, which asserts the raw dead path
on purpose, and it carries a risk of its own: a lib copy with the same basename is not proven to
have the same content, so the fallback could copy a different connector silently where the current
code raises. The patch stays local in `hooks/` for the record. Lesson: diagnose on the merge-base
the fix will land on, and look upstream for the same fix before writing one.

A blanket `connector_cache.clear` per example also removes the nine failures but breaks
`solution_loader_degrade_spec.rb:237`, a timing spec that only holds with a warm cache.

## 12. What is deliberately not in this repository

The four ipaas-side files that came out of Phase D (two RSpec `-r` fixes, the diagnostic
trace hook, the `connector_importer` patch) are changes to ipaas, not gate code. They stay in
`~/personal/scripts/gate/hooks/` until applied to ipaas.

## 13. Portability

The repository carries the gate and nothing of the project under test: no database dumps,
no fixtures, no git-server contents, no env files, no keys. `install.sh` recreates the
infrastructure empty and is idempotent. The two paths the gate needs are environment
variables (`IPAAS_GATE_REPOSITORY`, `IPAAS_GATE_WORKTREE`); every other ipaas assumption sits
in the `PROJECT CONFIGURATION` block at the top of `gate.py`, in four named functions, and in
steps 2 to 4 of the installer, so adapting the gate to another repository is an edit to those
places and to the ast-grep rules for the language in question. The README lists each one.

## 14. Impact must be visible at the edit, not only at Stop

User request: Claude should use a robust tool for a change's impact, not raw grep, and
everything related to a change should be visible to it. The Stop-time reference gate already
reports removed definitions; it now also runs per edit. `PostToolUse` compares the definitions
in the old and new text with ctags and, on a removal or rename, injects the remaining
references (code first, then string literals, symbols, specs) and the dynamic-dispatch count
into Claude's context before the next edit. The earlier debate rejected per-edit *blocking*
because half-finished renames are normal; per-edit *visibility* has no such cost, and it is
what the user asked for. Serena stays the tool for exact call sites; the protocol names both.

## 15. The red run reverts spec infrastructure

`split_patch` keeps only the example files (`*_spec.rb`, `*.test.*`, `__tests__/`) in the red
run. `spec_helper.rb`, `rails_helper.rb`, `spec/support/**` and factories are reverted with the
code, because a hook or a helper is often the change under proof. Finding 1 is exactly that: two
lines in `spec_helper.rb`. Under the old rule, which treated everything under `spec/` as a spec
hunk, that proof returned `not_applicable`. Enforcement is unchanged: spec infrastructure owes no
`Proof-Id` on its own (`is_code_path`), and it stays out of the reference scan.

## 16. Phased development, step A

Built 2026-09-07 in the ipaas worktree `~/work/ipaas_worktrees/phase-loop` (branch `worktree/phase-loop`,
written and not committed) and in `gate.py`: the kickoff skill `/phase`, the shared `ceremony.md`, the
skills `/phase-1` to `/phase-7`, the wrapper `.claude/bin/agent_task_finalize`, the `finalize` subcommand,
the `Phase: N` trailer, the pre-push exemption, and the protocol line the prompt hook prints. Copies of the
ipaas files sit under `phased/ipaas-skills/` so another machine can install them.

Verified by hand in that worktree: the prompt hook prints the default-workflow line, stays silent on `quick`,
and names the active phase; a phase 2 commit gets `Phase: 2` and passes pre-push without a proof; a phase 7
Claude commit without a proof is refused; the phase 2 check rejects a file outside its locations; the phase
3 check rejects a method body that is not the stub; the phase 4 check rejects a code line and a four-line
reason; `finalize --phase 2` passes end to end in a worktree without a slot (routes generated under
`RAILS_ENV=test` with the main clone's env files).

Two rulings made while building. Shape checks compare against the newest branch commit that does not carry
the current `Phase: N` trailer, so an earlier, approved phase never fails a later phase's check. Lint, yarn,
specs and references stay branch-wide. And a phase session must run in a worktree with a slot: phases 5 to 7
need a database and the local instance, and a worktree without `.claude/settings.local.json` fires no hooks,
so the ceremony copies that file from the main clone at phase start.

Amendment 2026-09-07, from the user: not a throwaway worktree per run and not a slot-less PR worktree.
One permanently slotted checks worktree (`~/work/ipaas_worktrees/checks`, `gate.py setup-checks`) serves
finalize's rubocop, yarn and specs, phase 5's exploration and phase 7's live check. It takes the branch
state (`checks apply`: HEAD plus working tree, routes regenerated, databases prepared when migrations
changed), and returns to a clean `origin/main` after every use (`checks reset`; finalize resets on its own
in a `finally`). A branch that holds it blocks another branch until reset. The test database is reloaded
on reset when migrations were applied; the development database is rebuilt only with `--rebuild-dev-db`,
because that reseeds the demo data. The slot-less Rails boot that borrowed the main clone's env files was
removed. Verified: finalize through the checks worktree in 15 seconds, apply, refusal of a second branch,
reset to a clean `origin/main`.

## 17. Nothing of the workflow enters ipaas

Ruled by the user 2026-09-07 after step A: the phase skills and `agent_task_finalize` are not committed
to ipaas, and they must still work in every worktree. Solution: the files live in
`ipaas-verification-gate/ipaas-skills/` (live copy `~/personal/scripts/gate/ipaas-skills/`);
`gate.py link-worktree <path>` symlinks them into a worktree's `.claude/skills/` and `.claude/bin/`,
copies `CLAUDE.local.md` (from the gate directory) and `.claude/settings.local.json` (from the main clone)
when missing, and adds exclude entries to the shared `.git/info/exclude`, without a trailing slash because
git matches a symlink as a file. A local `post-checkout` git hook (`.git/hooks/post-checkout ->
gate/post-checkout`) runs it for every new worktree; the gate and checks worktrees are skipped. Claude Code
discovers symlinked project skills (verified with `claude -p` in a linked worktree). `install.sh` step 9a
installs the hook and links the main clone and every existing worktree. Verified: a fresh `git worktree add`
came up with the skills, the wrapper, the protocol and the hooks file, and `git status` shows none of it.

## 18. Step B: the phased daemon

Built 2026-09-07 at `~/personal/scripts/phased/` (mirrored in `phased/`): `decide.py` (pure, 13 unit
tests), `github.py` (one aliased GraphQL query per repository for the registered pull requests only, through
`gh`), `state.py` (temp file plus rename, revision check, history), `tmux.py` (ghmention's window
convention: `pr<n>` in the repository's session, `pane_current_command` liveness), `phased.py` (loop and
commands), a LaunchAgent, and the local skill `phase-comments`. The ceremony's `phased handoff --pr N
--phase P` registers the pull request on first use, records the worktree and the tmux window, renames the
window to `pr<n>`, and sets `waiting_approval`. Xurrent stays with the session (the ceremony), not the daemon.

Dry run against PR 988 with an adopted state: one GraphQL call (cost 1), the approval rule held with a
fresh handoff, and with the handoff moved to 2020 the daemon chose to address the unresolved threads and
would have opened a new window because no live `pr988` window existed. Installed: `~/.local/bin/phased`,
`~/Library/LaunchAgents/com.aman.phased.plist`, running with no registered pull request. Not yet exercised:
a real approval typing into a live window; the first phased request will show that.

Operating rule, learned twice now: never `rsync --delete` from the live gate directory into
`ipaas-verification-gate/`; the repository directory holds `docs/` that the live directory does not.

## 19. The model cannot skip the gate

The user saw a session push with `GATE_SKIP=1` and ruled: no skipping by the model; a skip is the user's act.
Three layers, all outside the model's reach in an ipaas session:

1. The gate honours no environment variable any more. Anything a process can set, the model can set. The
   only override is a one-shot token, `gate.py skip-once "<reason>"`, written from the user's own shell
   (`!` prefix, or a terminal), valid for one refused push within 15 minutes, recorded in
   `~/.local/state/gate/skips.log` with the reason and the violations it waved through.
2. Deny rules in every worktree's `.claude/settings.local.json`, merged by `link-worktree` and so present in
   every new worktree: `--no-verify` on push and commit, `git commit -n`, `core.hooksPath`, `skip-once`,
   `phased pause|resume`, `launchctl`, and Edit, Write and MultiEdit on the gate directory, the daemon
   directory, `.claude/proof/runs/**`, `.claude/proof/references/**`, the settings file itself, the token and
   the skips log. Deny rules beat allow rules in every mode, auto included, and a deny in the project scope
   beats an allow in the user scope.
3. The `guard` PreToolUse hook, registered by the same step for Bash, Edit, Write, MultiEdit and
   NotebookEdit. It reads the whole Bash command and denies it when a forbidden token appears anywhere
   (`--no-verify`, `hooksPath`, `.git/hooks`, `skip-once`, `.gate-skip-once`, `GATE_SKIP`, `send-pack`,
   `GIT_DIR=`, `settings.local.json`, `launchctl`), when `git commit -n` or a hooks config change is in it,
   or when a write-shaped command targets the protected paths. Gate invocations the protocol asks for
   (`references`, `checks`, `finalize`, `pr-section`, `link-worktree`, the wrapper, `phased handoff|status|
   logs|adopt`, `printf N > .claude/proof/phase`, reading a findings file) pass. Hook denies hold in every mode.

Verified: `GATE_SKIP=1` push refused; token push allowed once, the next refused; the guard denied nine
bypass shapes and allowed seven legitimate commands; every worktree carries 31 deny rules and the hook,
and a fresh `git worktree add` inherits them. What remains reachable is what the user does by hand.

## 20. Four holes an ipaas session found, 2026-09-08

A session working on `requests/82698379` reported that `pr-section` was refused to it and that the
reference verdict never ran. Both reports were right, and chasing them opened two more.

**The guard judged the whole command line.** `cd <worktree> && python3 .../gate.py pr-section` failed the
allowed-invocation test, because that test required the gate call to start the line, so the command fell
through to the write check and was denied. The guard now splits a compound line on `&&`, `||`, `;`, `|`
and newlines and judges each simple command on its own, ignoring leading environment assignments. A
redirection into a protected path is denied by target, not by substring. Re-tested: eleven shapes, four
allowed, seven denied, including every earlier bypass.

**The Claude trailer pattern was case-sensitive.** It matched `Co-Authored-By: Claude`, which the commit
skill writes, but not `Co-authored-by: Claude`, which is git's own convention and what an Opus session
wrote. That commit was therefore not a Claude commit to the gate: no Stop warning, no stamped trailers,
and `pre-push` let it through unproven. It is now `re.IGNORECASE`. Verified on four trailer variants and on
the real commit, which the gate now sees and, with a real remote sha, refuses for the missing `Proof-Id`.

**A proof measured the uncommitted diff.** Once the work was committed the same change looked empty, so a
passing proof was reported stale, and a proof declared after committing reverted nothing and came back
`vacuous`. `snapshot_patch` now measures the branch delta (merge-base with the upstream branch to the
working tree, committed work included) and `launch` resets the gate worktree to that merge-base. The checks
worktree keeps the working-tree patch, because it is already checked out at HEAD. Verified: the committed
change re-proved `pass` with a real red run, and Stop no longer calls it stale.

**Silence read as "did not run".** Stop said nothing when no definition was removed, and `pr-section`
printed `not computed` when no report existed. Stop now states the outcome either way, and `pr-section`
computes the report when it is missing or stale rather than reporting its own absence.

## 21. The fence writes Edit rules only

Claude Code printed a warning per rule at every ipaas session start: `MultiEdit(path)` matches no known
tool, and `Write(path)` is not consulted by file permission checks. Only `Edit(path)` rules take part, and
an Edit rule covers every file-editing tool. `fence_deny_rules` now emits one `Edit(...)` per protected
path, and `ensure_fence` removes the rules earlier versions wrote (the `Write` and `MultiEdit` variants and
the triple-slash absolute form) when it refreshes a worktree. Seventeen rules per worktree, none rejected.

The startup also reported a `SessionStart` hook error. It belongs to the `agent-skills` plugin, not to the
gate: its hook pasted a whole `SKILL.md` into a JSON string built by a shell heredoc, so the raw newlines
made the payload invalid. SessionStart adds plain stdout to the context, so the local copy at
`~/.claude/plugins/cache/addy-agent-skills/agent-skills/1.0.0/hooks/session-start.sh` now prints the file
as text, with the original kept as `.bak`. A plugin update will overwrite it.

## 22. Conversation comments are feedback, and discovery runs through two skills

The user left a conversation comment on PR 1027 asking for a different `RunbookPresenter` shape, and the
loop ignored it: `decide` read conversation comments only as approvals, and review threads only as feedback.
A comment on the conversation tab is how a reviewer asks for a change that belongs to no single line, so the
approver's comments now count as feedback unless the comment is the approval itself. Ones carrying `@claude`
are skipped, because those belong to ghmention and reading them would make the two daemons answer each
other. `AddressComments` carries both thread and comment ids, the brief renders both, and the state records
`seen_comment_ids` so a comment fires once. Twenty-seven decision tests.

Phase 1 changed with it, by the user's instruction: discovery reads every comment already on the pull
request and lists what each changes in the plan, or answers it with a reason, and the discovery content goes
through `/agent-skills:code-simplify` and `/edge-case-hunter` before the plan is written, with what
each returned named in the PR description. The plugin ships that skill as a slash command whose
directory is named `code-simplification`; Claude Code exposes both names, and the skills use
`code-simplify`, the one the user invokes. `phase-comments` answers a conversation comment with a
conversation comment and runs a skill a comment names.

Verified on PR 1027: the daemon typed the feedback into the live window `pr1027` at the next tick.

## 23. A session's pull request comments carry an identity

On PR 1027 the approver's comment and the session's answer looked the same: both say
"Aman Kumar (aman-kumar) commented", because the session posts with the account's token. The user asked for
what ghmention does: an identity header, the content collapsed, and no way for the model to post around it.

`.claude/bin/pr-comment` (linked from `ipaas-skills/pr-comment`) takes `--pr`, `--title`, `--body-file` and
an optional `--thread`, and posts a comment that opens with a header naming the phase and loop status, the
branch and head, the worktree, the tmux session, the request and the time, followed by the body inside a
`<details>` block that is collapsed by default. It reads all of that itself from git, tmux and the loop's
state file, so a caller passes only the content.

The guard denies `gh pr comment`, `gh pr review`, `gh pr-review reply`, the comment mutations
(`addPullRequestReviewThreadReply`, `addPullRequestReview`, `addComment`) and a POST to
`issues/<n>/comments` unless the command runs the wrapper. Resolving a thread, editing the pull request
description, reading comments and `gh pr ready` stay direct: none of them posts a comment. Verified on ten
command shapes, five denied and five allowed.

## 24. Phases 1 to 5 skip CI

Those phases change structures, contracts, markers and an audit, so the full matrix has nothing to test and
burns eight jobs per phase. `prepare-commit-msg` now writes `[skip ci]` into the body of a phase 1 to 5
commit, above the trailer block and above git's comment block, so the trailers stay one block and a verbose
commit template cannot cut the keyword off. Any of the five spellings already present is left alone
(`[skip ci]`, `[ci skip]`, `[no ci]`, `[skip actions]`, `[actions skip]`, in the subject or the body).

Phases 6 and 7 must run CI, so `pre-push` refuses a commit of those phases whose message carries any of the
five keywords, naming the one it found. Verified on four message shapes, including a verbose template, and
on real phase 2, 4 and 7 commits.

## 25. pre-push audits what the push adds, not a range

A session on PR 1027 was refused its phase 2 push over two commits it never wrote, both already on
`origin/main`. Its diagnosis was right: `pre_push` audited `<old remote tip>..<new tip>`, and the ceremony
had it rebase because the branch was four commits behind. After a rebase the old tip is no longer an
ancestor, so that range fills with the upstream commits the branch was rebased onto. Every phase after a
rebase would have been blocked the same way, and the only ways out the session could see were a user skip
token or undoing the rebase.

`pushed_revisions` now asks what the push actually adds: everything reachable from the new tip and from no
remote-tracking ref, minus the tip being replaced (`rev-list <local> --not --remotes [<remote tip>]`). That
is the question the gate always meant to ask, and it is what the branch already did for a new branch, where
no remote tip exists. `commit_violations` also returns early for a commit already reachable from the
upstream branch, which covers a stale remote-tracking ref.

Reproduced first, then verified: a branch rebased onto four newer commits audited five commits before and
one after, and the session's own case, a phase 2 commit force-pushed after a rebase, now passes. A commit
that genuinely owes a proof is still refused.

## 26. Every gate call is on record

The gate ran inside hooks and left no evidence. When a hook misfired, the only account of it was whatever
the session chose to repeat, which is the account of the party under review. Judging where the gate
underperforms needs the calls themselves.

`traced_main` now wraps `main`. It reads the hook payload from standard input once and hands it back to the
subcommand, tees standard output and standard error, and writes one JSON line per call to
`~/.local/state/gate/trace/<date>.jsonl`: time, pid, subcommand, arguments, working directory, session id,
hook event, tool name, duration, exit code, the payload, the output, and a traceback when one is raised.
Text is clipped at 4000 characters and files older than 14 days are deleted.

Two rules keep the trace from becoming a gate of its own. `record_trace` swallows every exception, so a
broken trace can never break a hook. Standard input is read only for the five hook subcommands (`launch`,
`stop`, `surface`, `guard`, `pre-push`); a command the model runs from a shell keeps its own stream and
cannot block on a pipe that stays open.

`gate.py trace` reads it back: `--sessions` for one line per session with call counts and failures,
`--session ID`, `--command NAME`, `--failures`, `--day`, `--last N`, and `--full` for the whole payload and
answer. The directory sits under `~/.local/state/gate/`, which the fence already denies writing to, so the
model can read its own record and cannot edit it.

## 27. tmux keeps the shape, phased keeps the conversation

tmux-resurrect and tmux-continuum now persist the terminal side: every session, window, window name,
layout and working directory, saved every five minutes and restored when the tmux server starts.

The one rule that matters is what they must not restore. `claude` is deliberately absent from
`@resurrect-processes`. A restored Claude is a fresh process with no conversation and no brief, and the
phased daemon would read that window as a healthy session and leave the pull request alone, which is
worse than an empty window. Restoring the shape is resurrect's job; restoring the conversation is the
daemon's, through `claude --resume <id>`. Verified: a restore brought back every window, including
`pr1027` with its worktree, and every Claude pane came back as a plain shell.

Two daemon rules follow. A restart only joins a tmux server that already runs, never creates one: after a
reboot the daemon starts long before the user opens a terminal, and creating the server there would fire
continuum's restore against a server the daemon made, whose next auto-save would then overwrite the good
save. And a restart reuses the window resurrect brought back (`respawn-pane -k` into the window of that
name) instead of opening a second window called `pr1027`.

The gate side of a restart is the marker that outlives its owner. A prover killed mid-run left its findings
`pending` for ever, so every reader kept saying the proof was still running; a session holding the checks
worktree left a marker that refused every other branch until someone reset it by hand. Both are settled by
the boot time, with the process id added for a prover killed without a restart. The checks marker cannot
use a process id at all, because the worktree is held across several short gate calls.

The tmux configuration lives in `~/.tmux.conf`, outside both repositories, so the ownership rule is
written down here and in `phased/README.md`. `phased/tmux-resurrect-prune` keeps the last 120 saves,
because resurrect never deletes one.

## 28. The proof tier runs vitest as well as rspec

A session reported that its push was blocked with no way out: `commit_violations` counts `.ts` and `.tsx`
as code and demands a `Proof-Id` (gate.py:1084, :1096), while the prover only ran `bundle exec rspec`.
A TypeScript-only change could not produce a passing proof, and only `STATUS_PASS` satisfies the push
check (gate.py:1101), so `STATUS_DEFERRED` was no way round it either. The gate had a dead end, and its
only exit was the user's skip token. That is a gate that punishes a whole language, so the tier was built
rather than the change waved through.

`prove_one` now picks the runner from the test file: `is_javascript_test` (under `platform/app/javascript/`,
named as a test, with a JS or TS suffix) selects `vitest`, everything else stays `rspec`. The revert proof
itself is unchanged, because it never depended on the runner: revert the code hunks, run the declared
example, require red; restore, require green; then repeat.

Three details of vitest 4 that the proof logic depends on, each measured rather than assumed:

- `-t` filters by substring and reports every other test in the file as `skipped`, so the "exactly one
  example" rule counts the assertions that actually ran, never `numTotalTests`.
- A file that fails to load reports a failed suite with **no** assertion result at all. That is the
  analogue of rspec's errors outside examples, and it is the expected red for a change that adds an
  export the test imports. Without mapping it, a load error would read as zero failures and the red run
  would look vacuous.
- `--sequence.shuffle` with `--sequence.seed` changes nothing the JSON report can show: the report lists
  results in declared order under every seed and both flag spellings. So the flake tier repeats the file
  three times in its declared order and records `shuffled: false` in the findings. It is weaker than the
  rspec tier and says so, rather than claiming a random order that never ran.

Verified end to end on a real change (days in `formatDuration`, with the example that proves it): the
proof came back `pass` in 20 seconds with `runner: vitest`, red 1 failure and green 0, and `pre-push`
accepted the commit stamped `Proof-Status: pass`. The teeth still bite: an existing example that does not
depend on the change came back `vacuous`, and an example that does not exist came back
`ambiguous_declaration`.

The trace added in decision 26 settled what had actually happened: every `pre-push` call in the record
exited 0, including the two real pushes that ran while the report was being written. No push had been
refused. The change sits uncommitted in its worktree, so the refusal was still ahead of it, correctly
predicted from the code.
