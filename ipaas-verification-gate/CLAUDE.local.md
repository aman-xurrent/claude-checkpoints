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

## Every gate call is recorded

The gate writes each call and its answer to `~/.local/state/gate/trace/<date>.jsonl`. Read your own with
`python3 ~/personal/scripts/gate/gate.py trace --session <id> --full`. You cannot edit that directory.
When the gate answers something you did not expect, quote the trace line instead of describing it.

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
- `prepare-commit-msg` writes `[skip ci]` into the body of a phase 1 to 5 commit: those phases add no
  behaviour, so CI has nothing to test. Never write a skip keyword on phase 6 or 7; `pre-push` refuses it.
- `.claude/bin/agent_task_finalize --phase N` must exit 0 before a handoff: the shape of the diff for the
  phase (locations, stubs, TODO markers), then rubocop, yarn check and lint and, for phases 6 and 7, the
  specs, all run in the checks worktree `~/work/ipaas_worktrees/checks` (one permanent slot, reset to
  `origin/main` after every use), then the proof and the reference verdict.
- Live checks (phases 5 and 7) use the same worktree: `gate.py checks apply`, `bin/dev` on its slot,
  `gate.py checks reset`. The PR worktree itself needs no slot.
- Never start the next phase on your own. The approval of the phase commit starts it.
- Every pull request comment goes through `.claude/bin/pr-comment --pr N --title "..." --body-file <path>`,
  which stamps who posted it and folds the body into a collapsed block. `gh pr comment`, `gh pr review` and
  the comment API are denied, because a raw post is indistinguishable from one the user wrote.

## Review skills (three, every session that changed code)

Three skills look at every code change. Run all three whenever the session changed code, not only at
a handoff. `agent_task_finalize` fails and the Stop hook says so until each one has a record that
matches the current diff.

| Slug | Skill | What it looks for |
| --- | --- | --- |
| `simplification` | `/agent-skills:code-simplify` | the change is as small as it can be |
| `edge-cases` | `/edge-case-hunter` | the inputs and states nobody wrote a case for |
| `quality` | `/agent-skills:code-review-and-quality` | correctness, readability, architecture, security, performance |

Command names and skill names differ. The plugin keeps its commands in
`~/.claude/plugins/cache/addy-agent-skills/agent-skills/1.0.0/.claude/commands/`, so `code-simplify` is
the command and `code-simplification` is the skill it invokes. Read that directory before you call a
name wrong.

Record every item a skill returned, never a summary. Write the items file **outside the repository**:
a file inside the working tree joins the diff and makes the records stale the moment they are written.

```
cat > "$TMPDIR/quality.json" <<'JSON'
[{"title": "the same guard runs twice", "file": "platform/app/x.ts", "line": 42,
  "verdict": "fixed", "detail": "one line of what it is and what you did"}]
JSON
.claude/bin/review-record --skill quality --items-file "$TMPDIR/quality.json"
```

`verdict` is one of `fixed`, `in-scope`, `out-of-scope`, `no-change-needed`. The gate checks that every
file exists and every line is inside it, so an invented source is refused. A skill that returned nothing
records an empty list. `.claude/bin/review-record --section` prints all three lists as markdown for the
pull request.

Judge scope by one rule. A pre-existing problem that the change touches belongs in this pull request. A
pre-existing problem unrelated to the change does not: mark it `out-of-scope` and raise it as a separate
request. "Pre-existing" alone is never a reason to leave it.

## Design links live in the request notes

The user puts a labelled Figma link in the request. The links are in the **notes**, not in the request
fields:

```
.claude/bin/xurrent-api "/requests/<id>/notes?per_page=100" | python3 ~/personal/scripts/gate/gate.py design-links
```

It prints each label with its Figma file key and node id (`721:23920`). Fetch every node it lists, not
the one that looks most relevant. A label that says "Full page design with chrome layout wrapper" means
the frame holds a mock browser toolbar above the page, so the page does not start at the frame's top.

## Matching a design (measure it, never judge it by eye)

1. Seed the local database with the design's own copy. The database is local and one per request, so
   change it freely. Text is how the comparison pairs elements: without the same copy nothing pairs.
2. Read the design: run `~/personal/scripts/gate/figma/extract-design.js` through `use_figma` with the
   frame id. Fetch every node.
3. Render the built page at the design's width, then paste
   `~/personal/scripts/gate/figma/extract-built.js` into the page.
4. Compare:

```
python3 ~/personal/scripts/gate/gate.py figma-compare \
  --design design.json --built built.json --offset-y 76 --tolerance 2
```

`--offset-y` is the height of the mock browser toolbar in the design frame. Read it from the node tree:
on the iPaaS handoff file the `Chrome / Toolbar` instance is 76 tall, so the page starts at y=76.

Three rules the real data proved. The tool already applies them. Do not work around them by hand.

- **Measure text with `Range`, never with the element box.** A Figma text node is as wide as its glyph
  run. The `<h3>` holding the same words is as wide as its column: 1272px against 182px.
- **Never compare the height of text.** Figma reports the line box and the DOM reports the ink. For 14px
  text that is 24 against 17 on text that matches exactly.
- **Compare the width of a text node only when it hugs its content.** A fixed-width text box reports the
  box: the card description is an 800px box holding a 525px sentence.

Pass and fail come from the per-edge pixel delta, not from the overlap ratio. Overlap is evidence only.
Measured on the runbook list: one pixel of error scores 0.9986 overlap on a 1428px card and 0.9535 on a
42px label, so one ratio passes the card and fails the label for the same error. A title sitting 2.5px
low scores 0.70. A run that pairs nothing fails: a comparison that compared no element proves nothing.

## Deliberate differences

When the build does not follow the design or the spec on purpose, record it:

```
.claude/bin/deviation --id no-avatar-column --kind design \
  --summary "the avatar column is not built" --reason "the team dropped it from this release" \
  --source "figma 721:23920" --region 1104,98,180,640 \
  --decided-by user --evidence "<the user's words, or the comment URL>"
```

Only the user decides that a difference is deliberate, and `--decided-by user` needs `--evidence`.
Without it the record is a note from you: it explains the difference and it lets no mismatch pass.
`--region` is the page area in pixels that the comparison excuses. Ask the user before you record a
design difference. Never record one to make a comparison pass.

## Diagram replies

The user answers a diagram with `R:` marks. When they say they updated one, read the marks instead of
reading the picture:

```
~/personal/scripts/excalidraw-marks <file.excalidraw|file.svg>
```

`R:IN` keeps that point, `R:OUT` drops it, and `R:<text>` is a reply about it. Each mark reports the
point it sits next to. Act on every mark before you redraw, and answer every `R:<text>` in chat.
