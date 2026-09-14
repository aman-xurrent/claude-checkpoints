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

## Review skills

Three skills look at every code change before it is handed off. Run all three whenever the phase changed
code, in every session, not only at handoff. The gate fails `agent_task_finalize` until each one has a
record that matches the current diff.

| Slug | Skill | What it looks for |
| --- | --- | --- |
| `simplification` | `/agent-skills:code-simplification` | the change is as small as it can be |
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

`.claude/bin/review-record --section` prints all three lists as markdown. Paste it into the phase section
so the pull request carries every item with its source.

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
   - Phase 1 creates it as a draft:
     `GH_HOST=git.4me.com gh pr create --draft --title "Request#<id> <subject>" --body-file <file>`.
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
