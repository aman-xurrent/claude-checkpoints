# ipaas verification gate

A verification gate for Claude Code work on the ipaas monorepo (three Ruby projects:
`platform`, `connector`, `connector-sdk`, plus a TypeScript frontend in `platform`).

## The problem it solves

Claude Code can finish a change and say "done" while the change is not done: a renamed
method still has callers, the spec was never updated, the spec is flaky, or the spec is
green whether or not the fix is present. Claude has grep and can find references. What it
lacks is an obligation. Rules in `CLAUDE.md` are advisory and can be skipped. Hooks run by
the harness cannot. The gate is that obligation.

## What the gate does

**Revert proof.** Claude must name the spec example that proves its change, in
`.claude/proof/declaration.json`. Writing that file launches a detached prover. The prover
reverts the non-spec hunks in an isolated worktree, runs the named example and requires it
to FAIL, restores the change and requires it to PASS, then runs the spec file under three
random seeds and requires it to stay green. A spec that passes with the fix reverted is
reported as `vacuous`. A unit-tier proof takes about 30 seconds.

**Reference gate.** At the end of every turn the gate compares `ctags` output of the base
version (merge-base with `origin/main`) against the working version and lists every removed
or renamed definition. For each name it searches the whole repository with ripgrep, classifies
every hit (definition, comment, symbol, string literal, spec, code), and returns one verdict:

| Verdict | Meaning |
|---|---|
| `Used` | References to the removed name remain. Fix them. |
| `Enumerated` | No references remain, but the project contains dynamic dispatch with a non-literal target, so completeness cannot be proven. The sites are listed with `file:line`. |
| `Unused` | No references remain and no dynamic dispatch is present. |

The dynamic-dispatch detector is `unprovable.yml`, a set of ast-grep rules that match `send`,
`public_send`, `const_get`, `constantize`, `define_method`, the eval family with a non-literal
first argument, and `method_missing` definitions. `unprovable_fixture.rb` pins nine expected
matches; the gate refuses to report a clean verdict if the rules stop matching the fixture.

**Enforcement.** The Stop hook is advisory: it reports findings and never blocks a turn.
The hard gate is `pre-push`: it refuses a push whose range contains a Claude-authored commit
(one carrying `Co-Authored-By: Claude`) that touches code and lacks a fresh, passing
`Proof-Id` trailer. `prepare-commit-msg` stamps the trailers (`Proof-Id`, `Proof-Status`,
`Proof-Fresh`, `References-Verdict`, `Unprovable-References`). Human commits are ignored.
Commits authored before 2026-09-06 are exempt. `GATE_SKIP=1 git push` overrides on purpose.

## Files

| File | Role |
|---|---|
| `gate.py` | Everything. Subcommands: `setup`, `launch`, `prove`, `stop`, `surface`, `commit-msg`, `pre-push`, `references`, `pr-section`, `randomized-suite`, `rspec`. |
| `unprovable.yml` | ast-grep rules for dynamic dispatch with a non-literal target. |
| `unprovable_fixture.rb` | Nine known matches. The self-test the gate runs before trusting the rules. |
| `pre-push`, `prepare-commit-msg` | Two-line shell wrappers, symlinked into `.git/hooks/`. |
| `CLAUDE.local.md` | The per-session protocol Claude follows in ipaas. Lives at the ipaas root, excluded from ipaas git. |
| `docs/plan.md` | The plan, written last after orientation, questions, and adjudicated debates. |
| `docs/decisions.md` | Every decision, why, and what was retracted. |
| `docs/tool-verdicts.md` | Ten code-search tools read at source level, and what each is good for. |
| `docs/diagrams/` | Two Excalidraw diagrams of the flakes the gate found in `platform/spec/unit`. |

## Installation on the author's machine

- Code: `~/personal/scripts/gate/`.
- Claude Code hooks, in `~/work/ipaas/.claude/settings.local.json` (untracked):
  `PostToolUse Edit|Write -> gate.py launch`, `Stop -> gate.py stop`,
  `UserPromptSubmit -> gate.py surface`.
- Git hooks: `~/work/ipaas/.git/hooks/pre-push` and `prepare-commit-msg`, symlinks into the
  gate directory, shared by every worktree of the repository.
- `~/work/ipaas/.git/info/exclude`: `**/.claude/proof/`, `/CLAUDE.local.md`, `**/.serena/`.
- Prerequisites: ripgrep, ast-grep, universal-ctags (all Homebrew), Docker Desktop, Python 3,
  the repository's Ruby via rbenv. Serena is optional and registered as an MCP server for
  pre-change navigation only.

## Isolation

The prover never touches the working tree it proves. It uses:

- a persistent worktree, `~/work/ipaas_worktrees/gate`, reset to the base commit and patched
  per run; `rake js:routes:typescript` runs after every reset because request specs render
  through Vite and the build imports the generated route helpers;
- its own MySQL databases, `ipaas_gate_test` and `ipaas_queue_gate_test`, on the same server
  the suite uses (`127.0.0.1:13306`);
- its own Redis, container `ipaas-gate-redis` on port 26380;
- its own git server, container `ipaas-gate-git` on port 23232, because the suite's soft-serve
  server keeps state in SQLite and a concurrent client gets `SQLITE_BUSY`;
- its own git namespace, `worktree_gate/...`.

The prover refuses to start unless the gate worktree's `.env.test.local` names exactly those
databases and that Redis, and unless both containers are up. It fails closed.

Two operating rules. Never run rspec in the gate worktree except through `gate.py rspec`,
which takes the same lock the prover takes. A full randomized run starts from
`db:test:prepare`, because some specs commit rows outside the test transaction.

## Protocol in a session

1. Before renaming or deleting a symbol: `gate.py references --name <symbol>`.
2. After the change: write `.claude/proof/declaration.json` with the spec file and the exact
   example description. Read `.claude/proof/runs/<id>/findings.json`. Do not say "done" over
   `pending`, `vacuous`, `fails_with_change`, `flaky`, or `ambiguous_declaration`.
3. Editing after a proof makes it stale. Redeclare.
4. PR descriptions include `gate.py pr-section` verbatim.

## What it found

Two randomized runs of `platform/spec/unit` (4481 examples each) in the isolated gate found
two real cross-example leaks, both a process-global cache outliving the per-example rollback
or the demo checkout reset. Both are root-caused to the line and both fixes were validated in
the gate: with them applied, seed 22 runs 4481 examples with 0 failures. The diagrams in
`docs/diagrams/` and the record in `docs/decisions.md` carry the details. The fixes
themselves are ipaas changes and are not part of this repository.

## Declared limits

- Ruby reference completeness cannot be proven by any tool. `Enumerated` is the honest answer.
- System specs (Capybara) are a CI tier in this phase; the local proof covers unit specs.
- `git commit --amend` reports `Proof-Fresh: no`, because the diff against HEAD is empty then.
- The coverage cross-check on the declaration is not built.
- ast-grep cannot see ERB, so references inside Rails views are a declared hole.
