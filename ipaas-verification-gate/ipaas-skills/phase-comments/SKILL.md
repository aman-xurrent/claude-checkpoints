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

1. Read every listed item and the code it points at. Do what it asks within the phase's rules (phase 2
   structures only, phase 3 contracts only, and so on). When an item asks for something the phase forbids,
   say so in the answer and leave it for the phase it belongs to. A comment that names a skill (for example
   `/agent-skills:code-simplify` or `/edge-case-hunter`) means run that skill and fold its findings in.
2. Fix, then verify: `.claude/bin/agent_task_finalize --phase N` must exit 0.
3. Answer a conversation comment with a conversation comment naming what changed, file and line:

   ```bash
   GH_HOST=git.4me.com gh pr comment <n> --body "<what changed, file and line, one item per comment point>"
   ```

   Reply in each review thread with what changed, then resolve it. Both go through GraphQL on
   git.4me.com; `gh pr-review` replies are refused there:
   ```bash
   gh api graphql -f thread=<thread id> -f body="<reply>" -f query='mutation($thread:ID!,$body:String!){
     addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$thread, body:$body}){ comment{ url } } }'
   gh api graphql -f thread=<thread id> -f query='mutation($thread:ID!){
     resolveReviewThread(input:{threadId:$thread}){ thread{ isResolved } } }'
   ```
   Do not reply on threads you did not address. Do not resolve a thread you disagree with; answer it and
   leave it open for the approver.
4. Commit: amend the phase commit when the fix is small and the phase has one commit; otherwise
   `Request#<id> Phase N: address review comments`. Push with `--force-with-lease` after an amend.
5. `phased handoff --pr <n> --phase N`, then the Xurrent PATCH and the internal note from
   `.claude/skills/phase/ceremony.md` (member = me, Review column, `Phase N: <pr url> (Updated)`).
6. Stop. `Phase N updated after review: <pr url>. Waiting for an Approved comment on <short sha>.`
