# Say the thing, not its label

Real pairs from one session. The left column is what was written. The right column is what the
reader needed in order to read the sentence without going somewhere else first.

## The rule in one line

A label is an address, not a name. The label may sit beside the thing. It may never stand in for it.

## Chat replies

| Written | Write instead |
| --- | --- |
| "Item 2 fixed and verified." | "The pull request description no longer loses its fold when a section is replaced." |
| "Items 5 and 6 are claims from another agent." | "The memoised `run_action` and the missing placeholder are claims from another agent." |
| "Item 1 + 3, the one that mattered." | "Measuring the proof from the pull request's own base, and refusing a stale base, both landed." |
| "C3 decided." | "You marked the block-scope sweep as fix in this pull request." |
| "O1 is still open." | "Whether a real folder id is ever read is still open." |
| "Phase 2 of 7 across nine." | "Nine pull requests have their action declared and nothing built yet." |
| "The 4.1.d decision." | "The decision to keep the token as a whole address." |
| "Finding 3 is not caught." | "The spec whose own narrowing regex never fires is not caught." |

## The label is welcome, next to the thing

Do not strip the label. It is how the reader finds the thing again. Put it beside the words:

- "**C3**, the spec that lets one block vouch for another, is still green with the guard removed."
- "**PR 1059**, the connector itself, is the only one with real behaviour in it."
- "**Phase 5**, the throwaway build against the real tenant, is where the icon bug surfaced."

## First mention carries the words, always

A later mention may shorten, but only while the words are still on the same screen. A summary at the
end of a long answer is a first mention again, because that is often the only part that gets read.

Wrong, because the summary is where the labels came back:

```
...long answer explaining each finding in full...

So: 1 and 4 are mine, 2 and 3 are yours, 5 is out of scope.
```

Right:

```
So the orphaned webhook and the tautological signature are mine, the two vacuous
guards are yours, and the shared-mailbox gap is out of scope.
```

## Tables and lists

A table column of bare labels is the same defect in another shape. The label earns its column only
when a words column sits next to it.

Wrong:

| Item | Status |
| --- | --- |
| C1 | fixed |
| C2 | out of scope |

Right:

| | What it is | Decision |
| --- | --- | --- |
| C1 | deprovision keeps the webhook id on a 429 | fix in this pull request |
| C2 | the dropdown spec cannot fail | out of scope, raised separately |

## Where this comes from

It is the diagram rule, applied to everything else. A diagram must not send the reader to an editor,
and a sentence must not send the reader back up the page. Both are the same demand: the reader
should not have to hold a lookup table in their head to read what you wrote.
