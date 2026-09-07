# Verification gate (local, not committed)

Every change to non-spec code in this repository must be proven before it is reported. Spec
helpers and support files count as code for the proof: the red run reverts them too.

1. Write `.claude/proof/declaration.json` in the worktree you are editing:
   `{"proofs":[{"spec_file":"platform/spec/unit/foo_spec.rb","example":"exact example description"}]}`
   Use the description string, never a line number. It must select exactly one example.
2. Writing that file launches a detached prover. Read `.claude/proof/runs/<id>/findings.json`.
   A unit-tier proof finishes in about 30 seconds. Wait for it.
3. Report the status verbatim. `pass` may be called done. `pending` is "proof pending".
   `vacuous`, `fails_with_change`, `flaky`, `ambiguous_declaration` mean the change is not done.
4. If you edit again after a proof, redeclare. The Stop hook will call the old proof stale.
5. Never run the proof yourself, never stash or edit the main tree to test a revert. The prover
   uses `~/work/ipaas_worktrees/gate` with its own databases.

`pre-push` refuses a Claude commit that touches code without a fresh, passing `Proof-Id` trailer.
There is no skip for you. No environment variable, no `--no-verify`, no hook path change, no edit to the
gate: those commands are denied to your Bash tool. Only the user can let one push through, from their own
shell, with `! python3 ~/personal/scripts/gate/gate.py skip-once "<reason>"`. When the gate refuses, fix
the cause or report the refusal verbatim. The gate lives in `~/personal/scripts/gate/gate.py`.

## Reference impact (Phase B)

- Before renaming or deleting a symbol, run `python3 ~/personal/scripts/gate/gate.py references --name <symbol>`
  from the repo root. It lists every remaining textual reference with its category and the
  dynamic dispatch sites that make completeness unprovable.
- The Stop hook runs the same check over the removed and renamed definitions of your change and
  reports `Unused | Enumerated | Used`. `Used` means references to a removed name remain: fix them.
  `Enumerated` means no references remain but dynamic dispatch exists in that project, so the list
  cannot be proven complete. Say that in your final message, never claim completeness.
- When you write a PR description, include the output of `gate.py pr-section` verbatim under a
  "Verification" heading. Do not paraphrase it.

## Impact analysis (mandatory, not optional)

Raw grep is not an impact analysis. Before you rename, move, or delete a symbol, and before
you claim a change is complete, use the tools that see the whole change:

- `python3 ~/personal/scripts/gate/gate.py references --name <symbol>` from the repo root:
  every textual reference across all three Ruby projects and the TypeScript, classified as
  code, spec, symbol (mocks such as `receive(:name)`), string literal, comment, or definition,
  plus the dynamic dispatch sites that make the list unprovable.
- Serena `find_referencing_symbols` (MCP tool, `activate_project` first): the exact call sites
  with their enclosing method, which text search cannot distinguish from mocks.
- Every Edit or Write that removes or renames a definition injects a `gate impact:` block into
  your context with the remaining references and the dynamic dispatch count. Act on it before
  the next edit. Do not treat it as noise.

A change is the definition and all of its references. Specs that mock the old name are part
of the change. String literals and YAML that name it are part of the change.

## Phased development (default for every code change)

A code change starts with `/phase` (phase 1, discovery) unless the prompt says `quick`. One branch, one
draft PR, one commit per phase, a human approval between phases. The phase skills live in
`.claude/skills/phase-1` to `phase-7`; the shared start and handoff are in `.claude/skills/phase/ceremony.md`.

- The active phase is the number in `.claude/proof/phase`. `prepare-commit-msg` stamps it as `Phase: N`.
- `pre-push` lets phases 1 to 5 through without a proof and still refuses a `References-Verdict: Used`.
  Phases 6 and 7 need a passing, fresh proof like any other Claude commit.
- `.claude/bin/agent_task_finalize --phase N` must exit 0 before a handoff: the shape of the diff for the
  phase (locations, stubs, TODO markers), then rubocop, yarn check and lint and, for phases 6 and 7, the
  specs, all run in the checks worktree `~/work/ipaas_worktrees/checks` (one permanent slot, reset to
  `origin/main` after every use), then the proof and the reference verdict.
- Live checks (phases 5 and 7) use the same worktree: `gate.py checks apply`, `bin/dev` on its slot,
  `gate.py checks reset`. The PR worktree itself needs no slot.
- Never start the next phase on your own. The approval of the phase commit starts it.
