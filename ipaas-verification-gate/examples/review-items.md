# Review items and commit messages, worked

Both are evidence you produce for someone else to check. The gate validates the shape of a review
item; nothing validates a commit message, so the example is the only guard it has.

## A review item

Recorded with `.claude/bin/review-record`. The gate refuses an item without a title, a file, a line
and a verdict, and checks that the file exists and the line is inside it. That stops an invented
source. It cannot stop a useless one.

### Real items, from a real run

These came from `/agent-skills:code-simplify` over `gate.py`, and all four were applied:

```json
[
  {"title": "deviation is 44 lines doing three jobs",
   "file": "personal/scripts/gate/gate.py", "line": 1856,
   "verdict": "fixed",
   "detail": "parse, validate and write in one function. Split into deviation_problems (the checks) and deviation (the write)."},

  {"title": "the not-inside-a-git-repository stanza is written four times",
   "file": "personal/scripts/gate/gate.py", "line": 1489,
   "verdict": "fixed",
   "detail": "finalize, review_record, review_section and deviation each repeat it. One require_repository(command) helper."},

  {"title": "print the problems and stop is written twice",
   "file": "personal/scripts/gate/gate.py", "line": 1662,
   "verdict": "fixed",
   "detail": "review_record and deviation. One refuse(command, problems, code) helper. Note review-record documents exit 1, so the code is a parameter."}
]
```

### Why they pass

- **The title says what is wrong**, not what the file is. "deviation is 44 lines doing three jobs",
  not "deviation function".
- **The line points at the defect**, not at the top of the file.
- **The detail says what you did**, concretely enough to check without opening the diff.
- The third one carries the trap it found: the shared helper would have changed a documented exit
  code. An item that names the trap is worth more than one that names the fix.

### What fails

```json
{"title": "consider refactoring for clarity", "file": "gate.py", "line": 1, "verdict": "no-change-needed"}
```

It passes the gate's checks and teaches nobody anything. No defect, no site, no action. Line 1
because a line was required, which is the tell.

Also failing, and more common: reporting three items when the skill returned eleven. The rule is
every item the skill returned. A skill that returned nothing records an empty list, and that is a
real answer.

### Scope

A pre-existing problem the change touches belongs in this pull request: mark it `in-scope` or
`fixed`. A pre-existing problem unrelated to the change is `out-of-scope`, and it is raised as a
separate request rather than dropped. "Pre-existing" alone is never a reason to leave it.

## A commit message

The shape is the same as a PR body used to be: what the problem is, why it happens, how the change
solves it, and how it was verified. Plain language a novice can follow, no em dashes.

### A real one

```
Stop the daemon from typing into the wrong Claude session

What this fixes
The daemon remembers which tmux window holds a pull request's Claude session. For
PR 1027 that remembered id was @3, but @3 is a different window: a plain shell
window named zsh, in the main clone, running the user's own Claude session. The
real pr1027 window is @4. Delivering feedback for 1027 would have typed the
prompt into the wrong session, in the wrong directory.

Why it happened
find_live_window trusted the remembered id after two checks: the window still
exists, and it runs Claude. It never asked whether it is the right window. tmux
reuses a window id after a window closes, so a stale id can land on any other
Claude session and win, because every Claude window passes both checks.

How this change solves it
The remembered id must also still carry the expected window name (`pr<number>`).
When it does not, the lookup falls through to the search by name, which finds the
real window. The feedback path already writes the corrected id back to state, so
the drift heals the first time it fires.

Verified
Before: find_live_window("ipaas", "pr1027", "@3") returned @3, the wrong session.
After: it returns @4, the real pr1027 window. PR 1038's correct id @7 is
unchanged. The 58 phased tests pass.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

### Why it passes

- **The subject says the consequence**, not the edit. "Stop the daemon from typing into the wrong
  Claude session", not "Add a name check to find_live_window".
- **"What this fixes" is concrete**: the real ids, the real windows, what would have happened.
- **"Why it happened" names the wrong question**, which is the transferable part. Two checks were
  made and a third was missing. A reader learns something even if they never touch this function.
- **"Verified" gives the before and after as values**, not as a claim that it works.

### What fails

- "Fix window lookup bug." Says nothing about what broke or what would have happened.
- "Why it happened: there was a bug in find_live_window." Restates the subject.
- "Verified: tested and working." No input, no output, nothing to check.
- Narrating the diff in the source instead: `# added to fix the stale id`. That belongs here, never
  in the code. The source describes the current state.
