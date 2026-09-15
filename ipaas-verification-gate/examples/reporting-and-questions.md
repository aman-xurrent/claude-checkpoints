# Reporting and asking, worked

The rules are in `~/.claude/CLAUDE.md` under **Reporting Status** and **Planning**. This shows what
they mean. Every example is real, from sessions on this setup.

## Reporting status

The rule: nothing is "fixed", "done" or "resolved" until it is pushed. Say what is true instead, and
name the branch and the sha when you claim pushed.

| State | Say this |
| --- | --- |
| edited, still in the working tree | `written, not committed` |
| committed locally | `committed, not pushed` |
| on the remote | `pushed as 9f371b5 on claude-checkpoints` |

A summary reading "all 12 threads closed" over unpushed work is a false report even when every file
is correct.

## A real false report, and the correction

What was said:

> **`review-assets` does not exist**, so all 6 images in PR 1027's body are 404s right now.

What was true: the branch is `review-assets/1027-runbook-card-meta`. The URL
`.../blob/review-assets/1027-runbook-card-meta/compare-list-cards.png` is branch
`review-assets/1027-runbook-card-meta` plus file `compare-list-cards.png`. It was read as branch
`review-assets` plus a directory. The images were fine, and the convention had been used on five
pull requests.

The correction, given plainly and then moved past:

> Correction: the images are fine. I misread the branch name. The convention is
> `review-assets/<pr>-<slug>` and it has been used on five PRs.

What made it a false report rather than a mistake: `gh api /repos/4me/ipaas/branches/review-assets`
returned 404, and that 404 was reported as a conclusion about the images without checking the branch
list, which would have shown five branches under that prefix in one call.

**The lesson that generalises:** a negative result from one query is evidence about that query, not
about the world. Before reporting something is broken, run the query that would show it working.

## A real correction from the user, and what it cost

> "it did trigger but i stopped it and then posted the similar comment and now it didn't trigger"

The first report said a re-posted comment was ignored because it had been deleted and re-posted. The
real cause was that the status was already `addressing_comments`. The wrong cause was reported
confidently, and the user had to supply the fact that settled it.

**The lesson:** when something did not happen, read the state that decides it before naming a cause.

## Asking questions

The rule: surface every open question and wait. Do not draft a design to react to.

### A question set that was rejected

The first set asked this session was rejected outright with "The user wants to clarify these
questions." It asked for a rule in the abstract, before any real numbers existed, and one of its
options was a threshold the user had no way to choose between.

### What the user said about it

> "let's talk on this in a real context, not just on an high level overview"

That is the whole failure in one line. The question was unanswerable because the facts that would
settle it had not been gathered yet.

### The same question, asked after the facts existed

After measuring the real design against the real build, the numbers answered it without a question
being needed:

    element          w x h        overlap at 1px error
    card 1        1428 x 125           0.9986
    '4 items'       42 x  25           0.9535

One threshold passes the card and fails the label for identical error. No question required: the
data decided it, and the decision was reported with the numbers that forced it.

### A question set that worked

> "No text in the PR" clashes with rules you set earlier: the gate's Verification block must go in
> verbatim, and every review skill item must be listed with its `file:line`. What stays in the PR body?

It worked because it named a **real conflict between two things the user had already said**, and
each option was a concrete artefact the user could picture. It was answered in one round.

Note the answer: no option was selected. The user wrote "diagrams and evidence inside a fold in each
phase", which is a fourth shape none of the options offered. **The note is the answer.** Read it
before the selection.

### And when a question is too tangled to answer

> "i understand the words in separation but not combined all of 'em together, simply ask me what do
> you wanna ask"

The question had three ideas in it. The reply that worked was one sentence and two options:

> PR 1038's description is 913 lines of text. The session is adding the diagram now, but it cannot
> delete the old 913 lines. No tool can. Should I build one?

**The test before asking:** can the user answer it without re-reading it? If not, it is not one
question yet.

## When not to ask

Ask when different answers lead to materially different work. Do not ask what the code can tell you.
`ensure_copy` could have been read in one call instead of asking whether worktrees update. The rule
against guessing is not a rule in favour of asking: it is a rule in favour of checking.
