# Tool verdicts: 10 repos read at source level

Goal: verification gates that stop Claude declaring done when a change missed call
sites, missed a spec, or rests on a flaky or vacuous spec.

## KEEP

ripgrep        (already installed)  Recall layer. Nothing beat it. Sees the working
                                    tree instantly, respects .gitignore, no server.
ast-grep       MIT                  Unprovability detector. Shape-matches dispatch
                                    sites containing none of the symbol's characters.
                                    Unique capability. Integrate via CLI or pyo3,
                                    never @ast-grep/napi (excludes Ruby).
serena         MIT                  Precision layer. ruby-lsp first class.
                                    serena start-project-server = curl-able HTTP.
                                    3 projects, 1 daemon. Fails open on index timeout.
universal-ctags (NOT INSTALLED)     The defs/refs split. zoekt and opengrok both
                                    shell out to it anyway. Install it.

## OPTIONAL, only if cross-project indexed search is needed

sourcebot      FSL-1.1-ALv2         RUN it, do not copy it. FSL Permitted Purpose #1
                                    is verbatim "for your internal use and access".
                                    POST /api/search is FSL core, no entitlement
                                    check, no repo arg, returns isSearchExhaustive.
                                    MCP server IS gated (verified). We don't need it.
                                    Tripwire: selling this system voids internal use.
zoekt          Apache-2.0           Kind/Parent on every match. Sees dirty tree.
                                    No incremental index. Walks filesystem, so it
                                    would index the 4077 .rb files in .claude/worktrees.
                                    Silently yields zero symbols if ctags is missing:
                                    ALWAYS pass -require_ctags.

## STEAL THE IDEA, BUILD NOTHING

kythe          Apache-2.0           Assertions as comments beside the code they
                                    constrain. !{ } = "must not exist any more".
                                    Nonzero exit. No Ruby indexer exists.
Glean          BSD-3                Unused | Enumerated | Used. "Enumerated" is
                                    exactly our requirement 2. No Ruby indexer.
                                    Nothing in the open tree ever writes that value.
sourcebot      (see above)          The N+1 exhaustiveness trick: ask the engine for
                                    N+1 while displaying N, compare. Real truncation
                                    signal, not a guess. Also the ToolDefinition
                                    record shape and descriptions in sibling .txt.
opengrok       CDDL-1.0             full-minus-refs = the population that appears
                                    ONLY inside string literals. A second, independent
                                    unprovability signal orthogonal to ast-grep's.

## DISCARD

hound          MIT     Gzip-copies the tree and greps the copy. Zero symbol awareness.
                       rg beats it on every axis.
livegrep       BSD-2   Resident gRPC server, full rebuild per change, macOS arm64 is
                       second class (fswatcher unsupported). Truncates at 50 by default.
opengrok       CDDL    No headless query path at all. And a real defect: index side
                       uses RubySymbolTokenizer, query side uses PlainSymbolTokenizer
                       ([a-zA-Z_]\w*), so @user, :destroy and valid? are indexed but
                       unfindable. Large fraction of Rails symbols.
mcp-language-server BSD-3  stdio MCP only, no script entry, single root, launches no
                       Ruby server, readiness is time.Sleep(1s) with a TODO.

## UNCHANGED AFTER ALL TEN

Gates 3 (bind change to spec, revert, require red) and 4 (flake detection) have ZERO
coverage in every repo. Grepped each. That half we write ourselves.

## MEASURED TRAPS

- 5723 .rb on disk vs 1375 tracked. 4077 are in .claude/worktrees. Any filesystem
  walker indexes throwaway copies of your own code.
- Dynamic dispatch is concentrated, not pervasive: 654 `send` but 615 in specs,
  39 in app code. ~73 app-code dynamic sites total, clustered in the connector DSL.
- ast-grep silent-pass traps: a rule with no `kind` is dropped with a clean exit code;
  a pattern that fails to parse matches EVERYTHING; ERB is unreachable.
