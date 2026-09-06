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

Retraction: the first diagnosis blamed `SystemKeyProvider.memcache` alone and was written to
memory before it was proven. The proof failed and the entry was corrected.

**Finding 2.** `ConnectorLoader.connector_cache` is keyed by content, never by path, so
`lib/connectors/debug_connector.rb` and a checkout copy share a slot. `load_default_connectors`
promotes a checkout copy into the registry, `reset_demo_repositories` deletes that folder, and
`ConnectorImporter#connector_source_location` dereferences the dead path. Nine examples fail.

Retraction: the first fix put the path into the cache key. It removed the nine failures but
broke two specs that guard cross-path sharing as a designed feature. The correct fix is in the
consumer: check the path exists, fall back to the canonical copy under `lib/connectors`.
Validated: 85/85 on the guard and degrade specs, then 4481 examples, 0 failures on seed 22.

A blanket `connector_cache.clear` per example also removes the nine failures but breaks
`solution_loader_degrade_spec.rb:237`, a timing spec that only holds with a warm cache.

## 12. What is deliberately not in this repository

The four ipaas-side files that came out of Phase D (two RSpec `-r` fixes, the diagnostic
trace hook, the `connector_importer` patch) are changes to ipaas, not gate code. They stay in
`~/personal/scripts/gate/hooks/` until applied to ipaas.
