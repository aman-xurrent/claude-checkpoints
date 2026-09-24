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

## Every changed spec file is swept, declared or not

After the declared proofs, the prover runs every other example file your change touched, with the
code reverted. The verdict is per example: **every example your change adds must fail without your
change.** One that still passes gets the file `vacuous` and the whole run fails. A file where the
change adds no example must have at least one failure. Declaring one spec file no longer hides the
rest, and one vacuous example among forty working ones no longer hides either. You do not declare
the sweep and you cannot opt out of it.

Ruby and JavaScript are both swept per example. An example whose description is built by
interpolation is skipped, because it cannot be matched against the description the runner reports.
An example the patch both removes and adds counts as moved, not added.

Read what the sweep proves, and what it does not:

- It proves each example your change adds depends on **something** in the diff.
- It does not prove the file tests the **mechanism it claims to guard**. A spec whose own narrowing
  logic never fires, and a spec that rebuilds its expected value from the same expression it checks,
  both stay green here. Removing the specific guard and re-running is still your job, not the gate's.

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
- Serena `find_referencing_symbols` (MCP tool): the exact call sites with their enclosing
  method, which text search cannot distinguish from mocks.

  **Activate the Ruby project, not the worktree root.** Serena indexes one project per Ruby
  project. Call `activate_project` with the absolute path of the project that holds the symbol,
  for example `<worktree>/platform`, `<worktree>/connector` or `<worktree>/connector-sdk`.
  The worktree root is not a Serena project and activating it indexes nothing useful.
  Each worktree has its own project config, seeded by `gate.py link-worktree`.
  A symbol that crosses projects needs one `activate_project` call per project.
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

**Worked examples: `~/personal/scripts/gate/examples/review-items.md`.** Real items that were acted
on, what a useless item looks like even though the gate accepts it, and the same file covers the
commit message shape.

Judge scope by one rule. A pre-existing problem that the change touches belongs in this pull request. A
pre-existing problem unrelated to the change does not: mark it `out-of-scope` and raise it as a separate
request. "Pre-existing" alone is never a reason to leave it.

## Review comments: the user decides each one, then you fix

A review comment is not a task list you work through. It is a question for the user. On every pull
request, in the phased loop or not:

1. `.claude/bin/comment-triage fetch --pr <n>` stores every unresolved thread and gives each one a
   token, `C1`, `C2`, `C3`.
2. Draw **one** triage diagram, one column per thread, on one spine, following
   `~/personal/scripts/gate/diagram-convention.md`. Each column carries the token in its heading,
   the reviewer's **own words quoted whole**, the file and line, the **current code quoted verbatim**
   from a named ref, and the change you propose as a before and after diff in the green panel.
   When there is a real fork, give each approach its own panel and say what it costs and what it
   gives up. Do not invent approaches to look thorough: one obvious fix is one panel. Say whether
   you think it belongs in this pull request, and why, so the user can disagree. End the column
   with a `C<n> decision:` line in the gutter.
3. Stop. The user marks the diagram.
4. `.claude/bin/comment-triage decide --pr <n> --marks <file>` reads the marks.
   `R:IN` means fix it in this pull request. `R:OUT` means it stays out of this pull request: raise
   it as a separate request. `R:<text>` is an instruction: do what it says and answer it in chat.

**The gate holds every edit to code and specs while any comment has no mark.** The user lifts it by
marking the diagram. There is no other way through, and the gate never decides for them. A mark that
names no `C<n>` records nothing and the command refuses: a decision written against the wrong comment
is worse than no decision, because nothing later shows it went to the wrong place. When a reviewer
replies again on a thread, its earlier decision stops counting and the user sees it again.

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

## Diagrams say it in plain English

Every label in a diagram follows the same house style as every other answer: short words, short
sentences, no jargon left undefined. Do not reach for a fancier word when a common one works. Write
"use", not "leverage". Write "so", not "thereby". Write "stops working", not "degrades". One word
means one thing across the whole drawing: if it is "delete" in one box it is "delete" in every box.
A label that sounds impressive and says little is worse than a plain one, because the reader has to
decode it before they can decide. Never use an em dash. The full convention lives in
`~/personal/scripts/gate/diagram-convention.md`.

## Diagram replies

The user answers a diagram with `R:` marks. When they say they updated one, read the marks instead of
reading the picture:

```
~/personal/scripts/excalidraw-marks <file.excalidraw|file.svg>
```

`R:IN` keeps that point, `R:OUT` drops it, and `R:<text>` is a reply about it. A mark sits anywhere
on a line, usually at the end of the point's own line after `decision:`, and it reports the text
before it on that line as the point it answers. Act on every mark before you redraw, and answer
every `R:<text>` in chat.

**The answers stay on the drawing.** The Excalidraw canvas is one canvas, so drawing the next
diagram destroys the one on it and every answer typed on it. Export the canvas to its named file in
`~/.claude/excalidraw/` before every clear. After the user answers, import the marked source, export
the svg again, and republish with the same `.claude/bin/pr-diagram --name`: the file lands at the
same path on the same branch, so the image in the description updates in place with no edit to the
body. `pr-diagram` refuses an svg that carries no answer when its source has one.

When you redraw a diagram whose points were answered, state each answer inside its point
(`O3  decision: IN, phase 7 ships without shared-mailbox evidence`). Never put the old `R:` text
back by position: the wording moves, the coordinates do not, and a mark replaced beside the wrong
point is worse than no mark at all.

## The pull request description is diagrams, not prose

A phase section is a diagram. It is never a description. Prose in a pull request body turns into
filler that nobody reads, so the tool refuses it: `pr-phase` rejects a section with no image, and
rejects any line outside a `<details>` fold that is not the image or its links.

One section per phase, in the order the phases happened:

```
## Phase 1
## Phase 1 (updated)
## Phase 2
```

`## Phase N` is written by `.claude/bin/pr-phase --phase N`. A phase that comes back after review
adds a new section with `--updated` instead of replacing its old one, so the record keeps what the
phase first said and what changed after the review.

### The reader never leaves the diagram

Whatever the approver must read to understand the point or to make a decision is inside the drawing.

**A pointer is not content**, and that covers every pointer, not only code:

| Written | Write instead |
| --- | --- |
| `solution_exporter.rb:15` | the line itself, quoted |
| "the decision in 4.1.d" | the decision, stated |
| "see the plan for phase 3" | what phase 3 does |
| "per the request" | the sentence from the request |
| "as the reviewer noted" | what the reviewer said |
| "#16 above" | restate it here |

The test: someone with no editor, no browser and no access to the repository must be able to
understand the point and make the decision. If they have to go anywhere, including elsewhere in the
same diagram, it has failed. Each finding stands alone.

Follow `~/personal/scripts/gate/diagram-convention.md`. It is the written form of
`~/.claude/excalidraw/jamf-review-findings.excalidraw`, which is the reference every diagram copies:
one tall column on a vertical spine, stage markers in the left gutter, a dark panel holding the
verbatim code with `#` comments pointing at the defect, and a green panel holding the change as a
before/after diff. Green is a proposal, never applied code.

### What each phase diagram must carry

All four, in one drawing with one spine. The Excalidraw rules in `~/.claude/CLAUDE.md` say how to
draw; this says what to draw.

1. **What the phase changed**, each step labelled with its `file:line`.
2. **The call path the change runs through**: entry point, every hop in call order, the exit, each
   with `file:line`. No step is summarised as "then it processes".
3. **Design against build**, for an interface phase: the measured deltas from `gate.py figma-compare`,
   as a zoom-in panel anchored to the element they belong to.
4. **What is still open**: deliberate differences and out-of-scope items in a marked region, so the
   user can answer with `R:IN` or `R:OUT` on the drawing itself.

### Publishing a diagram

Draw with the Excalidraw MCP, never by hand-writing JSON. Export all three formats, then:

```
.claude/bin/pr-diagram --pr <n> --name phase-1 --svg out/phase-1.svg
```

Pass only the svg. `pr-diagram` normalises it to 1x, renders the png from it at 3x, checks the
diagram is dark, and puts both on `review-assets/<pr>-<slug>`, a branch that is never merged. The
`.excalidraw` source stays in `~/.claude/excalidraw/` and is never published.

The user answers a diagram by adding marks to it. Read them with
`~/personal/scripts/excalidraw-marks`, from the svg or from the source. Never read the picture.

### Creating the pull request

`.claude/bin/pr-create --title "Request#<id> <subject>" --request <id>` opens the draft. The body
starts as one link to the request and nothing else. Every section after that goes through
`pr-phase`, which takes a diagram and refuses prose.

The guard refuses `gh pr create --body` and `--body-file`. A description written at creation used to
escape every check, because the guard only watched `gh pr edit`. That is how a body reached several
hundred lines nobody reads.

### The only text allowed

Machine output, inside a `<details>` fold in that phase's own section: `gate.py pr-section` verbatim,
and `.claude/bin/review-record --section`. Both are generated evidence with sources. Nothing you
wrote yourself goes in the body.

```
## Phase 6

![Phase 6: the runbook card](https://git.4me.com/4me/ipaas/blob/review-assets/1027-runbook-view/phase-6.png?raw=1)

[svg](…/phase-6.svg?raw=1) · [source](…/phase-6.excalidraw?raw=1)

<details><summary>Verification and review items</summary>

…gate.py pr-section output, verbatim…
…review-record --section output…

</details>
```
