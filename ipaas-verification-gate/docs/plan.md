# ipaas verification gates

Goal: stop Claude declaring a task complete when the change missed a call site, missed
a spec, or rests on a spec that is flaky or passes with the fix reverted.

Status: Phases A, B, C built and self-tested 2026-09-05/06. Phase D: seed 11 clean run = 4481 examples, 1 failure (RunbookCloner hashed_credential contrast: IntermediateKeyProvider.memcache survives transaction rollback; main is masked by before(:all)-leaked key rows dated today; fix = clear both key caches per example, proven 18/18 via -r). Seed 22: 4481 examples, 9 failures, all Errno::ENOENT on a folder-layout debug_connector path kept by the global connector registry after reset_demo_repositories; culprit chain traced (ConnectorLoader.connector_cache digest key aliases lib/ and checkout paths; job_state_read_isolation_spec.rb:65 promotes the cached copy). Blanket cache clear breaks solution_loader_degrade_spec; path-in-key breaks two guard specs (cross-path sharing is designed); correct fix is in ConnectorImporter#connector_source_location (exist check + canonical lib/connectors fallback): validated, full seed 22 = 4481 examples, 0 failures. Phase D closed 2026-09-06. Update 2026-09-07: finding 1 applied to a worktree with a proven regression example (decisions.md section 11); finding 2's consumer fix withdrawn because origin/main ffa78c4a already fixes the flake at the isolation layer; the red run now reverts spec infrastructure too (section 15). Seed 22 running. Gate now owns MySQL DBs, Redis, git server (ipaas-gate-git:23232), git namespace, generated routes. Written, not committed, nothing pushed. Ledger hook dropped: ctags(base) vs ctags(working) over the branch delta carries the same information.
Companion: ~/.claude/plans/ipaas-tool-verdicts.md (10 repos read at source level).

## Locked decisions

Enforcement
- Stop hook is advisory. It writes findings and a systemMessage. It never blocks.
- pre-push is the hard gate. It refuses a push whose range has a missing, pending, or
  failing proof, or non-spec hunks with no declaration.
- An unprovable reference list never blocks anything. It is recorded three ways:
  my final message, a commit trailer (prepare-commit-msg, non-blocking), and a PR
  description section. Trailer and PR section are stamped from the findings file by a
  script, not written by me.
- Residual: this changes what I report, not what I can say. Blocking the claim itself
  needs the forced continuation that was declined.

Timing
- Reference gates run once at Stop against the accumulated diff.
- A silent PostToolUse hook on Edit|Write records removed or renamed definitions to a
  per-turn ledger, because old_string/new_string carry what a net diff destroys.

Spec binding
- I must write .claude/proof/declaration.json naming the spec example (path plus exact
  description string, never a line number) that proves the change. Naming nothing is a
  finding.
- The prover verifies the claim: revert non-spec hunks, run the example, require FAIL.
  Restore, require PASS. Passing both ways is vacuous.
- In platform, coverage cross-checks the declaration: did the named example execute the
  changed lines. simplecov is already on; Coverage entries are hashes (branch enabled).

Prover launch (fought, adjudicated)
- The HARNESS launches the prover. PostToolUse matched on the declaration path spawns a
  detached `claude -p --agent revert-prover`. The hook exits in under a second; the
  60s hook timeout binds the hook, not the child.
- I never launch a prover. Before claiming done I read findings, waiting up to 30s for
  a unit-tier proof. System-tier proofs report "pending", never "done".
- A UserPromptSubmit hook prints findings newer than last shown, so a slow failure is
  the first thing I see on your next message.
- The hook snapshots `git diff` to .claude/proof/<id>.patch at declaration time. The
  prover works from the patch. Later edits and the dirty tree cannot race it.

Isolation (decided on facts, no safe alternative)
- Main tree has 9 dirty files, 43 stashes, 8 live worktrees. The proof never stashes
  or edits it.
- Persistent proof worktree at ~/work/ipaas_worktrees/gate, reset to HEAD then patch
  applied per run. node_modules and generated routes installed once.
- Own database: DB_WRITER_DATABASE=ipaas_gate_test (source of truth today is
  platform/.env.test.local:5). rails_helper.rb:21 maintain_test_schema! handles drift.

Reference layers (all together; correctness over count)
1 recall      ripgrep over tracked files. Respects .gitignore. Never walks
              .claude/worktrees (4077 stray .rb files live there).
2 classify    universal-ctags: definition vs use, enclosing class. NOT INSTALLED yet.
3 precision   serena start-project-server, one daemon, three projects, three ruby-lsp.
              Health gate: an answer counts only if the daemon reports indexing
              complete; otherwise verdict is Enumerated, never Unused. Cross-project
              references are structurally impossible via LSP; layer 1 covers them.
4 unprovable  ast-grep shape rules (dispatch with a non-literal name, const_get,
              constantize, interpolated string feeding dispatch, method_missing,
              define_method/eval family)
              UNION full-minus-refs (name appears only inside a string literal).
              Every hit reported with file:line.
5 verdict     Unused | Enumerated | Used. Enumerated = looked at everything visible,
              here are the N sites we cannot see through.

Flakiness
- Add --order random to platform/.rspec. Prover repeats the named example under N
  seeds and requires identical results. Fallout across 284 never-randomized files is
  its own cleanup stream and does not block daily work.

Scope
- Everything in the repo: platform, connector, connector-sdk, ci, TypeScript.

## Build order (proposed)

A. Prover infrastructure: proof worktree + database, declaration schema,
   revert-prover agent, findings file, PostToolUse launcher, UserPromptSubmit
   surfacer, pre-push gate, prepare-commit-msg trailer. This is the half no tool
   covers and the half that actually stops a false done.
B. Reference gate: rg + ctags + ast-grep + full-minus-refs + verdict, run from Stop.
C. Serena daemon with health gate, folded into B's precision layer.
D. Random ordering + cleanup stream, in parallel with A and B.

## Measured facts
- Unit spec: 87 examples, 4.33s boot + 0.78s run = 5s. Revert proof about 10s.
- System specs: 71 Capybara files. Need `rake js:routes:typescript` then Vite build
  first (platform/AGENTS.md item 8). Unmeasured. System tier is async by design.
- Dynamic dispatch: 654 `send`, 615 in specs, 39 in app. About 73 app sites total,
  clustered in connector/lib/ipaas/connector/dsl/*.rb.
- Hook slots: only pre-commit exists (symlink to ~/personal/scripts/pre-commit, shared,
  untouched). prepare-commit-msg and pre-push are free. No core.hooksPath. All 8
  worktrees share .git/hooks, so one hook covers all.
- spec-reviewer.md forbids running any test runner. The prover is a new agent.

## Declared limits
- Ruby reference completeness cannot be proven by anything. Best answer is Enumerated.
- ERB views are unreachable by ast-grep. A declared hole in layer 4.
- A tautological spec that asserts the implementation detail passes the revert proof.
- ast-grep silent-pass traps: a rule with no `kind` is dropped with a clean exit; a
  pattern that fails to parse matches everything. Startup self-test against a
  known-positive fixture is mandatory.

## Open
- Sourcegraph container on localhost:7080 is orphaned by the pivot, restarts on boot,
  telemetry block dies on restart. Teardown: docker rm -f sourcegraph && rm -rf
  ~/.sourcegraph. Your call.
