# Diagram convention

The reference is `~/.claude/excalidraw/jamf-review-findings.excalidraw`. Every diagram follows it.
Open it before you draw.

## The rule that decides everything else

**The reader never leaves the diagram.** Whatever they must read to understand the point or to make
a decision is inside the drawing: the code itself, verbatim; what the code does; what is wrong with
it; the proposed change. A reader must never have to open an editor to look up a line number.

A label that says `solution_exporter.rb:15` and nothing else has failed. Show line 15.

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
