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
rows are gone, then round-trips in the example. Proof `20260906T195500Z-49743da06747`: red fails
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
