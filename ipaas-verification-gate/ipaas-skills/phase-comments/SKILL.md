---
name: phase-comments
description: Address the review comments on the current phase's draft PR: fix each unresolved thread, reply in it, resolve it, run finalize, push, and hand the request back. Started by the phase daemon when new comments arrive.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent
---

# Review comments on the current phase

The daemon's brief lists two kinds of feedback: review threads anchored to a line, and conversation comments
on the pull request itself. Both are requests from the approver. `N` is the current phase
(`.claude/proof/phase`), `<n>` the PR number.

1. **The user decides every comment before you fix any of it. You decide nothing here.**

   ```bash
   .claude/bin/comment-triage fetch --pr <n>
   ```

   This prints every unresolved thread with the reviewer's own words and gives each one a token,
   `C1`, `C2`, `C3`. Read every item and the code it points at. The gate refuses every edit to code
   and specs until each thread carries the user's mark, so there is nothing to do first except draw.

2. **Draw one triage diagram**, one column per thread, on one spine. Follow
   `~/personal/scripts/gate/diagram-convention.md`. Each column carries, inside the drawing:

   - the token `C<n>` in the heading, and the reviewer's **own words, quoted whole**, never a summary;
   - the file and line, and the **current code quoted verbatim** from a named ref;
   - the change you propose, as a before and after diff in the green panel. Green is a proposal,
     never applied code;
   - **only when there is a real fork**, one panel per approach with its own diff, and a line
     saying what each approach costs and what it gives up. Do not invent approaches to look
     thorough. One obvious fix is one panel;
   - whether you think it belongs in this pull request, with the reason, so the user can disagree;
   - a line `C<n> decision:` in the gutter under the column, with empty space beside it.

   The user must be able to decide without opening an editor, the pull request, or a browser. A
   pointer is not content. Write every label in plain, simple English.

3. **Stop and wait.** Tell the user the file path and nothing else of substance. Do not fix, do not
   reply on a thread, do not resolve one.

4. When the user says they marked it:

   ```bash
   .claude/bin/comment-triage decide --pr <n> --marks ~/.claude/excalidraw/<file>.excalidraw
   ```

   `R:IN` fixes it in this pull request. `R:OUT` keeps it out of this pull request: raise it as a
   separate request and say so in the reply. `R:<text>` is an instruction: do what it says and
   answer it in chat. A mark that names no `C<n>` records nothing and the command refuses, so fix
   the mark with the user rather than guessing which comment it meant.

5. Fix only what the user marked for this pull request, within the phase's rules (phase 2 structures
   only, phase 3 contracts only, and so on). When an item asks for something the phase forbids, say
   so in the answer and leave it for the phase it belongs to. A comment that names a skill (for
   example `/agent-skills:code-simplify` or `/edge-case-hunter`) means run that skill and fold its
   findings in.

6. Verify: `.claude/bin/agent_task_finalize --phase N` must exit 0.
7. Answer every item. Every comment you post goes through `.claude/bin/pr-comment`, never through
   `gh pr comment` or the API: the wrapper stamps who posted it (phase, branch, worktree, tmux session) and
   folds your text into a collapsed block, so a reader can tell it from a comment the account holder typed.
   The guard refuses the raw calls. Write the answer to a file first, then:

   ```bash
   .claude/bin/pr-comment --pr <n> --title "Phase N: <what you answered>" --body-file /tmp/answer.md
   .claude/bin/pr-comment --pr <n> --thread <thread id> --title "Fixed" --body-file /tmp/reply.md
   ```

   The title is the one line a reader sees; the body carries what changed, file and line, one item per point.
   Resolving a thread is not a comment and stays a direct call:

   ```bash
   GH_HOST=git.4me.com gh api graphql -f thread=<thread id> -f query='mutation($thread:ID!){
     resolveReviewThread(input:{threadId:$thread}){ thread{ isResolved } } }'
   ```
   Do not reply on threads you did not address. Do not resolve a thread you disagree with; answer it and
   leave it open for the approver.
8. Commit: amend the phase commit when the fix is small and the phase has one commit; otherwise
   `Request#<id> Phase N: address review comments`. Push with `--force-with-lease` after an amend.
9. `phased handoff --pr <n> --phase N`, then the Xurrent PATCH and the internal note from
   `.claude/skills/phase/ceremony.md` (member = me, Review column, `Phase N: <pr url> (Updated)`).
10. Stop. `Phase N updated after review: <pr url>. Waiting for an Approved comment on <short sha>.`
