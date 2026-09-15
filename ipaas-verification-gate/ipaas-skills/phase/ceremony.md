# Phase ceremony

The same start and the same handoff for every phase. `N` is the phase, `<id>` the request number from the
branch name `requests/<id>-*`, `<n>` the PR number.

## Start of a phase

1. `printf N > .claude/proof/phase`. In a worktree without `.claude/settings.local.json`, copy it from the
   main clone (`cp ~/work/ipaas/.claude/settings.local.json .claude/`): the gate hooks and the permission
   rules live there and Claude Code reads them per worktree, at session start.
2. `git fetch origin main`. When `git merge-base --is-ancestor origin/main HEAD` fails, the branch is behind:
   `git rebase origin/main` then `git push --force-with-lease origin HEAD`. The approvals on the old commits
   are gone after that, which is expected; the human re-approves the rebased head.
3. Read the context you need and nothing else: the request
   (`.claude/bin/xurrent-api "/requests/<id>?fields=custom_fields"`) and the PR description with the earlier
   phase sections (`GH_HOST=git.4me.com gh pr view <n> --json body --jq .body`). `AGENTS.md` and the sub-project
   `AGENTS.md` of every area you touch.
4. When the change touches the interface, list the design links. The user puts a labelled Figma link in the
   request, and the links live in the **notes**, not in the request fields:

   ```
   .claude/bin/xurrent-api "/requests/<id>/notes?per_page=100" | python3 ~/personal/scripts/gate/gate.py design-links
   ```

   It prints each label with its Figma file key and node id (`721:23920`). Fetch every node listed, not the
   one that looks most relevant. A label that says "Full page design with chrome layout wrapper" means the
   frame holds a mock browser toolbar above the page, so the page area does not start at the frame's top.

## Review skills

Three skills look at every code change before it is handed off. Run all three whenever the phase changed
code, in every session, not only at handoff. The gate fails `agent_task_finalize` until each one has a
record that matches the current diff.

| Slug | Skill | What it looks for |
| --- | --- | --- |
| `simplification` | `/agent-skills:code-simplify` | the change is as small as it can be |
| `edge-cases` | `/edge-case-hunter` | the inputs and states nobody wrote a case for |
| `quality` | `/agent-skills:code-review-and-quality` | correctness, readability, architecture, security, performance |

Record every item a skill returned, never a summary. Write the items file **outside the repository**
(use the session scratchpad): a file inside the working tree joins the diff and makes the records stale.

```
cat > "$TMPDIR/simplification.json" <<'JSON'
[{"title": "the same guard runs twice", "file": "platform/app/x.ts", "line": 42,
  "verdict": "fixed", "detail": "one line of what it is and what you did"}]
JSON
.claude/bin/review-record --skill simplification --items-file "$TMPDIR/simplification.json"
```

`verdict` is one of `fixed`, `in-scope`, `out-of-scope`, `no-change-needed`. The gate checks that every
file exists and every line is inside it, so an invented source is refused. A skill that returned nothing
records an empty list.

Judge scope by one rule. A pre-existing problem that the change touches belongs in this pull request. A
pre-existing problem unrelated to the change does not: mark it `out-of-scope` and raise it as a separate
request. "Pre-existing" alone is never a reason to leave it.

## Matching the design

When the phase builds an interface that has a Figma link, measure it. Do not judge it by eye.

1. Seed the local database with the design's own copy. The database is local and one per request, so
   change it freely. Text is how the comparison pairs elements: without the same copy nothing pairs.
2. Read the design: run `~/personal/scripts/gate/figma/extract-design.js` through `use_figma` with the
   frame id. Fetch every node, not the ones that look relevant.
3. Render the built page at the design's width and read it: paste
   `~/personal/scripts/gate/figma/extract-built.js` into the page.
4. Compare:

   ```
   python3 ~/personal/scripts/gate/gate.py figma-compare \
     --design design.json --built built.json --offset-y 76 --tolerance 2
   ```

   `--offset-y` is the height of any mock browser toolbar in the design frame. Check it in the node
   tree: on the iPaaS handoff file the `Chrome / Toolbar` instance is 76px, so the page starts at 76.

Three things the real data taught, which the tool already handles. Do not work around them by hand.

- **Measure text with `Range`, never with the element box.** A Figma text node is as wide as its glyph
  run. An `<h3>` holding the same words is as wide as its column: 1272px against 182px. The element box
  reports a difference that is not there.
- **Never compare the height of text.** Figma reports the line box, the DOM range reports the ink. For
  14px text that is 24 against 17 on text that matches perfectly.
- **Compare the width of a text node only when it hugs its content.** A fixed-width text box in Figma
  reports the box. The card description is an 800px box holding a 525px sentence.

Pass and fail come from the per-edge pixel deltas, not from the overlap ratio. Overlap is printed as
evidence only. On the real runbook list, a title that sits 2.5px low scores 0.70 overlap, while a
1px error on a whole card scores 0.9986. One ratio cannot judge both.

A run that pairs nothing fails. A comparison that compared no element proves nothing.

## Deliberate differences

When the build does not follow the design or the spec on purpose, record it:

```
.claude/bin/deviation --id no-avatar-column --kind design \
  --summary "the avatar column is not built" --reason "the team dropped it from this release" \
  --source "figma 721:23920" --region 1104,98,180,640 \
  --decided-by user --evidence "<the user's words, or the comment URL>"
```

Only the user decides that a difference is deliberate, and `--decided-by user` needs `--evidence`. Without
it the record is a note from you: it explains the difference and it lets no mismatch pass. `--region` is the
page area in pixels that the design comparison crops out and diffs on its own. Ask the user before you
record a design difference. Never record one to make a comparison pass.

`.claude/bin/review-record --section` prints all three lists as markdown. Paste it into the phase section
so the pull request carries every item with its source.

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
.claude/bin/pr-diagram --pr <n> --name phase-1 \
  --png out/phase-1.png --svg out/phase-1.svg --source out/phase-1.excalidraw
```

It puts the files on `review-assets/<pr>-<slug>`, a branch that is never merged, and prints the
markdown to paste. The png is what renders. The svg is the same drawing, scalable, and its text can
be read back. The `.excalidraw` source is what the user opens to edit and to mark.

The user answers a diagram by adding marks to it. Read them with
`~/personal/scripts/excalidraw-marks`, from the svg or from the source. Never read the picture.

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

## Handoff

1. `.claude/bin/agent_task_finalize --phase N` must exit 0. It runs rubocop, yarn and specs in the checks
   worktree and resets it; release any live check (`gate.py checks reset`) first. Fix what it reports and
   rerun. Never hand off on a failure, never skip a check.
2. Commit with `/commit`. Subject: `Request#<id> Phase N: <what>`. One commit per phase. A phase without a
   changeset: `git commit --allow-empty -m "Request#<id> Phase N: <what>"` (the co-author trailer is added by
   the hook, the `Phase: N` trailer by `prepare-commit-msg`). On phases 1 to 5 that hook also writes
   `[skip ci]` into the body, because those phases add no behaviour and CI has nothing to test. Do not write
   a skip keyword yourself on phase 6 or 7: those must run CI, and `pre-push` refuses a commit that carries
   one. When finalize made you fix something after the
   commit, amend that one commit.
3. `git push -u origin HEAD`.
4. The PR.
   - Phase 1 creates it as a draft with `.claude/bin/pr-create --title "Request#<id> <subject>"
     --request <id>`. The body starts as one link to the request and nothing else. A body written at
     creation escaped every check, which is how a description grew to several hundred lines of prose,
     so the guard refuses `gh pr create --body` and `--body-file`.
     Then set the request's `review` custom field to the PR URL
     (`.claude/bin/xurrent-api PATCH /requests/<id> '{"custom_fields":[{"id":"review","value":"<url>"}]}'`).
   - Every phase writes its `## Phase N` section with `.claude/bin/pr-phase`, which reads the live
     description and splices in that one section, leaving everything else byte for byte:

     ```
     .claude/bin/pr-phase --pr <n> --phase N --body-file <file>     # --dry-run shows the change first
     ```

     The file holds the section content only: the done criteria as a ticked checklist, what the phase
     changed in three lines, the output of `.claude/bin/review-record --section` when the phase changed
     code, and the phase's own notes (phase 1 discovery, phase 5 audit). Leave the
     `## Phase N` heading out, the script writes it. The section is appended when it is new and replaced
     in place when the phase comes back to it after review, so it never appears twice.

     Never write a whole body with `gh pr edit --body-file`: that rebuilds the record from memory and
     deletes every section you did not retype, the user's own edits included, and GitHub keeps no
     revision history to restore them from. The guard refuses those calls. `pr-phase` keeps the previous
     description under `~/.local/state/phased/<owner>__<repo>/pr-<n>/descriptions/` before every write.
     `phased handoff` refuses a phase whose section is missing from the description, and refuses one
     whose description lost a section it had at the last handoff.
     The PR description is the record of the work. Nothing about the phases is written into the repository.
5. Xurrent, one PATCH: `source .claude/bin/xurrent-constants`, then
   `.claude/bin/xurrent-api PATCH "/requests/<id>" '{"member_id": <my person id>, "agile_board_column_id": <review column>}'`
   with `REVIEW_COLUMN_IPAAS` when the record's `agile_board.id` is the iPaaS board (3767); otherwise look the
   column named Review up through `/agile_boards/<board>/agile_board_columns`. `<my person id>` comes from
   `.claude/bin/xurrent-api /me`.
6. One internal note: `.claude/bin/xurrent-api POST "/requests/<id>/notes" '{"text": "Phase N: <pr url> (Created|Updated)"}'`.
   Any comment on the pull request itself goes through `.claude/bin/pr-comment`, which stamps the identity
   header and collapses the body. `gh pr comment`, `gh pr review` and the comment API are refused by the guard.
7. `command -v phased >/dev/null && phased handoff --pr <n> --phase N`. Without the daemon this line does nothing.
8. Stop. Your last message: `Phase N handed off: <pr url>. Waiting for an Approved comment on <short sha>.`
   Do not start the next phase. The approval starts it: GitHub refuses a review approval from the author
   of a pull request, so the approver posts a conversation comment whose first line is `Approved`, newer
   than the handoff and than the last commit. The daemon reads it once and starts phase N+1.
