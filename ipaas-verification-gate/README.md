# ipaas verification gate

A verification gate for Claude Code. Built for the ipaas monorepo (three Ruby projects,
`platform`, `connector`, `connector-sdk`, plus a TypeScript frontend), and written so that
every ipaas assumption sits in one marked block and can be changed for another repository.

## The problem it solves

Claude Code can finish a change and say "done" while the change is not done: a renamed
method still has callers, the spec was never updated, the spec is flaky, or the spec is
green whether or not the fix is present. Claude has grep and can find references. What it
lacks is an obligation. Rules in `CLAUDE.md` are advisory and can be skipped. Hooks run by
the harness cannot. The gate is that obligation.

## What the gate does

**Revert proof.** Claude names the spec example that proves its change in
`.claude/proof/declaration.json`. Writing that file launches a detached prover. The prover
reverts every hunk outside the example files (`*_spec.rb`, `*.test.*`), spec helpers and
support files included, in an isolated worktree, runs the named example and requires it
to FAIL, restores the change and requires it to PASS, then runs the spec file under three
random seeds and requires it to stay green. A spec that passes with the fix reverted is
reported as `vacuous`. A unit-tier proof takes about 30 seconds.

**Phase-aware finish check.** `gate.py finalize --phase N` (wrapped by ipaas's
`.claude/bin/agent_task_finalize`) checks the shape of the current phase's own diff (allowed locations for
phases 2 to 4, `raise NotImplementedError` bodies for new methods in phase 3, comment-only TODO markers in
phase 4, no marker removed in phase 6, no marker or stub left in phase 7), runs rubocop on the changed Ruby,
`yarn check` and `yarn lint` when JavaScript changed, and for phases 6 and 7 the specs of the changed files,
all inside the **checks worktree** (`gate.py setup-checks` creates it once: one permanent `setup-worktree`
slot with its own databases, Redis DBs and ports at `~/work/ipaas_worktrees/checks`). Finalize applies the
branch's HEAD plus its working tree there, runs the tools, and resets it to `origin/main`, test database
reloaded when the branch carried migrations. `gate.py checks apply | reset | status` gives phases 5 and 7 the
same worktree for a live check with `bin/dev` and the Chrome MCP. Then the revert proof and the reference verdict.
Exit 0 pass, 1 a gate failed, 2 wrong shape. The active phase is the number in `.claude/proof/phase`;
`prepare-commit-msg` stamps it as a `Phase: N` trailer and `pre-push` exempts phases 1 to 5 from the proof.

**Local-only phase workflow.** The phase skills (`/phase`, `/phase-1` to `/phase-7`, `/phase-comments`) and
the `agent_task_finalize` wrapper are never committed to ipaas. They live in `ipaas-skills/` here and are
symlinked into each worktree by `gate.py link-worktree`; the `post-checkout` hook does that for every new
worktree and also copies `CLAUDE.local.md` and `.claude/settings.local.json` so hooks fire there. The
shared `.git/info/exclude` hides all of it from `git status`. The daemon that advances the phases lives in
`../phased/`.

**Reference gate.** At the end of every turn the gate compares `ctags` output of the base
version (merge-base with the upstream branch) against the working version and lists every
removed or renamed definition. For each name it searches the repository with ripgrep,
classifies every hit (definition, comment, symbol, string literal, spec, code), and returns
one verdict:

| Verdict | Meaning |
|---|---|
| `Used` | References to the removed name remain. Fix them. |
| `Enumerated` | No references remain, but the project contains dynamic dispatch with a non-literal target, so completeness cannot be proven. The sites are listed with `file:line`. |
| `Unused` | No references remain and no dynamic dispatch is present. |

The dynamic-dispatch detector is `unprovable.yml`, a set of ast-grep rules for Ruby.
`unprovable_fixture.rb` pins nine expected matches; the gate refuses to report a clean
verdict if the rules stop matching the fixture.

**Impact at edit time.** The same `PostToolUse` hook that launches the prover also compares
the definitions in an edit's old and new text with `ctags` (for a Write, the committed file
against the new one). When a definition disappears, Claude's context receives a `gate impact:`
block right away: every remaining reference, production call sites first, then string
literals, then spec mocks, then the dynamic-dispatch count that makes the list unprovable,
with pointers to Serena for exact call sites and to `gate.py references` for the full list.
Ordinary edits produce nothing. `CLAUDE.local.md` makes impact analysis mandatory: raw grep is
not an impact analysis, and a change is the definition plus all of its references.

**Enforcement.** The Stop hook is advisory: it reports and never blocks a turn. The hard
gate is `pre-push`: it refuses a push whose range contains a Claude-authored commit (one
carrying `Co-Authored-By: Claude`) that touches code and lacks a fresh, passing `Proof-Id`
trailer. `prepare-commit-msg` stamps the trailers. Human commits are ignored. Commits
authored before the gate epoch are exempt. `GATE_SKIP=1 git push` overrides on purpose.

## Files

| File | Role |
|---|---|
| `gate.py` | Everything. Subcommands: `setup`, `launch`, `prove`, `stop`, `surface`, `commit-msg`, `pre-push`, `references`, `pr-section`, `randomized-suite`, `rspec`. |
| `install.sh` | Idempotent installer for one repository on one machine. |
| `unprovable.yml`, `unprovable_fixture.rb` | The dynamic-dispatch rules and their self-test. |
| `pre-push`, `prepare-commit-msg` | Shell wrappers that find `gate.py` next to themselves. Symlinked into the repository's hooks directory. |
| `CLAUDE.local.md` | The per-session protocol Claude follows. Copied to the repository root, excluded from its git. |
| `docs/plan.md`, `docs/decisions.md`, `docs/tool-verdicts.md` | The plan, every decision with its reason and retractions, and the review of ten code-search tools. |
| `docs/diagrams/` | Two Excalidraw diagrams of the flakes the gate found. |

## Setup on a new machine

Prerequisites: a working ipaas development environment (specs pass in the main worktree and
`platform/.env.test.local` exists), Docker Desktop running, and three Homebrew tools:

```
brew install ripgrep ast-grep universal-ctags
```

Then:

```
git clone git@github.com:aman-xurrent/claude-checkpoints.git
cd claude-checkpoints/ipaas-verification-gate
./install.sh --repo ~/work/ipaas --worktree ~/work/ipaas_worktrees/gate
```

`install.sh` is idempotent. It checks the tools, creates the gate's Redis container and its
soft-serve git server (building the image from the repository's `git-server/Dockerfile-test`
if needed, generating the CI key pair if missing), trusts the server's host key, symlinks the
two git hooks, adds the three local git excludes, copies `CLAUDE.local.md`, registers the
three Claude Code hooks in `.claude/settings.local.json`, and runs `gate.py setup`, which
creates the proof worktree, generates its env file, and prepares its databases.

Run `install.sh` from the directory where the gate should live. The hook registration and
the symlinks point at that directory by absolute path.

Optional: Serena as an MCP server for pre-change navigation. From the repository:
`claude mcp add -s local serena -- uv run --directory <serena clone> serena start-mcp-server --context ide-assistant`,
then `serena project create <repo>/platform --name ipaas-platform --ls ruby --ls typescript`
and the same for `connector` and `connector-sdk`. It is not part of the gate verdict.

## Configuration for another project

Everything ipaas-specific is in the block at the top of `gate.py` marked
`PROJECT CONFIGURATION`, in four named functions, and in steps 1 to 4 of `install.sh`.

| Constant or function | What it encodes | Change it when |
|---|---|---|
| `MAIN_REPOSITORY`, `GATE_WORKTREE` | The repository and the prover's worktree. Env: `IPAAS_GATE_REPOSITORY`, `IPAAS_GATE_WORKTREE`. | Always. |
| `RUBY_PROJECTS` | Sub-projects with their own spec suite; a declared spec path starts with one. | Different layout, or a single-project repository (use one entry). |
| `GATE_DATABASE`, `GATE_QUEUE_DATABASE`, `GATE_REDIS_*`, `GATE_GIT_*` | The shared services the specs touch, one gate-specific instance each. | Your specs touch different services. Every shared stateful service needs its own instance or a proof will corrupt your own runs. |
| `TEST_ENVIRONMENT_FILE`, `SHARED_ENVIRONMENT_FILE_NAMES`, `SHARED_LINK_TARGETS` | Where the test env lives and which local files are shared into the worktree. | Different env conventions. |
| `gate_test_environment()`, `prover_environment()` | The environment variables the prover overrides (`DB_WRITER_DATABASE`, `DB_READER_DATABASE`, `DB_QUEUE_DATABASE`, `REDIS_URL`, `IPAAS_GIT_SERVER_REPO_NAME`, `IPAAS_GIT_SERVER_PORT`) and the guard that refuses to run against shared names. | Your app reads different variables. Keep the guard. |
| `generate_frontend_routes()` | Generated files the specs need after every worktree reset. | Your build has different generated inputs, or none. |
| `prepare_pristine_databases()` | The command that resets the test databases before a full randomized run. | Different framework. |
| `SYSTEM_SPEC_MARKER`, `CODE_EXTENSIONS`, `REFERENCE_GLOBS`, `*_EXCLUDES`, `NOISE_PATH_MARKERS` | What is a slow spec, what is code that owes a proof, what to search and skip. | Different languages or layout. |
| `UPSTREAM_BRANCH`, `CLAUDE_TRAILER_PATTERN`, `GATE_EPOCH` | Diff base, how Claude's commits are recognised, and the exemption date. | Your default branch is not `origin/main`; set the epoch to your install date. |
| `RBENV_SHIMS`, `HOMEBREW_BIN`, the timeouts and seeds | Toolchain paths and budgets. | Different Ruby manager, slower suite. |
| `rspec(...)` in the prover | The test runner invocation and its JSON report. | Not RSpec. The proof logic (red, then green, then seeds) is runner-independent. |
| `unprovable.yml` | Ruby dynamic dispatch. | Another language: write the equivalent ast-grep rules and a fixture, and update `UNPROVABLE_FIXTURE_EXPECTED_MATCHES`. |
| `install.sh` steps 2 to 4 | Redis, soft-serve git server, databases. | Replace with your project's shared services. Steps 5 to 9 are generic. |

Everything below the `GATE INTERNALS` banner is project-independent: proof state files,
verdict and status names, trailers, the lock, the size caps.

## Protocol in a session

1. Before renaming or deleting a symbol: `gate.py references --name <symbol>`.
2. After the change: write `.claude/proof/declaration.json` with the spec file and the exact
   example description. Read `.claude/proof/runs/<id>/findings.json`. Do not say "done" over
   `pending`, `vacuous`, `fails_with_change`, `flaky`, or `ambiguous_declaration`.
3. Editing after a proof makes it stale. Redeclare.
4. PR descriptions include `gate.py pr-section` verbatim.

Two operating rules. Never run rspec in the gate worktree except through `gate.py rspec`,
which takes the same lock the prover takes. A full randomized run starts from a database
reset, because some specs commit rows outside the test transaction.

## What it found

Two randomized runs of `platform/spec/unit` (4481 examples each) in the isolated gate found
two real cross-example leaks, both a process-global cache outliving the per-example rollback
or the demo checkout reset. Both are root-caused to the line; with both fixes applied, seed 22
runs 4481 examples with 0 failures. `docs/decisions.md` section 11 and the diagrams carry the
details. The fixes are changes to ipaas and are not in this repository.

## Not in this repository

No data of the repository under test: no database dumps, no fixtures, no contents of the gate
git server, no environment files, no keys. `install.sh` recreates the infrastructure empty.
The ipaas-side patches produced by the findings are kept with ipaas.

## Declared limits

- Ruby reference completeness cannot be proven by any tool. `Enumerated` is the honest answer.
- System specs (Capybara) are a CI tier in this phase; the local proof covers unit specs.
- `git commit --amend` reports `Proof-Fresh: no`, because the diff against HEAD is empty then.
- The coverage cross-check on the declaration is not built.
- ast-grep cannot see ERB, so references inside Rails views are a declared hole.
