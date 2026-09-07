# claude-checkpoints

Snapshots of tooling built with Claude Code. Each top-level directory is one piece of work,
frozen at the state described in its own documents.

| Directory | What it is |
|---|---|
| `ipaas-verification-gate/` | A verification gate for Claude Code work on the ipaas monorepo. It stops a change from being reported as done when the change missed a call site, missed a spec, or rests on a spec that is flaky or passes with the fix reverted. |
| `phased/` | The phase loop: 7-phase development as the default Claude Code workflow for ipaas, advanced by the author's GitHub approval per phase and run by a small Python daemon. Planned, not built; see `phased/docs/plan.md`. |

The live copy of the gate runs from `~/personal/scripts/gate/` on the author's machine. This repository is a snapshot of that directory plus its design documents. Sync before pushing a new version.
