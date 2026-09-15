# A failure trace, worked

The rule is in `~/.claude/CLAUDE.md` under **Tracing a Failure**. This shows what it means.

Everything here is real. It was written for request 81848811 and every line number was verified
against `origin/main` at `546e5dea`.

## The shape

Four parts, in order: where it enters, every hop, the failure point, the way back out. Every claim
carries `file:line`. A step without a source is not a step, it is a guess.

## What a complete trace looks like

The question was: what does a user see when they act on a locked solution? The answer is not "it
fails". It is five different failures, each with its own path, and the difference between them is
the whole point.

> What the user actually sees today, per action, all verified:
>
> | Action | While the solution is locked | Why |
> |---|---|---|
> | Open the Edit page | "Please wait", auto-reloads after 5s, gives up after 3 tries | full-page Inertia GET, so `errors_helper.rb:190` sets `X-Solution-Busy` and `SolutionBusyDialog.tsx:58` fires |
> | Import | Same busy dialog | `ImportSolutionButton.tsx:12` is a `Button href` → Inertia GET |
> | Save, Delete | Toast: "This solution is busy with another change." | non-GET Inertia → bare `head(:conflict)` at `errors_helper.rb:191` → `useConflictToast.ts:10-14` |
> | Test GitHub Connection, Sync now | Toast reading literally "Conflict" | `useRequest` raw `fetch` with `Accept: application/json` → `errors_helper.rb:187-188` renders `Rack::Utils::HTTP_STATUS_CODES[409]` |
> | **Export** | **A 409 whose body is an HTML page, with no in-app feedback of any kind** | `EditSolutionPage.tsx:40` is a plain `<a download>` (`Button.tsx:103-112`), so `request.inertia?` is false and `errors_helper.rb:193` renders `public/409.html` (14012 bytes, a real styled page) |

## Why that passes

- **Every row names the entry point.** `ImportSolutionButton.tsx:12`, `EditSolutionPage.tsx:40`. Not
  "the import button".
- **Every row names the branch that was taken and why.** `request.inertia?` is false *because* it is
  a plain `<a download>`. The cause of the branch, not only the branch.
- **The hops are clickable in order.** `EditSolutionPage.tsx:40` → `Button.tsx:103-112` →
  `errors_helper.rb:193` → `public/409.html`. A reader can follow it without searching.
- **The four neighbours are traced too.** The defect is only legible next to the four paths that
  behave differently. One row would have hidden that.
- **It carries what the user sees**, in their words: "Please wait", "Conflict", a toast, a styled
  page. Not only what the code does.
- **It quantifies.** `public/409.html` is 14012 bytes and a real styled page, so a reader knows this
  is not a blank error.

## The same author, saying what is not known

Two limits, stated plainly rather than papered over:

> `render file:` (`errors_helper.rb:193`) sets no `Content-Disposition`, and `Button.tsx:108` emits a
> bare `download` with no value, so there is no filename from either side; a browser would fall back
> to the last URL segment, `export`, with no extension. And whether a browser writes a non-2xx body
> to disk under a `download` navigation differs by engine, so this plan does not assert it as fact.

That is the rule "say unknown out loud" in practice. It names the unknown, names the two sources that
bound it, and refuses to assert the part that depends on the engine.

## And the reproduction

> Reproduction, no UI needed: `GET /solutions/:uuid/export` while a promote holds the lock waits three
> seconds on the `timeout_seconds: 3` default (`solution.rb:258`) and then serves `public/409.html`.

One line, no UI, with the source of the timeout. A reader can run it.

## What fails the rule

- "The controller checks whether the solution is busy and returns an error." No source, no branch,
  no hop. Three guesses in one sentence.
- "`errors_helper.rb:193` renders the 409 page." A source with no explanation of how execution
  reached it, and nothing about what the user sees.
- Line numbers recalled from a previous session. Verify against the ref you name, every time, and
  name the ref. Six cited files moved when 1038's branch was fast-forwarded from `9d5fb45b` to
  `546e5dea`, and every citation in them had to be re-checked line by line.
