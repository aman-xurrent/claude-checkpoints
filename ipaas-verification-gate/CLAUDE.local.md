# Verification gate (local, not committed)

Every change to non-spec code in this repository must be proven before it is reported.

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
`GATE_SKIP=1` overrides on purpose and is logged. The gate lives in `~/personal/scripts/gate/gate.py`.

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
