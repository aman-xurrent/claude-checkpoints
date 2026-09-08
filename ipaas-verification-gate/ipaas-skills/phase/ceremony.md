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
   - Every phase appends a `## Phase N` section to the description: the done criteria as a ticked checklist,
     what the phase changed in three lines, and the phase's own notes (phase 1 discovery, phase 5 audit).
     Write the whole body to a file and `GH_HOST=git.4me.com gh pr edit <n> --body-file <file>`.
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
