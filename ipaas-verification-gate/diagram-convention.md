# Diagram convention

The reference is `~/.claude/excalidraw/jamf-review-findings.excalidraw`. Every diagram follows it.
Open it before you draw.

## The rule that decides everything else

**The reader never leaves the diagram.** Whatever they must read to understand the point or to make
a decision is inside the drawing.

**A pointer is not content.** That covers every kind of pointer, not only code:

| Written | Why it fails | Write instead |
| --- | --- | --- |
| `solution_exporter.rb:15` | they must open an editor | the line itself, quoted |
| "the decision in 4.1.d" | they must open the document | the decision, stated |
| "see the plan for phase 3" | they must find the plan | what phase 3 does |
| "per the request" | they must open the request | the sentence from the request |
| "as the reviewer noted" | they must find the comment | what the reviewer said |
| "the usual lock pattern" | they must know it already | the pattern, drawn |
| "#16 above" | they must scroll and hold it | restate it here |

If the reader has to go anywhere at all, including elsewhere in the same diagram, it has failed.
Each finding stands alone.

The test: hand the diagram to someone with no editor, no browser and no access to the repository.
Can they understand the point and make the decision? If not, something is still a pointer.

This is why a finding carries the code verbatim, one line of what it does, what is wrong with it,
and the change as a before and after. Not because code is special, but because the code was the
thing the reader would otherwise have gone looking for.

## Dark, and what that changes

Diagrams are dark. `jamf-review-findings.excalidraw` is the reference for **anatomy**: the column,
the spine, the finding block, what goes in each panel. Its colours are from before this rule, so
read its shape and use the palette below, not its colours.

`pr-diagram` measures the rendered image and refuses one whose mean brightness is above 0.35. The
light diagrams this replaced measured 0.82 and 0.94.

| Part | Dark value | was |
| --- | --- | --- |
| page background | `#0b1220` | white |
| title | `#f1f5f9` | `#0f172a` |
| byline | `#94a3b8` | `#64748b` |
| summary line | `#cbd5e1` | `#334155` |
| trust rules | `#64748b` | `#94a3b8` |
| status panel | `#111c33` on `#334155` | `#f8fafc` on `#cbd5e1` |
| spine | `#475569` | `#94a3b8` |
| stage letter and name | `#e2e8f0` | `#0f172a` |
| stage ring | `#3b82f6` fill, `#1e3a5f` stroke | unchanged |
| stage description | `#64748b` | `#94a3b8` |
| section header | `#60a5fa` | `#1e40af` |
| section source file | `#64748b` | `#94a3b8` |
| section description | `#94a3b8` | `#64748b` |
| finding statement | `#cbd5e1` | `#475569` |
| blocker dot | `#f87171` | `#b91c1c` |
| major dot | `#fbbf24` | `#b45309` |
| minor dot | `#94a3b8` | `#475569` |
| simplification dot | `#a78bfa` | `#7c3aed` |
| status pill, stands | `#15803d` | unchanged |
| status pill, dropped | `#475569` | `#64748b` |
| pill label | `#ffffff` | unchanged |
| finding locator | `#64748b` | `#b0b7c3` |
| **code panel** | `#020617` on a `#1e293b` border | `#0f172a`, no border |
| code caption | `#94a3b8` | unchanged |
| code body | `#e2e8f0` | unchanged |
| **proposal panel** | `#052e1a` on `#059669` | `#f0fdf4` on `#059669` |
| `Reviewer suggests:` | `#34d399` | `#047857` |
| `The change` | `#10b981` | `#059669` |
| the diff | `#6ee7b7` | `#065f46` |

The code panel needs its own border now, because a dark panel on a dark page has no edge without
one. Everything else keeps its job: green is still the proposal, the pill is still the status.

## Export

Never export the png by hand. `pr-diagram` renders it from the svg, so the two can never disagree
about what the diagram says.

- **svg at 1x.** The Excalidraw export writes `width` and `height` at twice the `viewBox`, so a 1790
  wide drawing is written as 3580. `pr-diagram` rewrites them to match the viewBox.
- **png at 3x**, rendered with `rsvg-convert` from that 1x svg.
- **The `.excalidraw` source never goes on the branch.** Only the png and the svg. Keep the source in
  `~/.claude/excalidraw/`. `pr-diagram` refuses a `--source` flag.

## Shape

One tall column. A 1px vertical spine (`#94a3b8`) at x=300 running the full height. Content to the
right of the spine, stage markers to the left of it.

- **Header**, once at the top: the title at 22px, then one line naming the source and the date, then
  the rules the reader needs to trust the content (where the code was quoted from, what was
  shortened, what is unverified), then the legend, then a summary panel (`#f8fafc` on `#cbd5e1`)
  saying where things stand.
- **Stage markers**, in the left gutter: a short capital label (`A INSTALL`, `B CONFIG CHANGE`) with
  one plain line underneath saying what happens at that stage in the user's terms. A ring on the
  spine marks it (`#3b82f6` stroke, `#1e3a5f` fill, 16px).
- **Sections**: a 15px header in `#1e40af`, with the source file right-aligned in `#94a3b8`, then one
  grey line saying what that unit does.
- **Findings**, repeated. Each one is the block below.

## One finding block

1. **Severity dot**, 11px, filled: `#b91c1c` blocker, `#b45309` major, `#475569` minor,
   `#7c3aed` simplification.
2. **The statement**, 12.5px sans, one or two lines: what is wrong, in plain words.
3. **Status pill**, a 150px rounded rectangle on the right with white 10.5px text:
   `#15803d` for a decision that stands, `#64748b` for one that was dropped.
4. **Source locator**, far right, 10.5px `#94a3b8`: the file and the line range.
5. **The code panel**: a rectangle filled `#0f172a`. A caption at the top in 10.5px `#b0b7c3` giving
   the file, the line range, and one line of what the code does. Then the code itself, 11.5px
   monospace (fontFamily 3) in `#e2e8f0`, **verbatim from the file**, with `#` comments inline
   pointing at the defect.
6. **The proposal panel**: a rectangle filled `#f0fdf4` with a `#059669` border. `Reviewer suggests:`
   plus the fix in one line, 12px `#047857`. Then `The change` in 10.5px `#059669`. Then the change
   as a diff, 11.5px monospace in `#065f46`: the `before:` lines, then `after:` or `fixed:`, with
   `#` commentary.

Green is a proposal, never applied code. Say that once in the header.

## Rules that hold everywhere

- Quote code verbatim. Name where it was quoted from, and say so in the header. If a line was
  shortened to fit, mark it (`...` or `->`) and say in the header that you did.
- Every line number is read from a stated ref, never from memory. Name the ref.
- Mark anything you could not verify `UNVERIFIED` and say what would settle it.
- Each finding stands alone. No "as in step 2".
- Draw nesting as nesting: a call inside another call is a labelled dashed panel with the method and
  its line range, never flattened into siblings.
- Verify the real call order in the code before drawing it.
- Create background panels before their contents, because z-order is creation order.
- A `line` needs `width: 1`, not `0`, or it renders diagonally.
- Never set `width` or `height` on a text element. Excalidraw wraps to the declared width and hides
  the rest, so labels are silently cut.
- Screenshot and audit after every create and every edit: text spilling out of a panel, labels
  colliding, arrows detached, and above all a part of the diagram that now contradicts the part you
  just changed.

## Sizes

| Use | Size | Family | Colour |
| --- | --- | --- | --- |
| title | 22 | sans | `#0f172a` |
| section header | 15 | sans | `#1e40af` |
| finding statement | 12.5 | sans | `#475569`, or the severity colour |
| proposal headline | 12 | sans | `#047857` |
| code | 11.5 | mono | `#e2e8f0` on dark, `#065f46` on green |
| caption, locator, pill | 10.5 | sans | `#b0b7c3`, `#94a3b8`, `#ffffff` |

## Geometry, as measured from the reference

x positions are fixed. Copy them.

| Part | x | Size / colour |
| --- | --- | --- |
| title | 40 | 22 sans `#0f172a` |
| byline | 40 | 12 sans `#64748b` |
| summary line | 40 | 12 sans `#334155` |
| legend dots | 1438, 1524, 1610, 1696 | 11px ellipse, severity colour, filled |
| legend labels | dot x + 18 | 12 sans, the severity colour |
| trust rules | 40 | 11 sans `#94a3b8` |
| status panel | 40, width 1770 | `#f8fafc` on `#cbd5e1`; its title at 56, 13 sans |
| **the spine** | **300**, width 1 | `#94a3b8`, full height |
| stage letter and name | 40 | 13 sans `#0f172a`, two lines |
| stage ring | 291 | 16px ellipse, `#3b82f6` fill, `#1e3a5f` stroke |
| stage description | 40 | 10.5 sans `#94a3b8` |
| section header | 344 | 15 sans `#1e40af` |
| section source file | 1500 | 11 sans `#94a3b8` |
| section description | 344 | 12 sans `#64748b` |
| finding dot | 362 | 11px ellipse, severity colour, filled |
| finding statement | 384 | 12.5 sans `#475569` |
| status pill | 1200, width 150, height 19 | `#15803d` or `#64748b` |
| status pill label | ~1256 | 10.5 sans `#ffffff` |
| finding locator | 1500 | 10.5 sans `#b0b7c3` |
| code panel | 400, width 500 to 740 | `#0f172a` |
| code caption | 414, panel y + 8 | 10.5 sans `#94a3b8` |
| code body | 414, panel y + 24 | 11.5 **mono** `#e2e8f0` |
| proposal panel | 400, width 320 to 730 | `#f0fdf4` on `#059669` |
| `Reviewer suggests:` | 414, panel y + 8 | 12 sans `#047857` |
| `The change` | 414, panel y + 27 | 10.5 sans `#059669` |
| the diff | 414, panel y + 43 | 11.5 **mono** `#065f46` |

Vertical rhythm inside one finding, from the statement's y: dot at +3, pill at -2, code panel at
+42, proposal panel at +190.

## A finding, exactly as the reference draws it

This is one real block, copied out of `jamf-review-findings.excalidraw`. Match this.

**The statement**, 12.5 sans `#475569` at x=384, with an `#475569` dot at x=362:

    #32  MINOR  ·  The concurrency key is routed through request_body.customer_id, which only Create Schedule sets. A missing
    value removes the lock instead of failing.

**The pill** at x=1200 on `#64748b`, label `DROPPED`. **The locator** at x=1500 in `#b0b7c3`:

    Installed line 42, Sync lines 94-95

**The code panel**, `#0f172a` at x=400. Caption in 10.5 `#94a3b8`, then the code verbatim in
11.5 mono `#e2e8f0`. Note that the caption says what the code *does*, and the `#` comments inside the
code say what is *wrong*:

    the lock is keyed on a field only Create Schedule fills in

    # App Installed line 42
    - field_id: request_body
      proc: '{"customer_id" => trigger_output&.dig(:customer_account_id)}'

    # Synchronize Devices lines 94-95 -- the concurrency key reads that body
    - field_id: job_context_identifier_path
      fixed: customer_id            # absent value -> no lock, instead of a failure

**The proposal panel**, `#f0fdf4` bordered `#059669` at x=400. Headline 12 `#047857`, the label
`The change` in 10.5 `#059669`, then the diff in 11.5 mono `#065f46`:

    Reviewer suggests:  point job_context_identifier_path at schedule_reference.

    The change

    # Sync lines 94-95, before:
    - field_id: job_context_identifier_path
      fixed: customer_id

    # after:
      fixed: schedule_reference
    # every path that starts the runbook must then put schedule_reference in the body

Read what that block gives the reader. The defect in one sentence, the real code that causes it with
the offending line called out inline, and the exact edit that fixes it. Nothing sends them to an
editor.

## A section header and a stage marker, from the reference

Stage marker at x=40 in 13 sans, two lines, with a ring on the spine at x=291 and a plain
description under it in 10.5 `#94a3b8`:

    A
    INSTALL

    the customer clicks Install on
    the Jamf app

Section header at x=344 in 15 `#1e40af`, its source file right-aligned at x=1500 in 11 `#94a3b8`,
then one line at x=344 in 12 `#64748b` saying what the unit does:

    App Installed runbook · create-schedule            App Installed · …-79ea-80ab-0d8ca8c6d6cd.yaml
    Creates the recurring daily sync and one immediate catch-up run about a minute later.

## The header, from the reference

Title, byline, what must be done, the legend, then the rules that let the reader trust the content.
That last block is not optional: it is how the reader knows the code is real.

    Jamf CMDB solution — the 37 review findings on request #82428691, placed where each one bites

    Reviewer: <name>, 8 Sep 2026 · reviewed the staging export against the Jamf connector contract
    and the platform source · recommendation: hold the release

    Must do before release: 3 blockers + 13 majors (#1–#16).   Minors and simplifications (#17–#37)
    are optional.   Each finding carries the code it turns on, the reviewer's suggestion, and a
    concrete change.   Severity key:            ● Blocker  ● Major  ● Minor  ● Simplification

    Code is quoted from the working-tree solution at platform/tmp/dev-git/accounts/1/solution-01a0…,
    line numbers as read there.
    Every snippet line is verbatim from that file, except lines carrying … or -> , which are
    shortened to fit, and the four request fields in #16, which come from the API.
    Green boxes are proposals, not applied code. Every method used in them was checked against the
    proc allow list in connector/lib/ipaas/connector/common/proc_rules/valid_methods_rule.rb.
    Three are marked UNVERIFIED, where a platform capability still needs confirming.

Then a status panel, `#f8fafc` on `#cbd5e1`, 1770 wide, saying where every finding stands today.
