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
- It does not prove each changed line is tested. The unit stage below asks that.

## Every changed unit is removed, one at a time

`python3 ~/personal/scripts/gate/gate.py units --measure` removes one changed unit at a time in the gate
worktree and runs the specs that ran it. It is not part of the proof yet: on PR 1111 it took about ten hours
under the gate lock for 705 units, so it runs only on request until the owner decides its place. A unit is a statement, an entry of a hash, an array or a keyword argument list, a JSX child
or attribute, a guard reduced to what it guards (`c && <X/>` to `<X/>`), or a fallback reduced to its left
side (`a || b` to `a`). Imports, types, comments, `private` and declarations are not units: their users
carry the proof.

- `killed`: an example that passed with the change failed without the unit. The unit is tested.
- `survived`: every spec that ran the unit stays green without it. Nothing tests it.
- `unexecuted`: no candidate spec file runs the unit at all.
- `unmeasured`: the unit could not be judged, and the record says why: the specs did not load without it
  (the file is needed, not the line), only spec files that are red with the change applied ran it, its
  spec files are not green when run together, examples that pass with it did not run without it, a run
  timed out, or the time budget ran out first.

A runner that writes no report or no coverage makes the stage an error, never a pass, and a stage that
killed no unit proved nothing, so its status is `unmeasured`. Changed lines that produced no unit (a
constant, an assignment, a link of a method chain) and files no finder reads (`.erb`, a config file outside
`platform/app/javascript/`) are listed as not judged: in the run's `findings.json` under `units.not_judged`,
or in `units.json` for `units --measure`.

Read the report with `python3 ~/personal/scripts/gate/gate.py units`. A survivor is fixed with an
example that fails without the unit, or by deleting the unit. **It reports for now: survivors do not
refuse the push until the stage has been measured on real branches.**

If you think a survivor needs no spec, quote the unit to the user and stop. Only their words clear it,
recorded once they give them:
`python3 ~/personal/scripts/gate/gate.py waive --unit <digest> --evidence "<their words>"`. The words must
be whole sentences the user typed in this repository after the unit stage reported the unit. Your summary,
text the gate printed before they typed it, a pasted block and an option label you wrote are refused. One
record covers one unit in one worktree, and it stops counting when the unit's text changes. A survivor
found by `units --measure` cannot be waived: declare a proof and waive the unit its run reports.

`pre-push` refuses a Claude commit that touches code without a fresh, passing `Proof-Id` trailer, and
it judges the branch again at the newest Claude commit: a rebase or an amend that changed a file under a
proof makes it stale. A clean rebase keeps a proof fresh, because freshness compares the changed lines,
not the raw diff. Every changed file except documentation counts, YAML and stylesheets included. A file
that sat untracked in the worktree while the proof ran and is never committed does not count, unless it
is code, a spec or a fixture.
There is no skip for you. No environment variable, no `--no-verify`, no hook path change, no edit to the
gate: those commands are denied to your Bash tool. Only the user can let one push through, from their own
shell, with `! python3 ~/personal/scripts/gate/gate.py skip-once "<reason>"`. When the gate refuses, fix
the cause or report the refusal verbatim. The gate lives in `~/personal/scripts/gate/gate.py`.

## Every gate call is recorded

The gate writes each call and its answer to `~/.local/state/gate/trace/<date>.jsonl`. Read your own with
`python3 ~/personal/scripts/gate/gate.py trace --session <id> --full`. Never write that directory: the
guard refuses the plain ways, and the others are the same breach.
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

## Phased development (only when the user asks for it)

The phase loop runs when the user asks for it with `/phase`. Never start it on your own. One branch, one
draft PR, one commit per phase, a human approval between phases. The phase skills live in
`.claude/skills/phase-1` to `phase-7`; the shared start and handoff are in `.claude/skills/phase/ceremony.md`.

- The active phase is the number in `.claude/proof/phase`. `prepare-commit-msg` stamps it as `Phase: N`
  on every new commit, whatever its message says. An amend takes the higher of its own phase and the
  active one. With no phase active, a Claude commit carries no phase and no `[skip ci]`: the hook removes
  both. A commit a rebase replays (a pick, a reword, a continue after a conflict) keeps its trailers, and
  a squash keeps its highest phase; the hook says so on standard error. A cherry-pick outside a rebase is
  a new commit.
- `pre-push` lets phases 1 to 5 through without a proof and still refuses a `References-Verdict: Used`.
  It refuses a skip keyword on any other code commit, the ones outside the loop included.
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

## Nothing leaves the draft unseen

A session shipped an entire page and wrote, when pressed: *"Never seen rendered: all of it."* It had
run the unit tests, the type check, rubocop and the revert proof, and it had ticked the phase 7 live
checkbox. A checkbox is not a check.

**A user-facing change must be driven in a real browser. A change with no rendered surface must be
run through `bin/rails runner`.** Both are recorded, and the gate produces the evidence:

```
.claude/bin/live-record --kind browser --path /solutions \
    --asserted "the list drew five rows and the empty screen showed on a no-match search" \
    --screenshot /tmp/solutions.png

.claude/bin/live-record --kind runner --expression 'puts Solution.order(:id).pluck(:name).inspect' \
    --asserted "the exporter returns the five seeded names in order"
```

You choose the page and the expression. You never write the result. The gate asks the checks server
for that page itself and stores the status and the size, and it runs the ruby itself and stores the
real stdout. A screenshot alone proves nothing, because a session can write a png.

Which browser does not matter: Chrome through its MCP, Playwright, anything that drives a real page.
What matters is that the server answered when the record was taken.

Redirects are followed, because this application redirects by design: every page carries
`redirect_to_pick_solution`, so a 302 is normal and the chain is what counts. The record keeps where
it landed. **The gate picks the `xurrent` account first.** Every page redirects to the account picker until an
account is chosen, so an unauthenticated fetch only ever sees the picker. The gate drives the picker
itself, which is two requests and holds no credential: it reads the CSRF token off the picker page
and posts `sitename=xurrent`. `--account <name>` overrides it, and `IPAAS_LIVE_ACCOUNT` sets the
default. A name the application does not know is refused by name.

**The request must end on the path it asked for**, and the answer must carry the `x-request-id` and
`x-runtime` headers that Rails sets on every response. Asking for the solutions list and arriving at
the account picker is a 200, and recording it would say a page was seen that nobody saw. The header
check is there because anything can listen on a port and answer 200: a stub left on the checks port
answered every request with 813 bytes for eight hours, kept Rails from binding, and would have been
recorded as a rendered page.

A chain that leaves `127.0.0.1` means no page of the change rendered, and the landing url says why:
either the sign-in flow, when the current user cannot be established (check that the server has
`STUB_IDENTITY_PROVIDER=true` and that `User.first` exists in the checks database), or an external
host the page itself redirects to. The gate carries no credential and will not accept a page it
could not reach itself.

**The record is keyed to the diff.** Change the code afterwards and it goes stale, because a change
nobody has rendered since is a change nobody has seen. Record it again. That covers the edit made
after phase 7 as much as the first one.

Where it binds:

- `agent_task_finalize` fails on phase 5 and phase 7 without it. Phases 1 to 4 ship no behaviour to
  render and are skipped.
- **The guard refuses `gh pr ready` and the `markPullRequestReadyForReview` mutation.** Taking the
  pull request out of draft is the moment the work is offered to a reviewer, and nothing owned that
  moment before.

**There is no exception path.** If the checks worktree will not boot, fix the worktree. A slot
problem is not permission to ship a change nobody has seen, and a skip here would become the way
every live check gets skipped.

## Review skills (three, every session that changed code)

Three skills look at every code change. Run all three whenever the session changed code, not only at
a handoff. `agent_task_finalize` fails and the Stop hook says so until each one has a record that
matches the current diff.

| Slug | Skill | What it looks for |
| --- | --- | --- |
| `simplification` | `/agent-skills:code-simplify` | the change is as small as it can be |
| `edge-cases` | `/edge-case-hunter` | the inputs and states nobody wrote a case for |
| `quality` | `/agent-skills:code-review-and-quality` | correctness, readability, architecture, security, performance |

`review-record --skill` takes the slug or the skill's own name in any spelling: `/edge-case-hunter`,
`bmad-review-edge-case-hunter`, `agent-skills:code-simplify` and `code-simplification` all resolve.

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

## A stacked pull request measures from its own base

A pull request opened on top of another one does not branch from main, and it does not wait for
the parent to merge.

The proof resets to the merge-base with **the pull request's own base branch**, so everything the
parent already shipped is present in the red run and only this change is reverted. The declared
example then fails because this change is gone, which is the question the proof asks. Whether the
parent has landed on main changes nothing about that.

The base comes from the pull request itself and the gate caches it. You never write it: a base you
choose is a skip switch, because a base of HEAD makes every revert revert nothing.
`.claude/proof/base.json` is refused to your tools, by the file path and by a shell redirect.

`agent_task_finalize` checks the base on phases 6 and 7, the phases that carry a proof:

- the base branch must be fetched, or the proof cannot measure from it;
- the branch must not be **behind** its base. A base that has moved means the proof measures
  against a parent that no longer exists, and the branch has to be redone on the newer one anyway.
  Rebase onto `origin/<base>` and re-prove.

Being behind is the real hazard, not being unmerged. Merge order to main is a merge-time rule and
the gate does not enforce it here.

## Connector specs: one run_action per example

`run_action` memoises the action for the whole example. `spec/support/shared_contexts/action_context.rb:27`
opens with `return @action if defined?(@action)`, so the second call in one example re-runs the
**first** input and ignores the one you passed.

```ruby
it 'rejects an oversized body' do
  run_action(valid_input)        # runs valid_input
  expect { run_action(huge_body) }.to raise_error(...)   # runs valid_input AGAIN
end
```

The example passes while proving nothing, which is the failure the gate exists to catch and cannot
see: the revert proof only asks whether the example goes red without the change, and a memoised
re-run goes red for the first input just as happily.

**One `run_action` per example. Always.** A second input is a second example.

## An action is declared with a failing run block from the start

A connector action declared with no `run` block was reported to stop the application booting, found
in PR 1059 after three phases, because phases 2 to 5 never run the test suite. Not verified here;
the cost of the rule is one line either way.

Declare the action and its placeholder together, in the phase that declares it:

```ruby
run do
  raise NotImplementedError, 'TODO(phase-4 #<request>) send the message'
end
```

The placeholder is the phase 3 contract. Phase 6 or 7 replaces it under proof.

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

**The user may answer in chat instead of on the drawing**, and often will. They run nothing: while
any comment is waiting, every prompt carries a `gate:` line listing the open ones, so when their
message answers one you record it straight away, before anything else, with their own words beside
it:

```
.claude/bin/comment-triage decide --pr <n> --token C4 --verdict fix-in-this-pr \
    --evidence "yes fix the scrubber one here, the rest go in their own request"
```

`--verdict` is one of `fix-in-this-pr`, `separate-request`, `follow-the-user's-words`. `--evidence`
is the user's own words, whole sentences copied from what they typed, never your summary of them. The
gate checks the quote against the user's stored prompts and answers, typed in this repository after the
reviewer's latest comment on that thread, and refuses one it cannot find. The record keeps which way it
came, `chat` or `marks`.

If their message does not say which comment it answers, ask which one. Never guess: a decision
recorded against the wrong comment is worse than no decision, because nothing later shows it went
to the wrong place.

This path exists because the drawing is a file you can write. A decision spoken in chat was being
transcribed into a mark and read back as though the user had drawn it, which works and hides where
the decision came from. Do not do that any more: record chat as chat. **Never write a mark on the
drawing yourself.** A mark is the user's, and one you typed is indistinguishable from one they did.

**The gate holds every edit to code and specs while any comment has no decision.** The user lifts it,
by marking the drawing or by saying it in chat. There is no other way through, and the gate never
decides for them.

**An outdated thread holds nothing.** GitHub marks a thread outdated when the code it points at has
changed or gone, and the standing rule for what is still outstanding is `isResolved == false and
isOutdated == false`. The gate used to hold on outdated threads too, and three of them, all on S3
code that had already been deleted, blocked every edit on a branch. They stay on the list and in the
Stop message so nothing is lost. Answer them on the pull request, or decide them like any other. A mark that
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
  --decided-by user --evidence "<the user's words, copied from what they typed>"
```

Only the user decides that a difference is deliberate. `--decided-by user` needs `--evidence` that the
gate finds as whole sentences of what the user typed in this repository, after figma-compare reported
the difference it answers; it refuses anything else. A `--region` needs that report first, whatever the
record's kind, and a compare that matches ends it. A
record you make without it is a note: it explains the difference and it lets no mismatch pass.
`--region` is the page area in pixels that the comparison excuses. Ask the user before you record a
design difference. Never record one to make a comparison pass.

## Diagram replies

The writing and diagram style rules live in `~/.claude/CLAUDE.md`, which every session loads. For what the
ipaas tools enforce (the pull request description, review records, comment triage and the gate), this file
is the authority.

Read the user's `R:` marks with `~/personal/scripts/excalidraw-marks <file.excalidraw|file.svg>`, never by
reading the picture. After the user answers, import the marked source, export the svg again, and republish
with the same `.claude/bin/pr-diagram --name`: the file lands at the same path on the same branch, so the
image in the description updates in place. `pr-diagram` refuses an svg that carries no answer when its
source has one.

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

Each phase diagram carries four things on one spine: what the phase changed, with `file:line` per step;
the call path the change runs through, every hop in call order with `file:line`; the design against the
build, with the measured differences, as a zoom-in anchored to its element; and what is still open, in a
marked region the user answers with `R:IN` or `R:OUT`.

`## Phase N` is written by `.claude/bin/pr-phase --phase N`. A phase that comes back after review
adds a new section with `--updated` instead of replacing its old one, so the record keeps what the
phase first said and what changed after the review.

### Publishing a diagram

Draw with the Excalidraw MCP, never by hand-writing JSON. Export the `.excalidraw` source and the svg, then:

```
.claude/bin/pr-diagram --pr <n> --name phase-1 --svg out/phase-1.svg
```

Pass only the svg. `pr-diagram` normalises it to 1x, renders the png from it at 3x, checks the
diagram is dark, and puts both on `review-assets/<pr>-<slug>`, a branch that is never merged. The
`.excalidraw` source stays in `~/.claude/excalidraw/` and is never published.

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

[svg](…/phase-6.svg?raw=1)

<details><summary>Verification and review items</summary>

…gate.py pr-section output, verbatim…
…review-record --section output…

</details>
```
