#!/usr/bin/env python3
"""Verification gate for Claude Code work on ipaas.

Subcommands
  setup       Create the proof worktree and the gate database. Run once.
  launch      PostToolUse hook. Snapshots the diff and starts a detached prover
              when the proof declaration is written.
  prove       Run the revert proof for one snapshot. Started by launch.
  stop        Stop hook. Advisory summary of the declaration and the findings.
  surface     UserPromptSubmit hook. Prints findings that were not shown yet.
  commit-msg  prepare-commit-msg git hook. Stamps proof trailers on the commit.
  pre-push    pre-push git hook. Refuses Claude commits that lack a passing proof. No environment
              override; the only skip is the one-shot token from skip-once.
  skip-once "<reason>"  User-only: allow the next refused push once (15 minutes). Logged.
  guard       PreToolUse hook. Denies a Bash command or an edit that would skip or weaken the
              gate: --no-verify, hooksPath, skip-once, writes to the gate, the daemon, the proof
              results or the settings file. Registered in every worktree by link-worktree.
              Commits of phases 1 to 5 (Phase: N trailer) need no proof.
  finalize    Phase-aware finish check: shape of the diff for the phase, then rubocop,
              yarn check and lint, specs (phases 6 and 7) in the checks worktree, the
              proof (phases 6 and 7) and the references. Exit 0 pass, 1 a gate failed,
              2 the diff has the wrong shape.
  setup-checks  Create the checks worktree and give it a slot (own databases, ports).
  link-worktree [path]  Link the local phase skills and agent_task_finalize into a worktree,
              copy CLAUDE.local.md and .claude/settings.local.json when missing, add the
              exclude entries. Nothing of this is committed. post-checkout runs it for
              every new worktree.
  post-checkout  git hook. Runs link-worktree on a new worktree.
  checks      apply | reset [--rebuild-dev-db] | status. Put the current branch state into
              the checks worktree for a live check, or return it to origin/main.
  references  Reference impact of removed or renamed definitions (or --name X).
              Verdict per symbol: Unused | Enumerated | Used.
  pr-section  Markdown block for a PR description, stamped from the findings.
  randomized-suite  Run spec/unit under --order rand:SEED in the gate worktree.
  rspec       Run rspec in the gate worktree under the gate lock (never run it any other way).

The proof: revert the non-spec hunks in an isolated worktree, run the declared
spec example, require it to FAIL. Restore the change, require it to PASS. Then
run the spec file under several random seeds and require it to stay green.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# PROJECT CONFIGURATION. Every constant in this block encodes an ipaas assumption.
# Adapting the gate to another repository means changing this block, the four
# functions named in the comments, and install.sh. Nothing below the next banner
# knows about ipaas.
# =============================================================================
HOME = Path.home()

# The repository under test and the disposable worktree the prover works in.
# Environment variables win over the defaults.
MAIN_REPOSITORY = Path(os.environ.get("IPAAS_GATE_REPOSITORY", HOME / "work/ipaas")).expanduser()
GATE_WORKTREE = Path(os.environ.get("IPAAS_GATE_WORKTREE", HOME / "work/ipaas_worktrees/gate")).expanduser()
# One permanently slotted worktree (setup-worktree: own databases, Redis DBs, ports, Vite) where finalize
# runs rubocop, yarn and specs, and where phases 5 and 7 run the app for the live check. It carries a
# branch state only while a check runs and returns to origin/main afterwards.
CHECKS_WORKTREE = Path(os.environ.get("IPAAS_CHECKS_WORKTREE", HOME / "work/ipaas_worktrees/checks")).expanduser()
WORKTREE_SETUP_SCRIPT = Path(".claude/bin/setup-worktree")
WORKTREE_ENVIRONMENT_FILE = Path(".claude/worktree.env")
MIGRATIONS_DIRECTORY = "platform/db/migrate/"
# Local-only workflow files linked into every worktree, never committed: the phase skills and the
# finalize wrapper live next to this file, the protocol and the hooks file are copied when missing.
LOCAL_SKILLS_DIRECTORY = Path(__file__).resolve().parent / "ipaas-skills"
LOCAL_SKILL_NAMES = ("phase", "phase-1", "phase-2", "phase-3", "phase-4", "phase-5", "phase-6", "phase-7", "phase-comments")
LOCAL_BIN_NAMES = ("agent_task_finalize",)
LOCAL_PROTOCOL_FILE = Path(__file__).resolve().parent / "CLAUDE.local.md"
LOCAL_SETTINGS_FILE = Path(".claude/settings.local.json")
# No trailing slash: the skill entries are symlinks, and git matches a symlink as a file.
LOCAL_EXCLUDE_ENTRIES = ("**/.claude/skills/phase", "**/.claude/skills/phase-[1-7]", "**/.claude/skills/phase-comments",
                         "**/.claude/bin/agent_task_finalize",
                         "/CLAUDE.local.md", "**/.claude/proof/")

# Sub-projects that own an RSpec suite. A declared spec path starts with one of these.
RUBY_PROJECTS = ("platform", "connector", "connector-sdk")

# Shared services the specs touch. Each needs a gate-specific instance, or a proof
# corrupts the developer's own runs. install.sh creates them; gate_test_environment()
# refuses to run unless the gate worktree's env file names exactly these.
GATE_DATABASE = "ipaas_gate_test"
GATE_QUEUE_DATABASE = "ipaas_queue_gate_test"
GATE_REDIS_CONTAINER = "ipaas-gate-redis"
GATE_REDIS_URL = "redis://127.0.0.1:26380/1"
GATE_GIT_CONTAINER = "ipaas-gate-git"
GATE_GIT_PORT = "23232"  # soft-serve keeps state in SQLite, which refuses concurrent writers
GATE_GIT_NAMESPACE = "worktree_gate/%{prefix}/%{solution_repo_name}"  # same shape setup-worktree writes

# Where the test environment lives, which local files are shared into the gate worktree,
# and which environment variables prover_environment() overrides (see that function).
TEST_ENVIRONMENT_FILE = Path("platform/.env.test.local")
SHARED_ENVIRONMENT_FILE_NAMES = (".env.local", ".env")
SHARED_LINK_TARGETS = ("platform/node_modules", "git-server/ssh-user")

# Per-reset generation and per-run database preparation live in
# generate_frontend_routes() and prepare_pristine_databases().

# Spec layout and what counts as code that owes a proof.
SYSTEM_SPEC_MARKER = "/spec/system/"
CODE_EXTENSIONS = {".rb", ".rake", ".erb", ".ru", ".ts", ".tsx", ".js", ".jsx"}
REFERENCE_GLOBS = ("*.rb", "*.rake", "*.erb", "*.ru", "*.ts", "*.tsx", "*.js", "*.jsx", "*.yml", "*.yaml")
REFERENCE_EXCLUDES = ("!.claude/**", "!**/node_modules/**", "!**/coverage/**", "!**/log/**", "!**/tmp/**", "!**/db/schema.rb", "!**/vendor/**")
SCAN_EXCLUDES = ("!**/spec/**", "!**/node_modules/**", "!**/.claude/**", "!**/tmp/**", "!**/vendor/**")
NOISE_PATH_MARKERS = ("/locales/", "/db/schema.rb", "/db/migrate/")

# Phased development. The phase skill writes the current phase into .claude/proof/phase;
# prepare-commit-msg stamps it as a trailer, pre-push exempts the structural phases from the
# proof, and finalize checks that the diff has the shape the phase allows.
PHASE_FILE_NAME = "phase"
PHASE_TRAILER = "Phase"
PHASES = range(1, 8)
PROOF_EXEMPT_PHASES = {1, 2, 3, 4, 5}
CONNECTOR_CORE = ("connector/lib/ipaas/connector/", "connector/lib/ipaas/job/", "connector/lib/ipaas/test_case/")
PLATFORM_LOGIC = ("platform/app/models/", "platform/app/controllers/", "platform/app/services/", "platform/app/jobs/",
                  "platform/app/presenters/")
FRONTEND_LOGIC = ("platform/app/javascript/components/", "platform/app/javascript/hooks/", "platform/app/javascript/common/",
                  "platform/app/javascript/pages/", "platform/app/javascript/layouts/")
PHASE_ALLOWED_PATHS = {
    2: CONNECTOR_CORE + ("connector-sdk/lib/ipaas/", "platform/app/models/", "platform/app/javascript/types/"),
    3: CONNECTOR_CORE + PLATFORM_LOGIC + FRONTEND_LOGIC + ("connector-sdk/lib/ipaas/connector/", "platform/app/helpers/", "platform/lib/"),
    4: CONNECTOR_CORE + PLATFORM_LOGIC + FRONTEND_LOGIC + ("connector/lib/ipaas/encryption/",),
}
TODO_MARKER = "TODO(phase-4"
TODO_REASON_MAX_LINES = 3
RUBY_STUB_BODY = "raise NotImplementedError"
TYPESCRIPT_STUB_BODY = 'throw new Error("not implemented")'
JAVASCRIPT_PREFIX = "platform/app/javascript/"
RUBY_SUFFIXES = (".rb", ".rake", ".ru")
JAVASCRIPT_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")
RUBY_CONTRACT_STARTERS = ("def ", "end", "class ", "module ", "private", "protected", "public", "attr_reader", "attr_writer",
                          "attr_accessor", "include ", "extend ", "prepend ", "require", "alias", "delegate ", "class << self",
                          "module_function", "private_class_method", "private_constant", "frozen_string_literal")
TYPESCRIPT_DECLARATION_STARTERS = ("export", "import", "const ", "let ", "var ", "type ", "interface ", "enum ", "class ",
                                   "function", "async function", "abstract ", "readonly ", "private ", "public ", "protected ",
                                   "static ", "declare ", "default ", "get ", "set ")
TYPESCRIPT_STATEMENT = re.compile(r"^(return\b|if\s*\(|for\s*\(|while\s*\(|switch\s*\(|try\b|await\b|yield\b|console\.|[\w.$\[\]]+\s*=[^=>]|[\w.$]+\(.*\);?$)")
FINALIZE_TIMEOUT_SECONDS = 1800

# Which branch the reference gate diffs against, which commits are Claude's, and the
# date before which Claude commits are exempt from the proof requirement.
UPSTREAM_BRANCH = "origin/main"
# Case-insensitive on purpose: git's own convention is `Co-authored-by:`, the commit skill writes
# `Co-Authored-By:`, and a case-sensitive pattern let a Claude commit past the gate unseen.
CLAUDE_TRAILER_PATTERN = re.compile(r"^Co-authored-by:\s*Claude", re.MULTILINE | re.IGNORECASE)
GATE_EPOCH = "2026-09-06T00:00:00+00:00"

# Toolchain locations and budgets.
RBENV_SHIMS = HOME / ".rbenv/shims"
HOMEBREW_BIN = Path("/opt/homebrew/bin")
RSPEC_TIMEOUT_SECONDS = 900
FULL_SUITE_TIMEOUT_SECONDS = 3600  # spec/unit takes 17 to 28 minutes in the gate
FLAKE_SEEDS = (11, 22, 33)
RANDOMIZED_SUITE_SEED = 11

# =============================================================================
# GATE INTERNALS. Project-independent.
# =============================================================================
GATE_LOCK_FILE = GATE_WORKTREE.parent / ".gate.lock"
CHECKS_LOCK_FILE = CHECKS_WORKTREE.parent / ".checks.lock"
CHECKS_IN_USE_FILE_NAME = "in_use.json"
CHECKS_LOCK_TIMEOUT_SECONDS = 1800

PROOF_DIRECTORY = Path(".claude/proof")
DECLARATION_FILE_NAME = "declaration.json"
RUNS_DIRECTORY_NAME = "runs"
SURFACED_MARKER_NAME = ".surfaced"
PATCH_FILE_NAME = "patch.diff"
META_FILE_NAME = "meta.json"
FINDINGS_FILE_NAME = "findings.json"
LOG_FILE_NAME = "prover.log"
REFERENCES_DIRECTORY_NAME = "references"

UNPROVABLE_RULES = Path(__file__).resolve().parent / "unprovable.yml"
UNPROVABLE_FIXTURE = Path(__file__).resolve().parent / "unprovable_fixture.rb"
UNPROVABLE_FIXTURE_EXPECTED_MATCHES = 9
DEFINITION_KINDS = {"class", "module", "method", "singletonMethod", "constant", "accessor", "alias",
                    "function", "interface", "type", "enum", "variable"}
UNSCOPED_ONLY_KINDS = {"variable", "constant"}
MAX_REMOVED_NAMES = 40
MAX_HITS_PER_NAME = 80

VERDICT_UNUSED = "Unused"
VERDICT_ENUMERATED = "Enumerated"
VERDICT_USED = "Used"
REFERENCES_VERDICT_TRAILER = "References-Verdict"
UNPROVABLE_TRAILER = "Unprovable-References"
PROOF_ID_TRAILER = "Proof-Id"
PROOF_STATUS_TRAILER = "Proof-Status"
PROOF_FRESH_TRAILER = "Proof-Fresh"
# The only way past a refused push: a one-shot token the user writes with `gate.py skip-once "<reason>"`
# from their own shell (`!` prefix in the prompt, or a terminal). Claude's Bash tool is denied that command.
# No environment variable is honoured: anything a process can set, the model can set.
SKIP_TOKEN_FILE = GATE_WORKTREE.parent / ".gate-skip-once"
SKIP_TOKEN_MAX_AGE_SECONDS = 15 * 60
SKIPS_LOG = HOME / ".local/state/gate/skips.log"

# The fence around the gate, applied to Claude's tools in every ipaas worktree by link_worktree():
# permission deny rules for what the rule syntax can express, and the guard hook for the rest.
PHASED_DIRECTORY = Path(__file__).resolve().parent.parent / "phased"
GUARD_WRITE_TOKENS = (">", "sed -i", "tee ", "rm ", "mv ", "cp ", "chmod ", "truncate", "python3 -", "cat <<", "ln -", "install ")
GUARD_FORBIDDEN_ANYWHERE = ("--no-verify", "hooksPath", ".git/hooks", "skip-once", ".gate-skip-once", "GATE_SKIP", "send-pack",
                            "GIT_DIR=", "phased pause", "phased resume", "settings.local.json", "launchctl")
GUARD_WRITE_PROTECTED = (".claude/proof/runs", ".claude/proof/references", "personal/scripts/gate", "personal/scripts/phased",
                         ".local/state/gate", ".local/state/phased")
GUARD_ALLOWED_PREFIXES = ("python3 ~/personal/scripts/gate/gate.py ", f"python3 {Path(__file__).resolve()} ",
                          ".claude/bin/agent_task_finalize", "phased handoff", "phased status", "phased logs", "phased adopt")
GUARD_ALLOWED_GATE_SUBCOMMANDS = ("references", "checks", "finalize", "pr-section", "link-worktree", "setup-checks", "rspec",
                                  "randomized-suite", "surface", "stop", "launch")
GUARD_HOOK_MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit"
ZERO_SHA = "0" * 40

STATUS_PENDING = "pending"
STATUS_PASS = "pass"
STATUS_VACUOUS = "vacuous"
STATUS_FAILS_WITH_CHANGE = "fails_with_change"
STATUS_AMBIGUOUS = "ambiguous_declaration"
STATUS_FLAKY = "flaky"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_DEFERRED = "deferred_system_tier"
STATUS_ERROR = "error"
STATUS_SEVERITY = (
    STATUS_PASS,
    STATUS_NOT_APPLICABLE,
    STATUS_DEFERRED,
    STATUS_PENDING,
    STATUS_FLAKY,
    STATUS_AMBIGUOUS,
    STATUS_FAILS_WITH_CHANGE,
    STATUS_VACUOUS,
    STATUS_ERROR,
)


# ---------------------------------------------------------------- helpers

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(command, cwd=None, allow_exit_codes=(0,), env=None, timeout=None, input_text=None):
    completed = subprocess.run(
        command, cwd=cwd, env=env, input=input_text, timeout=timeout,
        capture_output=True, text=True, stdin=None if input_text is not None else subprocess.DEVNULL,
    )
    if completed.returncode not in allow_exit_codes:
        raise RuntimeError(
            f"{' '.join(str(part) for part in command)} exited {completed.returncode}\n"
            f"{completed.stderr.strip()}"
        )
    return completed


def git(root, *arguments, allow_exit_codes=(0,), input_text=None):
    return run(["git", "-C", str(root), *arguments], allow_exit_codes=allow_exit_codes, input_text=input_text)


def repository_root(start):
    completed = run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], allow_exit_codes=(0, 128))
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip())


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def proof_directory(root):
    return Path(root) / PROOF_DIRECTORY


def runs_directory(root):
    return proof_directory(root) / RUNS_DIRECTORY_NAME


def sorted_runs(root):
    directory = runs_directory(root)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_dir())


def latest_run(root):
    runs = sorted_runs(root)
    return runs[-1] if runs else None


def worst_status(statuses):
    ranked = [status for status in STATUS_SEVERITY if status in statuses]
    return ranked[-1] if ranked else STATUS_ERROR


def is_example_path(path):
    """The example files themselves. Spec infrastructure (spec_helper, support, factories) is
    reverted with the code in the red run, because a hook or helper is often the change under proof."""
    name = Path(path).name
    return name.endswith("_spec.rb") or ".test." in name or "/__tests__/" in f"/{path}"


def is_spec_path(path):
    return bool(re.search(r"(^|/)spec/", path)) or is_example_path(path)


def is_code_path(path):
    if path.startswith(".claude/") or is_spec_path(path):
        return False
    if any(marker in f"/{path}" for marker in NOISE_PATH_MARKERS):
        return False
    return Path(path).suffix in CODE_EXTENSIONS


def is_system_spec(path):
    return SYSTEM_SPEC_MARKER in f"/{path}"


def project_of(path):
    first_segment = path.split("/", 1)[0]
    return first_segment if first_segment in RUBY_PROJECTS else None


def notify(title, message):
    notifier = shutil.which("terminal-notifier")
    if notifier:
        subprocess.run([notifier, "-title", title, "-message", message], capture_output=True)


# ------------------------------------------------------------- the patch

def untracked_paths(root):
    completed = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return [path for path in completed.stdout.split("\0") if path and not path.startswith(str(PROOF_DIRECTORY))]


def snapshot_patch(root, base=None):
    """The change under proof: everything between the merge-base with the upstream branch and the working
    tree, committed work included. Measuring only the uncommitted diff made the same change look empty once
    it was committed, which reported a passing proof as stale and a fresh one as vacuous."""
    reference = base or diff_base(root)
    tracked = git(root, "diff", reference, "--binary", "--no-color", "--no-ext-diff",
                  "--", ".", f":(exclude){PROOF_DIRECTORY}").stdout
    pieces = [tracked]
    for path in untracked_paths(root):
        piece = git(root, "diff", "--no-index", "--binary", "--no-color", "--",
                    "/dev/null", path, allow_exit_codes=(0, 1)).stdout
        pieces.append(piece)
    return "".join(pieces)


def split_patch(patch):
    blocks = re.split(r"(?m)^(?=diff --git )", patch)
    spec_blocks, code_blocks, paths = [], [], []
    for block in blocks:
        if not block.strip():
            continue
        header = re.match(r"diff --git a/(.+?) b/(.+)\n", block)
        path = header.group(2) if header else ""
        paths.append(path)
        (spec_blocks if is_example_path(path) else code_blocks).append(block)
    return "".join(spec_blocks), "".join(code_blocks), paths


def patch_digest(patch):
    return hashlib.sha256(patch.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- launch

def declaration_written(tool_input):
    file_path = tool_input.get("file_path") or tool_input.get("path")
    if not file_path:
        return None
    path = Path(file_path)
    if path.name != DECLARATION_FILE_NAME or path.parent.name != "proof" or path.parent.parent.name != ".claude":
        return None
    return path


def launch():
    payload = read_json_stdin()
    tool_input = payload.get("tool_input") or {}
    declaration_path = declaration_written(tool_input)
    if declaration_path is None:
        context = impact_context(payload.get("tool_name", ""), tool_input)
        if context:
            emit_context(context)
        return
    root = repository_root(declaration_path.parent)
    if root is None:
        return
    declaration = read_json(declaration_path)
    if not declaration or not declaration.get("proofs"):
        emit_context(f"gate: {declaration_path} has no \"proofs\" list; nothing launched.")
        return

    base_sha = diff_base(root)
    patch = snapshot_patch(root, base_sha)
    spec_patch, code_patch, paths = split_patch(patch)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{patch_digest(patch)}"
    run_directory = runs_directory(root) / run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    (run_directory / PATCH_FILE_NAME).write_text(patch)
    write_json(run_directory / DECLARATION_FILE_NAME, declaration)
    write_json(run_directory / META_FILE_NAME, {
        "id": run_id, "source_root": str(root), "base_sha": base_sha,
        "patch_sha": patch_digest(patch), "paths": paths,
        "has_code_hunks": bool(code_patch.strip()), "created_at": now_iso(),
    })
    write_json(run_directory / FINDINGS_FILE_NAME, {"id": run_id, "status": STATUS_PENDING, "started_at": now_iso()})

    log_handle = open(run_directory / LOG_FILE_NAME, "ab")
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "prove", str(run_directory)],
        stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=True, cwd=str(root),
    )
    emit_context(
        f"gate: proof {run_id} launched for {len(declaration['proofs'])} declared example(s). "
        f"Read {run_directory.relative_to(root)}/{FINDINGS_FILE_NAME} before claiming done; "
        f"a unit-tier proof finishes in about 30 seconds."
    )


def read_json_stdin():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def emit_context(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))




# ---------------------------------------------------------------- impact
# Runs on every Edit and Write. When an edit removes or renames a definition, Claude
# sees every remaining reference and the dynamic-dispatch count right away, instead of
# discovering them at Stop or, worse, never.

IMPACT_MAX_NAMES = 5
IMPACT_MAX_HITS = 6
IMPACT_CATEGORY_ORDER = ("code", "string_literal", "symbol", "spec")  # production call sites first


def impact_context(tool_name, tool_input):
    file_path = tool_input.get("file_path")
    if not file_path or tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    path = Path(file_path)
    root = repository_root(path.parent)
    if root is None or not (root / "platform").is_dir():
        return None
    relative = str(path.relative_to(root)) if path.is_relative_to(root) else None
    if relative is None or not is_code_path(relative):
        return None
    removed = removed_by_edit(tool_name, tool_input, root, relative, path)
    if not removed:
        return None
    return impact_report(root, relative, removed)


def removed_by_edit(tool_name, tool_input, root, relative, path):
    suffix = path.suffix
    if tool_name == "Write":
        before = base_definitions(root, "HEAD", relative)
        after = definitions_in(path, relative) if path.exists() else []
        return definition_names(before) - definition_names(after)
    edits = tool_input.get("edits") or [tool_input]
    removed = set()
    for edit in edits:
        old_text, new_text = edit.get("old_string") or "", edit.get("new_string") or ""
        removed |= definition_names(snippet_definitions(old_text, suffix, relative)) - definition_names(snippet_definitions(new_text, suffix, relative))
    return removed


def definition_names(definitions):
    return {each["name"] for each in definitions}


def snippet_definitions(text, suffix, display_path):
    if not text.strip():
        return []
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as handle:
        handle.write(text)
        temporary = handle.name
    try:
        return definitions_in(temporary, display_path)
    finally:
        os.unlink(temporary)


def impact_report(root, relative, removed):
    names = sorted(removed)[:IMPACT_MAX_NAMES]
    project = project_of(relative)
    lines = [f"gate impact: this edit removed or renamed {len(removed)} definition(s) in {relative}."]
    for name in names:
        hits = search_references(root, name)
        remaining = sorted((hit for hit in hits if hit["category"] not in ("comment", "definition")),
                           key=lambda hit: IMPACT_CATEGORY_ORDER.index(hit["category"]) if hit["category"] in IMPACT_CATEGORY_ORDER else len(IMPACT_CATEGORY_ORDER))
        files = len({hit["path"] for hit in remaining})
        lines.append(f"  {name}: {len(remaining)} remaining reference(s) in {files} file(s)")
        for hit in remaining[:IMPACT_MAX_HITS]:
            lines.append(f"    {hit['path'].lstrip('./')}:{hit['line']} [{hit['category']}] {hit['text'][:80]}")
        if len(remaining) > IMPACT_MAX_HITS:
            lines.append(f"    ... {len(remaining) - IMPACT_MAX_HITS} more; run gate.py references --name {name}")
    if len(removed) > IMPACT_MAX_NAMES:
        lines.append(f"  ... {len(removed) - IMPACT_MAX_NAMES} more removed definitions")
    try:
        sites = dynamic_dispatch_sites(root, [project] if project else list(RUBY_PROJECTS))
        if sites:
            lines.append(f"  completeness cannot be proven: {len(sites)} dynamic dispatch site(s) in {project or 'the repo'}")
    except Exception as error:  # the impact report must never hide a broken detector
        lines.append(f"  dynamic dispatch scan failed: {error}")
    lines.append("  every one of these is part of this change. For exact call sites use serena find_referencing_symbols; "
                 "for the full textual list, mocks and strings included, run gate.py references --name <symbol>.")
    return "\n".join(lines)


# ----------------------------------------------------------------- prove

def gate_test_environment():
    """Read the gate worktree's generated .env.test.local and refuse to proceed
    unless it names the gate databases. This is the guard that keeps a proof
    away from ipaas_test and ipaas_queue_test."""
    path = GATE_WORKTREE / TEST_ENVIRONMENT_FILE
    if not path.is_file():
        raise RuntimeError(f"{path} is missing; run `gate.py setup`")
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    expected = {"DB_WRITER_DATABASE": GATE_DATABASE, "DB_READER_DATABASE": GATE_DATABASE, "DB_QUEUE_DATABASE": GATE_QUEUE_DATABASE}
    for key, wanted in expected.items():
        if values.get(key) != wanted:
            raise RuntimeError(f"{path} has {key}={values.get(key)!r}, expected {wanted!r}; refusing to run against a shared database")
    if values.get("REDIS_URL") != GATE_REDIS_URL:
        raise RuntimeError(f"{path} has REDIS_URL={values.get('REDIS_URL')!r}, expected {GATE_REDIS_URL!r}; refusing to share a Redis database")
    return values


def prover_environment():
    gate_values = gate_test_environment()
    ensure_gate_git()
    environment = dict(os.environ)
    environment["PATH"] = f"{RBENV_SHIMS}:{HOMEBREW_BIN}:{environment.get('PATH', '')}"
    environment["RAILS_ENV"] = "test"
    for key in ("DB_WRITER_DATABASE", "DB_READER_DATABASE", "DB_QUEUE_DATABASE", "REDIS_URL"):
        environment[key] = gate_values[key]
    environment["IPAAS_GIT_SERVER_REPO_NAME"] = GATE_GIT_NAMESPACE
    environment["IPAAS_GIT_SERVER_PORT"] = GATE_GIT_PORT
    environment.pop("RUBYOPT", None)
    environment.pop("BUNDLE_GEMFILE", None)
    return environment


def ensure_environment_links():
    for project in RUBY_PROJECTS:
        for file_name in SHARED_ENVIRONMENT_FILE_NAMES:
            source_path = MAIN_REPOSITORY / project / file_name
            target = GATE_WORKTREE / project / file_name
            if source_path.exists() and not target.exists():
                target.symlink_to(source_path)


def reset_gate_worktree(base_sha):
    git(GATE_WORKTREE, "checkout", "--detach", "--force", base_sha)
    git(GATE_WORKTREE, "reset", "--hard", "--quiet")
    git(GATE_WORKTREE, "clean", "-fd", "--quiet")
    ensure_environment_links()
    generate_frontend_routes()


def generate_frontend_routes():
    """Request specs render Inertia pages through Vite, and the Vite build imports the
    generated route helpers (platform/AGENTS.md item 8). They are not checked in, so
    every reset regenerates them; Vite autoBuild then rebuilds on the first render."""
    completed = subprocess.run(["bundle", "exec", "rake", "js:routes:typescript"], cwd=str(GATE_WORKTREE / "platform"),
                               env=prover_environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=RSPEC_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise RuntimeError("js:routes:typescript failed: " + (completed.stderr or completed.stdout)[-500:])


def apply_patch(patch, label, log):
    if not patch.strip():
        log(f"apply {label}: nothing to apply")
        return
    git(GATE_WORKTREE, "apply", "--whitespace=nowarn", "-", input_text=patch)
    log(f"apply {label}: ok")


def rspec(project, spec_file, example, order, json_path, log):
    command = ["bundle", "exec", "rspec", spec_file, "--format", "json", "--out", str(json_path), "--order", order]
    if example:
        command += ["-e", example]
    log("$ " + " ".join(command))
    completed = subprocess.run(
        command, cwd=str(GATE_WORKTREE / project), env=prover_environment(),
        capture_output=True, text=True, timeout=RSPEC_TIMEOUT_SECONDS,
    )
    tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-8:])
    log(f"exit {completed.returncode}\n{tail}")
    report = read_json(json_path, default={}) or {}
    summary = report.get("summary", {})
    return {
        "exit_code": completed.returncode,
        "example_count": summary.get("example_count", 0),
        "failure_count": summary.get("failure_count", 0),
        "errors_outside_examples": summary.get("errors_outside_of_examples_count", 0),
        "failed_examples": [
            {"description": each.get("full_description"), "message": (each.get("exception") or {}).get("message", "")[:400]}
            for each in report.get("examples", []) if each.get("status") == "failed"
        ],
    }


def is_red(result):
    return result["failure_count"] >= 1 or result["errors_outside_examples"] >= 1


def is_green(result):
    return result["exit_code"] == 0 and result["failure_count"] == 0 and result["errors_outside_examples"] == 0


def prove_one(proof, spec_patch, code_patch, base_sha, run_directory, index, log):
    spec_file = proof["spec_file"]
    example = proof.get("example", "")
    project = project_of(spec_file)
    record = {"spec_file": spec_file, "example": example, "project": project}
    if project is None:
        return {**record, "status": STATUS_ERROR, "reason": f"spec_file must start with one of {RUBY_PROJECTS}"}
    if is_system_spec(spec_file):
        return {**record, "status": STATUS_DEFERRED, "reason": "system specs run in the CI tier in this phase"}
    if not code_patch.strip():
        return {**record, "status": STATUS_NOT_APPLICABLE, "reason": "the diff has no non-spec hunks to revert"}

    relative_spec = spec_file.split("/", 1)[1]
    json_directory = run_directory / f"rspec-{index}"
    json_directory.mkdir(exist_ok=True)

    log(f"--- proof {index}: {spec_file} -e {example!r}")
    reset_gate_worktree(base_sha)
    apply_patch(spec_patch, "spec hunks only", log)
    red = rspec(project, relative_spec, example, "defined", json_directory / "red.json", log)
    record["red"] = red
    if red["example_count"] != 1 and red["errors_outside_examples"] == 0:
        return {**record, "status": STATUS_AMBIGUOUS,
                "reason": f"-e matched {red['example_count']} examples; the declaration must select exactly one"}
    if not is_red(red):
        return {**record, "status": STATUS_VACUOUS,
                "reason": "the example passes with the change reverted, so it does not prove the change"}

    apply_patch(code_patch, "code hunks", log)
    green = rspec(project, relative_spec, example, "defined", json_directory / "green.json", log)
    record["green"] = green
    if green["example_count"] != 1:
        return {**record, "status": STATUS_AMBIGUOUS, "reason": f"-e matched {green['example_count']} examples with the change applied"}
    if not is_green(green):
        return {**record, "status": STATUS_FAILS_WITH_CHANGE, "reason": "the example fails with the change applied"}

    flake = []
    for seed in FLAKE_SEEDS:
        result = rspec(project, relative_spec, "", f"rand:{seed}", json_directory / f"flake-{seed}.json", log)
        flake.append({"seed": seed, "green": is_green(result), "failure_count": result["failure_count"],
                      "failed_examples": result["failed_examples"]})
    record["flake"] = flake
    if not all(each["green"] for each in flake):
        return {**record, "status": STATUS_FLAKY, "reason": "the spec file is not green under every seed"}
    return {**record, "status": STATUS_PASS}


def prove(run_directory):
    run_directory = Path(run_directory)
    log_path = run_directory / LOG_FILE_NAME

    def log(text):
        with open(log_path, "a") as handle:
            handle.write(f"[{now_iso()}] {text}\n")

    meta = read_json(run_directory / META_FILE_NAME) or {}
    declaration = read_json(run_directory / DECLARATION_FILE_NAME) or {}
    patch = (run_directory / PATCH_FILE_NAME).read_text()
    spec_patch, code_patch, _ = split_patch(patch)
    findings = {"id": meta.get("id"), "status": STATUS_PENDING, "base_sha": meta.get("base_sha"),
                "patch_sha": meta.get("patch_sha"), "started_at": now_iso(), "proofs": []}
    write_json(run_directory / FINDINGS_FILE_NAME, findings)

    GATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_LOCK_FILE, "w") as lock_handle:
        log("waiting for the gate worktree lock")
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        log("lock acquired")
        try:
            for index, proof in enumerate(declaration.get("proofs", []), start=1):
                findings["proofs"].append(prove_one(proof, spec_patch, code_patch, meta["base_sha"], run_directory, index, log))
            findings["status"] = worst_status([each["status"] for each in findings["proofs"]])
        except Exception as error:  # the findings file must always reach a final state
            log(f"prover error: {error!r}")
            findings["status"] = STATUS_ERROR
            findings["reason"] = str(error)[:1000]
        finally:
            findings["finished_at"] = now_iso()
            write_json(run_directory / FINDINGS_FILE_NAME, findings)
            log(f"final status: {findings['status']}")
    notify("gate proof", f"{findings['id']}: {findings['status']}")


# ------------------------------------------------------------------ stop

def stop():
    payload = read_json_stdin()
    if payload.get("stop_hook_active"):
        return
    root = repository_root(payload.get("cwd") or os.getcwd())
    if root is None or not (root / "platform").is_dir():
        return
    message = stop_message(root)
    if message:
        print(json.dumps({"systemMessage": f"gate: {message}"}))


def stop_message(root):
    messages = []
    base = diff_base(root)
    code_paths = code_files_owed_proof(root, base)
    declaration = read_json(proof_directory(root) / DECLARATION_FILE_NAME)
    run_directory = latest_run(root)
    phase = phase_number(root)
    if phase in PROOF_EXEMPT_PHASES and code_paths and not declaration:
        messages.append(f"phase {phase} of 7 is active; its commits need no proof. Run .claude/bin/agent_task_finalize --phase {phase} before handing off.")
    elif code_paths and not declaration:
        messages.append(f"{len(code_paths)} non-spec file(s) changed and no {PROOF_DIRECTORY}/{DECLARATION_FILE_NAME} exists. "
                        "Nothing has proven this change. Declare the spec example that proves it.")
    elif run_directory is not None:
        findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
        meta = read_json(run_directory / META_FILE_NAME) or {}
        status = findings.get("status", STATUS_PENDING)
        if status == STATUS_PENDING:
            messages.append(f"proof {findings.get('id')} is still running. Do not report the change as done.")
        elif code_paths and meta.get("patch_sha") != patch_digest(snapshot_patch(root)):
            messages.append(f"proof {findings.get('id')} finished {status} but the diff changed since it ran. It is stale. Redeclare to re-prove.")
        elif status != STATUS_PASS:
            reasons = "; ".join(f"{each['spec_file']}: {each.get('reason', each['status'])}" for each in findings.get("proofs", []) if each["status"] != STATUS_PASS)
            messages.append(f"proof {findings.get('id')} finished {status}. {reasons or findings.get('reason', '')}")
    if code_paths:
        try:
            report = references_report(root)
            write_references_report(root, report)
            messages.append(summarize_references(report))
        except Exception as error:  # a broken reference check must be visible, never silent
            messages.append(f"reference check failed: {error}")
    return "\n".join(messages) if messages else None


# --------------------------------------------------------------- surface

def surface():
    payload = read_json_stdin()
    root = repository_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return
    marker_path = proof_directory(root) / SURFACED_MARKER_NAME
    surfaced = set((read_json(marker_path, default=[]) or []))
    lines = []
    for run_directory in sorted_runs(root):
        findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
        if findings.get("status", STATUS_PENDING) == STATUS_PENDING or run_directory.name in surfaced:
            continue
        surfaced.add(run_directory.name)
        details = "; ".join(f"{each['spec_file']} -> {each['status']}" + (f" ({each['reason']})" if each.get("reason") else "")
                            for each in findings.get("proofs", []))
        lines.append(f"gate: proof {run_directory.name} finished {findings['status']}. {details}")
    if lines:
        write_json(marker_path, sorted(surfaced))
    protocol = phase_protocol_line(root, payload.get("prompt") or payload.get("user_prompt") or "")
    if protocol:
        lines.append(protocol)
    if lines:
        print("\n".join(lines))


def phase_protocol_line(root, prompt):
    """One line per prompt: which phase is active, or that a code task starts at /phase."""
    phase = phase_number(root)
    if phase is not None:
        return (f"gate: phase {phase} of 7 is active on this branch. Stay inside /phase-{phase}: its allowed locations and"
                f" done criteria bind every edit. End with .claude/bin/agent_task_finalize --phase {phase}, then hand off.")
    if not (root / ".claude/skills/phase/SKILL.md").exists() or re.search(r"\bquick\b", prompt, re.IGNORECASE):
        return None
    return "gate: phased development is the default in this repository. A code change starts with /phase (phase 1, discovery) unless the prompt says quick."


# ------------------------------------------------------------ commit-msg

def commit_msg(arguments):
    if len(arguments) >= 2 and arguments[1] in ("merge", "squash"):
        return
    message_path = Path(arguments[0])
    root = repository_root(os.getcwd())
    if root is None:
        return
    trailers = []
    phase = phase_number(root)
    if phase is not None:
        trailers.append((PHASE_TRAILER, str(phase)))
    trailers.extend(proof_trailers(root, message_path))
    if not trailers:
        return
    for trailer, value in trailers:
        git(root, "interpret-trailers", "--in-place", "--if-exists", "replace", "--trailer", f"{trailer}: {value}", str(message_path))


def proof_trailers(root, message_path):
    """Proof and reference trailers for a Claude commit that touches code."""
    if not CLAUDE_TRAILER_PATTERN.search(message_path.read_text()):
        return []
    staged = git(root, "diff", "--cached", "--name-only").stdout.split()
    if not any(is_code_path(path) for path in staged):
        return []
    trailers = []
    run_directory = latest_run(root)
    if run_directory is not None:
        findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
        meta = read_json(run_directory / META_FILE_NAME) or {}
        fresh = "yes" if meta.get("patch_sha") == patch_digest(snapshot_patch(root)) else "no"
        trailers += [(PROOF_ID_TRAILER, run_directory.name), (PROOF_STATUS_TRAILER, findings.get("status", STATUS_PENDING)), (PROOF_FRESH_TRAILER, fresh)]
    report = latest_references_report(root)
    if report and report.get("verdict") != STATUS_NOT_APPLICABLE:
        trailers.append((REFERENCES_VERDICT_TRAILER, report["verdict"]))
        if report.get("dynamic_dispatch_sites"):
            trailers.append((UNPROVABLE_TRAILER, f"{len(report['dynamic_dispatch_sites'])} dynamic dispatch sites in {', '.join(report['projects_scanned'])}"))
    return trailers


# -------------------------------------------------------------- pre-push

def trailer_value(body, trailer):
    match = re.search(rf"^{re.escape(trailer)}:\s*(.+)$", body, re.MULTILINE)
    return match.group(1).strip() if match else None


def pre_push():
    root = repository_root(os.getcwd())
    violations = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, _, remote_sha = parts
        if local_sha == ZERO_SHA:
            continue
        if remote_sha == ZERO_SHA:
            revisions = git(root, "rev-list", local_sha, "--not", "--remotes").stdout.split()
        else:
            revisions = git(root, "rev-list", f"{remote_sha}..{local_sha}").stdout.split()
        for sha in revisions:
            violations.extend(commit_violations(root, sha))
    if not violations:
        return
    print("gate: refusing the push.", file=sys.stderr)
    for violation in violations:
        print(f"  {violation}", file=sys.stderr)
    token = consume_skip_token()
    if token:
        record_skip(token, violations)
        print(f"gate: one-shot skip token used ({token['reason']}); pushing this once.", file=sys.stderr)
        return
    print("gate: only the user can skip, from their own shell: "
          f"`! python3 {Path(__file__).resolve()} skip-once \"<reason>\"` then push again within 15 minutes.", file=sys.stderr)
    sys.exit(1)


def consume_skip_token():
    if not SKIP_TOKEN_FILE.exists():
        return None
    token = read_json(SKIP_TOKEN_FILE) or {}
    SKIP_TOKEN_FILE.unlink(missing_ok=True)
    written = token.get("at", "1970-01-01T00:00:00+00:00")
    if (datetime.now(timezone.utc) - datetime.fromisoformat(written)).total_seconds() > SKIP_TOKEN_MAX_AGE_SECONDS:
        print("gate: the skip token is older than 15 minutes and was discarded.", file=sys.stderr)
        return None
    return token


def record_skip(token, violations):
    SKIPS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SKIPS_LOG, "a") as handle:
        handle.write(json.dumps({"at": now_iso(), "reason": token.get("reason"), "cwd": os.getcwd(), "violations": violations}) + "\n")


def skip_once(arguments):
    """Written by the user, never by the model. One push, within 15 minutes, with a reason on record."""
    reason = " ".join(arguments).strip()
    if not reason:
        print("usage: gate.py skip-once \"<why this push may bypass the gate>\"")
        sys.exit(2)
    write_json(SKIP_TOKEN_FILE, {"reason": reason, "at": now_iso()})
    print(f"gate: the next refused push within 15 minutes goes through once. Reason on record: {reason}")


def commit_violations(root, sha):
    if not is_gated_commit(root, sha):
        return []
    body = git(root, "log", "-1", "--format=%B", sha).stdout
    files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
    if not any(is_code_path(path) for path in files):
        return []
    short = sha[:10]
    if trailer_value(body, REFERENCES_VERDICT_TRAILER) == VERDICT_USED:
        return [f"{short}: references verdict is {VERDICT_USED}; a removed or renamed name is still referenced"]
    phase = trailer_value(body, PHASE_TRAILER)
    if phase and phase.isdigit() and int(phase) in PROOF_EXEMPT_PHASES:
        return []
    proof_id = trailer_value(body, PROOF_ID_TRAILER)
    if not proof_id:
        return [f"{short}: Claude commit touches code and carries no {PROOF_ID_TRAILER} trailer"]
    findings = read_json(runs_directory(root) / proof_id / FINDINGS_FILE_NAME)
    if not findings:
        return [f"{short}: {PROOF_ID_TRAILER} {proof_id} has no findings file under {PROOF_DIRECTORY}"]
    if findings.get("status") != STATUS_PASS:
        return [f"{short}: proof {proof_id} status is {findings.get('status')}, not {STATUS_PASS}"]
    if trailer_value(body, PROOF_FRESH_TRAILER) == "no":
        return [f"{short}: proof {proof_id} ran against a different diff than the one committed"]
    return []


# -------------------------------------------------------------- finalize

def phase_number(root):
    path = proof_directory(root) / PHASE_FILE_NAME
    if not path.exists():
        return None
    value = path.read_text().strip()
    return int(value) if value.isdigit() and int(value) in PHASES else None


def request_number(root):
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    match = re.match(r"(?:requests|problems)/(\d+)", branch)
    return match.group(1) if match else None


def changed_files_all(root, base):
    tracked = git(root, "diff", "--name-only", base).stdout.split()
    return sorted(set(tracked + untracked_paths(root)))


def phase_base(root, base, phase):
    """The commit the current phase builds on: the newest branch commit that does not carry
    `Phase: <phase>`. Shape checks look only at this phase's own changes, earlier phases are approved."""
    for sha in git(root, "rev-list", f"{base}..HEAD", allow_exit_codes=(0, 128)).stdout.split():
        body = git(root, "log", "-1", "--format=%B", sha).stdout
        if trailer_value(body, PHASE_TRAILER) != str(phase):
            return sha
    return base


def diff_lines(root, base, path):
    """Added and removed lines of one file against base, working tree included.
    Both are lists of (line_number, text); removed numbers count on the base side."""
    disk = root / path
    untracked = set(untracked_paths(root))
    if path in untracked:
        return list(enumerate(disk.read_text(errors="replace").splitlines(), 1)), []
    if not disk.exists():
        old = git(root, "show", f"{base}:{path}", allow_exit_codes=(0, 128)).stdout.splitlines()
        return [], list(enumerate(old, 1))
    diff = git(root, "diff", "--unified=0", base, "--", path).stdout
    added, removed = [], []
    new_line = old_line = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            old_line, new_line = int(match.group(1)), int(match.group(2))
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed.append((old_line, line[1:]))
            old_line += 1
    return added, removed


def is_comment_line(path, text):
    stripped = text.strip()
    if not stripped:
        return True
    if path.endswith(RUBY_SUFFIXES):
        return stripped.startswith("#")
    return stripped.startswith(("//", "/*", "*", "*/"))


def code_paths_of(paths):
    return [path for path in paths if is_code_path(path)]


def outside_allowed_paths(phase, paths):
    allowed = PHASE_ALLOWED_PATHS.get(phase, ())
    return [f"{path}: outside the locations phase {phase} may change" for path in code_paths_of(paths) if not path.startswith(allowed)]


def spec_changes(paths):
    return [f"{path}: specs are not written in phases 2 to 4" for path in paths if is_spec_path(path)]


def bracket_balance(text):
    return text.count("(") + text.count("[") + text.count("{") - text.count(")") - text.count("]") - text.count("}")


def ruby_def_name(text):
    match = re.match(r"\s*def\s+(?:self\.)?([\w?!=]+)", text)
    return match.group(1) if match else None


def ruby_contract_violations(path, added, removed):
    """Phase 3 in Ruby: signatures, constants, class-level data and NotImplementedError stubs only."""
    violations = []
    changed_signatures = {ruby_def_name(text) for _, text in removed if ruby_def_name(text)}
    balance = 0
    expect_stub = False
    for number, text in added:
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if balance > 0:
            balance += bracket_balance(stripped)
            continue
        if expect_stub:
            expect_stub = False
            if not stripped.startswith(RUBY_STUB_BODY):
                violations.append(f"{path}:{number} a new method body must be exactly {RUBY_STUB_BODY}")
                continue
        if stripped.startswith(RUBY_STUB_BODY):
            continue
        name = ruby_def_name(stripped)
        is_contract = (stripped.startswith(RUBY_CONTRACT_STARTERS) or re.match(r"[A-Z][A-Z0-9_]*\s*=", stripped)
                       or re.match(r"@@?\w+\s*=", stripped) or re.match(r"self\.\w+\s*=", stripped))
        if not is_contract:
            violations.append(f"{path}:{number} logic added in phase 3: {stripped[:70]}")
            continue
        balance = max(bracket_balance(stripped), 0)
        if name and balance == 0 and name not in changed_signatures and not re.search(r"\bend\b\s*$", stripped):
            expect_stub = True
    return violations


def typescript_contract_violations(path, added):
    violations = []
    for number, text in added:
        stripped = text.strip()
        if not stripped or is_comment_line(path, text) or TYPESCRIPT_STUB_BODY in stripped:
            continue
        if stripped.startswith(TYPESCRIPT_DECLARATION_STARTERS):
            continue
        if TYPESCRIPT_STATEMENT.match(stripped):
            violations.append(f"{path}:{number} logic added in phase 3: {stripped[:70]}")
    return violations


def phase3_violations(root, base, paths):
    violations = []
    for path in code_paths_of(paths):
        added, removed = diff_lines(root, base, path)
        if path.endswith(RUBY_SUFFIXES):
            violations += ruby_contract_violations(path, added, removed)
        elif path.endswith(JAVASCRIPT_SUFFIXES):
            violations += typescript_contract_violations(path, added)
    return violations


def phase4_violations(root, base, paths):
    """Phase 4: comment lines only, every block a TODO marker, no code removed."""
    violations = []
    markers = 0
    for path in code_paths_of(paths):
        added, removed = diff_lines(root, base, path)
        violations += [f"{path}:{number} removed code in phase 4: {text.strip()[:60]}" for number, text in removed if text.strip()]
        block = 0
        for number, text in added:
            if not is_comment_line(path, text):
                violations.append(f"{path}:{number} added a non-comment line in phase 4: {text.strip()[:60]}")
                block = 0
            elif TODO_MARKER in text:
                markers += 1
                block = 1
            elif not text.strip():
                block = 0
            elif block:
                block += 1
                if block == TODO_REASON_MAX_LINES + 1:
                    violations.append(f"{path}:{number} TODO reason longer than {TODO_REASON_MAX_LINES} lines")
    if markers == 0 and code_paths_of(paths):
        violations.append(f"no {TODO_MARKER} ...) marker was added")
    return violations


def phase6_violations(root, base, paths):
    violations = []
    for path in code_paths_of(paths):
        _, removed = diff_lines(root, base, path)
        violations += [f"{path}:{number} removed a TODO marker in phase 6" for number, text in removed if TODO_MARKER in text]
    return violations


def phase7_violations(root, base, paths):
    violations = []
    request = request_number(root)
    marker = f"{TODO_MARKER} #{request}" if request else TODO_MARKER
    hits = run(["rg", "--no-heading", "--line-number", "--fixed-strings", marker, *[f"--glob={glob}" for glob in REFERENCE_GLOBS],
                *[f"--glob={exclude}" for exclude in REFERENCE_EXCLUDES], "--", "."], cwd=root, allow_exit_codes=(0, 1, 2))
    violations += [f"{line.split(':', 1)[0].lstrip('./')}: TODO marker still present" for line in hits.stdout.splitlines()]
    for path in code_paths_of(paths):
        added, _ = diff_lines(root, base, path)
        violations += [f"{path}:{number} stub body still present" for number, text in added if RUBY_STUB_BODY in text or TYPESCRIPT_STUB_BODY in text]
    return violations


def shape_violations(root, base, phase, paths):
    if phase == 2:
        return outside_allowed_paths(2, paths) + spec_changes(paths)
    if phase == 3:
        return outside_allowed_paths(3, paths) + spec_changes(paths) + phase3_violations(root, base, paths)
    if phase == 4:
        return outside_allowed_paths(4, paths) + spec_changes(paths) + phase4_violations(root, base, paths)
    if phase == 6:
        return phase6_violations(root, base, paths)
    if phase == 7:
        return phase7_violations(root, base, paths)
    return []


def toolchain_environment():
    environment = dict(os.environ)
    environment["PATH"] = f"{RBENV_SHIMS}:{HOMEBREW_BIN}:{environment.get('PATH', '')}"
    return environment


def command_check(name, command, cwd, environment=None):
    completed = run(command, cwd=cwd, allow_exit_codes=range(256), env=environment or toolchain_environment(), timeout=FINALIZE_TIMEOUT_SECONDS)
    tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-12:])
    return (name, "ok" if completed.returncode == 0 else "FAIL", f"{' '.join(command)} (exit {completed.returncode})" + (f"\n{tail}" if completed.returncode else ""))


def rubocop_checks(root, paths):
    by_project = {}
    for path in paths:
        if path.endswith((".rb", ".rake")) and (root / path).exists() and project_of(path):
            by_project.setdefault(project_of(path), []).append(path.split("/", 1)[1])
    return [command_check(f"rubocop {project}", ["bundle", "exec", "rubocop", "--force-exclusion", *files], root / project)
            for project, files in sorted(by_project.items())]


def javascript_checks(root, paths):
    if not any(path.startswith(JAVASCRIPT_PREFIX) and path.endswith(JAVASCRIPT_SUFFIXES) for path in paths):
        return []
    platform = root / "platform"
    return [command_check("yarn check", ["yarn", "check"], platform), command_check("yarn lint", ["yarn", "lint"], platform)]


def environment_file_values(path):
    """KEY=value lines of a dotenv or a sourceable `export KEY=value` file."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def specs_for(root, paths):
    per_project = {}
    for path in paths:
        project = project_of(path)
        if not project or not (root / path).exists():
            continue
        if path.endswith("_spec.rb"):
            per_project.setdefault(project, set()).add(path.split("/", 1)[1])
        elif path.endswith(".rb") and not is_spec_path(path):
            for hit in (root / project / "spec").rglob(f"{Path(path).stem}_spec.rb"):
                per_project.setdefault(project, set()).add(str(hit.relative_to(root / project)))
    return {project: sorted(files) for project, files in per_project.items()}


def javascript_tests_for(root, paths):
    tests = set()
    for path in paths:
        if not path.startswith(JAVASCRIPT_PREFIX) or not (root / path).exists():
            continue
        if ".test." in Path(path).name:
            tests.add(path.split("/", 1)[1])
        elif path.endswith(JAVASCRIPT_SUFFIXES):
            stem = Path(path).name.split(".")[0]
            for hit in (root / "platform/app/javascript").rglob(f"{stem}.test.*"):
                tests.add(str(hit.relative_to(root / "platform")))
    return sorted(tests)


def spec_checks(root, paths):
    checks = [command_check(f"rspec {project}", ["bundle", "exec", "rspec", *files], root / project, environment=checks_test_environment())
              for project, files in sorted(specs_for(root, paths).items())]
    tests = javascript_tests_for(root, paths)
    if tests:
        checks.append(command_check("vitest", ["yarn", "vitest", "run", *tests], root / "platform"))
    return checks or [("specs", "skip", "no spec maps to the changed files")]


def proof_check(root, paths):
    if not code_paths_of(paths):
        return ("proof", "skip", "no code changed")
    run_directory = latest_run(root)
    if run_directory is None:
        return ("proof", "FAIL", f"no proof declared; write {PROOF_DIRECTORY}/{DECLARATION_FILE_NAME}")
    findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
    meta = read_json(run_directory / META_FILE_NAME) or {}
    status = findings.get("status", STATUS_PENDING)
    if status != STATUS_PASS:
        return ("proof", "FAIL", f"{run_directory.name} is {status}")
    if meta.get("patch_sha") != patch_digest(snapshot_patch(root)):
        return ("proof", "FAIL", f"{run_directory.name} passed against an older diff; redeclare")
    return ("proof", "ok", run_directory.name)


def references_check(root, paths):
    if not code_paths_of(paths):
        return ("references", "skip", "no code changed")
    report = references_report(root)
    write_references_report(root, report)
    verdict = report["verdict"]
    if verdict == STATUS_NOT_APPLICABLE:
        return ("references", "ok", "no definition removed or renamed")
    return ("references", "FAIL" if verdict == VERDICT_USED else "ok", summarize_references(report, limit=3))


def finalize(arguments):
    root = repository_root(os.getcwd())
    if root is None:
        print("finalize: not inside a git repository")
        sys.exit(2)
    phase = phase_number(root)
    if "--phase" in arguments:
        phase = int(arguments[arguments.index("--phase") + 1])
    if phase not in PHASES:
        print(f"finalize: no phase given and {PROOF_DIRECTORY}/{PHASE_FILE_NAME} is missing; pass --phase N")
        sys.exit(2)
    base = diff_base(root)
    paths = changed_files_all(root, base)
    own_base = phase_base(root, base, phase)
    own_paths = changed_files_all(root, own_base)
    checks = []
    violations = shape_violations(root, own_base, phase, own_paths)
    checks.append(("shape", "FAIL" if violations else "ok",
                   "\n".join(violations) if violations else f"phase {phase} shape holds over {len(own_paths)} file(s) changed since {own_base[:10]}"))
    checks += tool_checks_in_checks_worktree(root, paths, run_specs=phase in (6, 7))
    if phase in (6, 7):
        checks.append(proof_check(root, paths))
    else:
        checks.append(("proof", "skip", f"phase {phase} needs no proof"))
    checks.append(references_check(root, paths))
    print_steps(checks)
    if checks[0][1] == "FAIL":
        sys.exit(2)
    if any(status == "FAIL" for _, status, _ in checks):
        sys.exit(1)


def tool_checks_in_checks_worktree(root, paths, run_specs):
    """rubocop, yarn and specs run against the branch state applied to the checks worktree, which is
    returned to origin/main afterwards whatever happened."""
    if root.resolve() == CHECKS_WORKTREE.resolve():
        return [("checks", "FAIL", "finalize runs from the PR worktree, not from the checks worktree")]
    if not checks_ready():
        return [("checks", "FAIL", f"no checks worktree with a slot at {CHECKS_WORKTREE}; run gate.py setup-checks")]
    checks = []
    with checks_lock():
        try:
            checks += apply_branch_to_checks(root)
            if all(status == "ok" for _, status, _ in checks):
                checks += rubocop_checks(CHECKS_WORKTREE, paths)
                checks += javascript_checks(CHECKS_WORKTREE, paths)
                if run_specs:
                    checks += spec_checks(CHECKS_WORKTREE, paths)
                else:
                    checks.append(("specs", "skip", "this phase does not run specs"))
            else:
                checks.append(("checks", "skip", "the checks worktree could not take the branch state"))
        except RuntimeError as error:
            checks.append(("checks", "FAIL", str(error)))
        finally:
            checks += reset_checks_worktree()
    return checks


# ------------------------------------------------------------------ guard

def guard():
    """PreToolUse hook. Prints a deny decision when a tool call would skip or weaken the gate; silent otherwise.
    Deny rules cover the prefix-shaped commands; this covers substrings and paths anywhere in the command."""
    payload = read_json_stdin()
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    reason = None
    if tool_name == "Bash":
        reason = guard_bash(tool_input.get("command") or "")
    elif tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        reason = guard_path(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": f"gate guard: {reason}"}}))


def guard_bash(command):
    for token in GUARD_FORBIDDEN_ANYWHERE:
        if token in command:
            return f"`{token}` skips or changes the gate. Only the user can do that, from their own shell (`!` prefix)."
    if re.search(r"\bgit\s+commit\b.*\s-n\b", command):
        return "`git commit -n` skips the commit hooks that stamp the proof and phase trailers."
    if re.search(r"\bgit\s+config\b.*\bhooks", command):
        return "changing the hooks configuration disables the gate."
    for segment in command_segments(command):
        reason = guard_segment(segment)
        if reason:
            return reason
    return None


def command_segments(command):
    """Each simple command of a compound line, judged on its own: `cd x && gate.py pr-section` is two segments."""
    return [part.strip() for part in re.split(r"&&|\|\||;|\||\n", command) if part.strip()]


def guard_segment(segment):
    touched = [path for path in GUARD_WRITE_PROTECTED if path in segment]
    if not touched:
        return None
    redirect = re.search(r">>?\s*(\S+)", segment)
    if redirect and any(path in redirect.group(1) for path in GUARD_WRITE_PROTECTED):
        return f"redirecting into `{redirect.group(1)}` would forge or weaken the gate."
    if guard_allowed_invocation(segment):
        return None
    if any(token in segment for token in GUARD_WRITE_TOKENS):
        return f"writing under `{touched[0]}` would forge or weaken the gate. Read it if you need to; never write it."
    return None


def guard_allowed_invocation(segment):
    """`python3 .../gate.py <read-only or check subcommand> ...` and the wrapper and daemon commands the protocol
    asks for, with leading environment assignments ignored."""
    stripped = re.sub(r"^(?:\w+=\S*\s+)+", "", segment.strip())
    for prefix in GUARD_ALLOWED_PREFIXES:
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            if prefix.endswith("gate.py "):
                return rest.split(" ", 1)[0] in GUARD_ALLOWED_GATE_SUBCOMMANDS
            return True
    return False


def guard_path(file_path):
    expanded = str(Path(file_path).expanduser())
    for path in GUARD_WRITE_PROTECTED:
        if path in expanded:
            return f"`{expanded}` is part of the gate; the model never writes it."
    if expanded.endswith("settings.local.json") or expanded.endswith(".gate-skip-once"):
        return f"`{expanded}` holds the fence around the gate; the model never writes it."
    return None


def fence_deny_rules():
    gate = str(Path(__file__).resolve().parent)
    # An absolute path rule is written `//Users/...`: one extra slash in front of the absolute path.
    protected = [f"/{gate}/**", f"/{PHASED_DIRECTORY}/**", "**/.claude/proof/runs/**", "**/.claude/proof/references/**",
                 "**/.claude/settings.local.json", f"/{SKIP_TOKEN_FILE}", f"/{SKIPS_LOG.parent}/**"]
    rules = ["Bash(git push --no-verify*)", "Bash(git push * --no-verify*)", "Bash(git commit --no-verify*)", "Bash(git commit -n*)",
             "Bash(git -c core.hooksPath*)", "Bash(git config * core.hooksPath*)", "Bash(* skip-once*)", "Bash(phased pause*)",
             "Bash(phased resume*)", "Bash(launchctl *)"]
    # Only `Edit(path)` rules take part in file permission checks, and an Edit rule covers every
    # file-editing tool. `Write(path)` and `MultiEdit(path)` rules are ignored and warned about at startup.
    rules += [f"Edit({pattern})" for pattern in protected]
    return rules


def stale_fence_rules(deny):
    """Rules earlier versions wrote that Claude Code rejects or ignores: the triple-slash absolute form,
    and the Write and MultiEdit path rules."""
    return [rule for rule in deny
            if "(///" in rule or rule.startswith(("Write(/", "Write(**", "Write(~", "MultiEdit("))]


def ensure_fence(root):
    """Deny rules and the guard hook in the worktree's .claude/settings.local.json, merged, idempotent."""
    settings_path = root / LOCAL_SETTINGS_FILE
    settings = read_json(settings_path, default={}) or {}
    permissions = settings.setdefault("permissions", {})
    deny = permissions.setdefault("deny", [])
    stale = stale_fence_rules(deny)
    for rule in stale:
        deny.remove(rule)
    added = [rule for rule in fence_deny_rules() if rule not in deny]
    deny.extend(added)
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    command = f"python3 {Path(__file__).resolve()} guard"
    present = any(hook.get("command") == command for entry in pre for hook in entry.get("hooks", []))
    if not present:
        pre.append({"matcher": GUARD_HOOK_MATCHER, "hooks": [{"type": "command", "command": command}]})
    if added or stale or not present:
        write_json(settings_path, settings)
        return [f"fenced {settings_path}: {len(added)} deny rule(s) added, {len(stale)} stale removed" + ("" if present else ", guard hook registered")]
    return []


# ------------------------------------------------------- local worktree links

def link_worktree(root, quiet=False):
    """Make one worktree usable for phased development without committing anything: symlinks to the
    local skills and the finalize wrapper, copies of the protocol and the hooks file when missing, and
    exclude entries so git never lists any of it."""
    actions = []
    skills = root / ".claude/skills"
    skills.mkdir(parents=True, exist_ok=True)
    for name in LOCAL_SKILL_NAMES:
        actions += ensure_symlink(skills / name, LOCAL_SKILLS_DIRECTORY / name)
    binaries = root / ".claude/bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for name in LOCAL_BIN_NAMES:
        actions += ensure_symlink(binaries / name, LOCAL_SKILLS_DIRECTORY / name)
    actions += ensure_copy(root / LOCAL_PROTOCOL_FILE.name, LOCAL_PROTOCOL_FILE)
    actions += ensure_copy(root / LOCAL_SETTINGS_FILE, MAIN_REPOSITORY / LOCAL_SETTINGS_FILE)
    actions += ensure_fence(root)
    actions += ensure_exclude_entries(root)
    if not quiet:
        print("\n".join(actions) if actions else f"{root}: already linked")
    return actions


def ensure_symlink(link, target):
    if link.is_symlink() and link.resolve() == target.resolve():
        return []
    if link.exists() and not link.is_symlink():
        return [f"kept {link}: a real file or directory is there, not replaced"]
    if link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return [f"linked {link} -> {target}"]


def ensure_copy(destination, source):
    if destination.exists() or destination.is_symlink() or not source.exists() or source.resolve() == destination.resolve():
        return []
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return [f"copied {source.name} -> {destination}"]


def ensure_exclude_entries(root):
    common = Path(git(root, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = (root / common).resolve()
    exclude = common / "info/exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    present = exclude.read_text().splitlines() if exclude.exists() else []
    missing = [entry for entry in LOCAL_EXCLUDE_ENTRIES if entry not in present]
    if not missing:
        return []
    with open(exclude, "a") as handle:
        handle.write("".join(f"{entry}\n" for entry in missing))
    return [f"excluded in {exclude}: {', '.join(missing)}"]


def link_worktree_command(arguments):
    root = repository_root(Path(arguments[0]).expanduser() if arguments else Path(os.getcwd()))
    if root is None:
        print("link-worktree: not inside a git repository")
        sys.exit(2)
    if root.resolve() in (GATE_WORKTREE.resolve(), CHECKS_WORKTREE.resolve()):
        print(f"{root}: gate or checks worktree, no Claude session runs here; skipped")
        return
    link_worktree(root)


def post_checkout(arguments):
    """git post-checkout: <previous head> <new head> <flag>. flag 1 is a branch checkout, which is also what
    `git worktree add` performs in the new worktree. The gate and checks worktrees run no Claude session."""
    if len(arguments) < 3 or arguments[2] != "1":
        return
    root = repository_root(Path(os.getcwd()))
    if root is None or root.resolve() in (GATE_WORKTREE.resolve(), CHECKS_WORKTREE.resolve()):
        return
    actions = link_worktree(root, quiet=True)
    if actions:
        print(f"gate: linked the phase workflow into {root}", file=sys.stderr)


# ------------------------------------------------------- checks worktree

def checks_ready():
    return (CHECKS_WORKTREE / ".git").exists() and (CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE).exists()


def checks_environment():
    environment = toolchain_environment()
    environment.update(environment_file_values(CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE))
    return environment


def checks_test_environment():
    return {**checks_environment(), "RAILS_ENV": "test"}


def checks_in_use_path():
    return CHECKS_WORKTREE / PROOF_DIRECTORY / CHECKS_IN_USE_FILE_NAME


@contextlib.contextmanager
def checks_lock():
    CHECKS_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKS_LOCK_FILE, "w") as handle:
        waited = 0
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if waited >= CHECKS_LOCK_TIMEOUT_SECONDS:
                    raise RuntimeError(f"the checks worktree stayed locked for {CHECKS_LOCK_TIMEOUT_SECONDS} seconds")
                time.sleep(5)
                waited += 5
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def current_branch(root):
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def migrations_in(root, base):
    return any(path.startswith(MIGRATIONS_DIRECTORY) for path in changed_files_all(root, base))


def apply_branch_to_checks(root):
    """Put the checks worktree into the exact state of root: its HEAD commit plus its working tree."""
    in_use = read_json(checks_in_use_path()) or {}
    branch = current_branch(root)
    if in_use and in_use.get("branch") != branch:
        raise RuntimeError(f"the checks worktree is in use by {in_use.get('branch')} since {in_use.get('at')}; finish there, then gate.py checks reset")
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    git(CHECKS_WORKTREE, "checkout", "--detach", "--force", "--quiet", head)
    git(CHECKS_WORKTREE, "reset", "--hard", "--quiet")
    git(CHECKS_WORKTREE, "clean", "-fd", "--quiet")
    # HEAD, not the merge-base: the checks worktree is already at this commit, so only the working tree
    # is missing. A branch-delta patch would apply the committed part a second time.
    patch = snapshot_patch(root, "HEAD")
    if patch.strip():
        git(CHECKS_WORKTREE, "apply", "--whitespace=nowarn", "-", input_text=patch)
    migrations = migrations_in(root, diff_base(root)) or bool(in_use.get("migrations"))
    write_json(checks_in_use_path(), {"branch": branch, "source_root": str(root), "head": head,
                                      "patch_sha": patch_digest(patch), "migrations": migrations, "at": now_iso()})
    steps = [("apply", "ok", f"{branch} at {head[:10]} plus {len(patch.splitlines())} patch line(s) in {CHECKS_WORKTREE}")]
    platform = CHECKS_WORKTREE / "platform"
    if migrations:
        steps.append(command_check("db:test:prepare", ["bundle", "exec", "rake", "db:test:prepare"], platform, environment=checks_test_environment()))
        steps.append(command_check("db:prepare", ["bin/rails", "db:prepare"], platform, environment=checks_environment()))
    steps.append(command_check("routes", ["bundle", "exec", "rake", "js:routes:typescript"], platform, environment=checks_test_environment()))
    return steps


def reset_checks_worktree(rebuild_dev_db=False):
    """Back to a clean origin/main. The test database is reloaded when the branch carried migrations;
    the development database is rebuilt only on request, because that reseeds the demo data."""
    in_use = read_json(checks_in_use_path()) or {}
    git(CHECKS_WORKTREE, "fetch", "--quiet", "origin", "main")
    git(CHECKS_WORKTREE, "checkout", "--detach", "--force", "--quiet", UPSTREAM_BRANCH)
    git(CHECKS_WORKTREE, "reset", "--hard", "--quiet")
    git(CHECKS_WORKTREE, "clean", "-fd", "--quiet")
    platform = CHECKS_WORKTREE / "platform"
    steps = []
    if in_use.get("migrations"):
        steps.append(command_check("db:test:prepare (reset)", ["bundle", "exec", "rake", "db:test:prepare"], platform, environment=checks_test_environment()))
    if rebuild_dev_db:
        steps.append(command_check("db:drop db:prepare (reset)", ["bin/rails", "db:drop", "db:prepare"], platform, environment=checks_environment()))
    checks_in_use_path().unlink(missing_ok=True)
    head = git(CHECKS_WORKTREE, "rev-parse", "--short", "HEAD").stdout.strip()
    steps.append(("reset", "ok", f"checks worktree back at {UPSTREAM_BRANCH} {head}, clean"))
    return steps


def print_steps(steps):
    for name, status, detail in steps:
        first, *rest = detail.splitlines() or [""]
        print(f"[{status:4}] {name}: {first}")
        for line in rest:
            print(f"       {line}")


def setup_checks():
    if not (CHECKS_WORKTREE / ".git").exists():
        git(MAIN_REPOSITORY, "fetch", "origin", "main")
        git(MAIN_REPOSITORY, "worktree", "add", "--detach", str(CHECKS_WORKTREE), UPSTREAM_BRANCH)
        print(f"created {CHECKS_WORKTREE} at {UPSTREAM_BRANCH}")
    completed = subprocess.run([str(CHECKS_WORKTREE / WORKTREE_SETUP_SCRIPT)], cwd=str(CHECKS_WORKTREE), env=toolchain_environment(),
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=FULL_SUITE_TIMEOUT_SECONDS)
    print(completed.stdout.strip()[-1500:])
    if completed.returncode != 0:
        print(completed.stderr.strip()[-1500:])
        sys.exit(1)
    values = environment_file_values(CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE)
    print(f"checks worktree ready: slot {values.get('IPAAS_WT_SLOT')}, web port {values.get('WEB_PORT')}")


def checks(arguments):
    if not arguments or arguments[0] not in ("apply", "reset", "status"):
        print("usage: gate.py checks apply | reset [--rebuild-dev-db] | status")
        sys.exit(2)
    if not checks_ready():
        print(f"no checks worktree with a slot at {CHECKS_WORKTREE}; run gate.py setup-checks")
        sys.exit(1)
    if arguments[0] == "status":
        in_use = read_json(checks_in_use_path())
        head = git(CHECKS_WORKTREE, "rev-parse", "--short", "HEAD").stdout.strip()
        dirty = git(CHECKS_WORKTREE, "status", "--porcelain").stdout.strip()
        values = environment_file_values(CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE)
        print(f"{CHECKS_WORKTREE}: HEAD {head}, {'dirty' if dirty else 'clean'}, slot {values.get('IPAAS_WT_SLOT')}, web port {values.get('WEB_PORT')}")
        print(f"in use by {in_use['branch']} since {in_use['at']}" if in_use else "free")
        return
    with checks_lock():
        if arguments[0] == "apply":
            root = repository_root(os.getcwd())
            if root is None or root.resolve() == CHECKS_WORKTREE.resolve():
                print("run gate.py checks apply from the PR worktree")
                sys.exit(2)
            try:
                steps = apply_branch_to_checks(root)
            except RuntimeError as error:
                print(f"checks apply refused: {error}")
                sys.exit(1)
            print_steps(steps)
            values = environment_file_values(CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE)
            print(f"live check: cd {CHECKS_WORKTREE} && source {WORKTREE_ENVIRONMENT_FILE} && cd platform && bin/dev   then open http://127.0.0.1:{values.get('WEB_PORT')}")
            print("when done: gate.py checks reset  (add --rebuild-dev-db after exploratory migrations)")
            sys.exit(1 if any(status == "FAIL" for _, status, _ in steps) else 0)
        steps = reset_checks_worktree(rebuild_dev_db="--rebuild-dev-db" in arguments)
        print_steps(steps)
        sys.exit(1 if any(status == "FAIL" for _, status, _ in steps) else 0)


# ----------------------------------------------------------------- setup

def setup():
    if not GATE_WORKTREE.exists():
        GATE_WORKTREE.parent.mkdir(parents=True, exist_ok=True)
        git(MAIN_REPOSITORY, "worktree", "add", "--detach", str(GATE_WORKTREE), "HEAD")
        print(f"worktree created at {GATE_WORKTREE}")
    ensure_environment_links()
    ensure_shared_links()
    ensure_gate_redis()
    ensure_gate_git()
    write_gate_test_environment()
    gate_values = gate_test_environment()
    print(f"gate env verified: {gate_values['DB_WRITER_DATABASE']}, {gate_values['DB_QUEUE_DATABASE']}, {gate_values['REDIS_URL']}")
    completed = subprocess.run(["bundle", "exec", "rake", "db:test:prepare"], cwd=str(GATE_WORKTREE / "platform"),
                               env=prover_environment(), capture_output=True, text=True, timeout=RSPEC_TIMEOUT_SECONDS)
    print(f"db:test:prepare exit {completed.returncode}")
    if completed.returncode != 0:
        print(completed.stderr[-2000:])
        sys.exit(1)


def ensure_shared_links():
    for relative in SHARED_LINK_TARGETS:
        source_path = MAIN_REPOSITORY / relative
        target = GATE_WORKTREE / relative
        if source_path.exists() and not target.exists() and not target.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source_path)
            print(f"linked {relative}")


def ensure_gate_redis():
    ensure_container(GATE_REDIS_CONTAINER, "run install.sh, which creates it")


def ensure_gate_git():
    ensure_container(GATE_GIT_CONTAINER, "run install.sh, which builds the image and creates it")


def ensure_container(name, how_to_start):
    status = run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Status}}"]).stdout.strip()
    if status.startswith("Up"):
        return
    raise RuntimeError(f"container {name} is not running; {how_to_start}")


def write_gate_test_environment():
    """Seed from the main repo's .env.test.local (host, port, password) and override
    only the isolation keys. Mirrors .claude/bin/setup-worktree, which cannot be
    used directly because its seven Redis slots are all allocated."""
    source_path = MAIN_REPOSITORY / TEST_ENVIRONMENT_FILE
    target = GATE_WORKTREE / TEST_ENVIRONMENT_FILE
    if target.is_symlink():
        target.unlink()
    overrides = {
        "DB_WRITER_DATABASE": GATE_DATABASE,
        "DB_READER_DATABASE": GATE_DATABASE,
        "DB_QUEUE_DATABASE": GATE_QUEUE_DATABASE,
        "REDIS_URL": GATE_REDIS_URL,
    }
    lines = source_path.read_text().splitlines() if source_path.is_file() else []
    seen = set()
    output = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.startswith("#") else None
        if key in overrides:
            output.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in overrides.items():
        if key not in seen:
            output.append(f"{key}={value}")
    target.write_text("\n".join(output) + "\n")
    print(f"generated {target.relative_to(GATE_WORKTREE)}")



# ------------------------------------------------------------ references

def diff_base(root):
    completed = git(root, "merge-base", "HEAD", UPSTREAM_BRANCH, allow_exit_codes=(0, 1, 128))
    return completed.stdout.strip() if completed.returncode == 0 and completed.stdout.strip() else "HEAD"


def changed_code_files(root, base):
    tracked = git(root, "diff", "--name-only", base).stdout.split()
    return sorted(path for path in set(tracked + untracked_paths(root)) if is_code_path(path))


def claude_commits(root, base):
    revisions = git(root, "rev-list", f"{base}..HEAD", allow_exit_codes=(0, 128)).stdout.split()
    return [sha for sha in revisions if is_gated_commit(root, sha)]


def is_gated_commit(root, sha):
    """A Claude-authored commit created after the gate existed."""
    body, committed_at = git(root, "log", "-1", "--format=%B%x00%cI", sha).stdout.split("\x00")
    committed = datetime.fromisoformat(committed_at.strip())
    return bool(CLAUDE_TRAILER_PATTERN.search(body)) and committed >= datetime.fromisoformat(GATE_EPOCH)


def code_files_owed_proof(root, base):
    """Uncommitted code changes plus code touched by Claude-authored commits on the branch.
    Human commits are outside the gate, the same way pre-push treats them."""
    uncommitted = git(root, "diff", "--name-only", "HEAD").stdout.split() + untracked_paths(root)
    committed = []
    for sha in claude_commits(root, base):
        committed += git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
    return sorted(path for path in set(uncommitted + committed) if is_code_path(path))


def definitions_in(disk_path, display_path):
    completed = run(["ctags", "--output-format=json", "--fields=+nKz", "-f", "-", str(disk_path)], allow_exit_codes=(0, 1))
    definitions = []
    for line in completed.stdout.splitlines():
        try:
            tag = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, scope = tag.get("kind"), tag.get("scope")
        if kind not in DEFINITION_KINDS:
            continue
        if kind in UNSCOPED_ONLY_KINDS and scope and Path(display_path).suffix in (".ts", ".tsx", ".js", ".jsx"):
            continue
        definitions.append({"name": tag["name"], "kind": kind, "scope": scope, "line": tag.get("line"), "file": display_path})
    return definitions


def base_definitions(root, base, path):
    completed = git(root, "show", f"{base}:{path}", allow_exit_codes=(0, 128))
    if completed.returncode != 0:
        return []
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=Path(path).suffix, delete=False) as handle:
        handle.write(completed.stdout)
        temporary = handle.name
    try:
        return definitions_in(temporary, path)
    finally:
        os.unlink(temporary)


def removed_definitions(root, base):
    removed = []
    for path in changed_code_files(root, base):
        before = base_definitions(root, base, path)
        if not before:
            continue
        current_path = Path(root) / path
        after_names = {each["name"] for each in definitions_in(current_path, path)} if current_path.exists() else set()
        seen = set()
        for definition in before:
            if definition["name"] not in after_names and definition["name"] not in seen:
                seen.add(definition["name"])
                removed.append(definition)
    return removed


def reference_pattern(name):
    return r"(?<!\w)" + re.escape(name) + r"(?![\w?!=])"


def search_references(root, name):
    command = ["rg", "-P", "--json", "-n", "--no-messages", "-e", reference_pattern(name)]
    for glob in REFERENCE_GLOBS + REFERENCE_EXCLUDES:
        command += ["-g", glob]
    command += ["--", "."]
    completed = run(command, cwd=str(root), allow_exit_codes=(0, 1))
    hits = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        text = data["lines"]["text"].rstrip("\n")
        start = data["submatches"][0]["start"] if data.get("submatches") else 0
        path = data["path"]["text"]
        hits.append({"path": path, "line": data["line_number"], "text": text.strip()[:160],
                     "category": classify_hit(path, text, start, name)})
    return hits


def classify_hit(path, text, start, name):
    escaped = re.escape(name)
    stripped = text.lstrip()
    if re.match(r"(#|//|\*|<!--|/\*)", stripped):
        return "comment"
    ruby_definition = rf"^\s*(def\s+(self\.)?{escaped}(?![\w?!=])|class\s+{escaped}\b|module\s+{escaped}\b|{escaped}\s*=(?!=)|attr_(reader|writer|accessor)\b[^#]*:{escaped}\b|alias(_method)?\s+:?{escaped}\b)"
    script_definition = rf"^\s*(export\s+)?(default\s+)?(async\s+)?(function\s+{escaped}\b|(const|let|var)\s+{escaped}\b|class\s+{escaped}\b|interface\s+{escaped}\b|type\s+{escaped}\b|enum\s+{escaped}\b)"
    if re.search(ruby_definition, text) or re.search(script_definition, text):
        return "definition"
    before = text[:start]
    if before.endswith(":") and not before.endswith("::"):
        return "symbol"
    if inside_string_literal(before):
        return "string_literal"
    if is_spec_path(path):
        return "spec"
    return "code"


def inside_string_literal(before):
    interpolation_open = before.rfind("#{")
    if interpolation_open != -1 and "}" not in before[interpolation_open:]:
        return False
    double = len(re.findall(r'(?<!\\)"', before)) % 2 == 1
    single = len(re.findall(r"(?<!\\)'", before)) % 2 == 1
    return double or single


def self_test_unprovable_rules():
    hits = scan_unprovable([str(UNPROVABLE_FIXTURE)], cwd=str(UNPROVABLE_FIXTURE.parent))
    if len(hits) != UNPROVABLE_FIXTURE_EXPECTED_MATCHES:
        raise RuntimeError(f"unprovable rules self-test: expected {UNPROVABLE_FIXTURE_EXPECTED_MATCHES} matches on the fixture, got {len(hits)}; refusing to report a clean verdict")


def scan_unprovable(paths, cwd):
    command = ["ast-grep", "scan", "--inline-rules", UNPROVABLE_RULES.read_text(), "--json=stream"]
    for glob in SCAN_EXCLUDES:
        command += ["--globs", glob]
    command += paths
    completed = run(command, cwd=cwd, allow_exit_codes=(0, 1))
    if "must have kind" in completed.stderr:
        raise RuntimeError("unprovable rules were dropped by ast-grep: " + completed.stderr.strip()[:300])
    hits = []
    for line in completed.stdout.splitlines():
        try:
            match = json.loads(line)
        except json.JSONDecodeError:
            continue
        hits.append({"path": match["file"], "line": match["range"]["start"]["line"] + 1, "text": match["lines"].strip()[:160]})
    return hits


def dynamic_dispatch_sites(root, projects):
    self_test_unprovable_rules()
    existing = [project for project in projects if (Path(root) / project).is_dir()]
    return scan_unprovable(existing, cwd=str(root)) if existing else []


def references_report(root, names=None):
    base = diff_base(root)
    if names:
        removed = [{"name": name, "kind": "requested", "scope": None, "line": None, "file": None} for name in names]
    else:
        removed = removed_definitions(root, base)
    truncated = len(removed) > MAX_REMOVED_NAMES
    removed = removed[:MAX_REMOVED_NAMES]
    projects = sorted({project_of(each["file"]) for each in removed if each["file"] and project_of(each["file"])}) or list(RUBY_PROJECTS)
    dynamic_sites = dynamic_dispatch_sites(root, projects)
    symbols = []
    for definition in removed:
        hits = search_references(root, definition["name"])
        counts = {}
        for hit in hits:
            counts[hit["category"]] = counts.get(hit["category"], 0) + 1
        remaining = [hit for hit in hits if hit["category"] not in ("comment", "definition")]
        if remaining:
            verdict = VERDICT_USED
        elif dynamic_sites:
            verdict = VERDICT_ENUMERATED
        else:
            verdict = VERDICT_UNUSED
        symbols.append({**definition, "verdict": verdict, "counts": counts, "remaining_count": len(remaining),
                        "still_defined_at": [f"{hit['path']}:{hit['line']}" for hit in hits if hit["category"] == "definition"][:10],
                        "remaining": remaining[:MAX_HITS_PER_NAME], "truncated": len(remaining) > MAX_HITS_PER_NAME})
    verdicts = [each["verdict"] for each in symbols]
    if not symbols:
        overall = STATUS_NOT_APPLICABLE
    elif VERDICT_USED in verdicts:
        overall = VERDICT_USED
    elif VERDICT_ENUMERATED in verdicts:
        overall = VERDICT_ENUMERATED
    else:
        overall = VERDICT_UNUSED
    return {"created_at": now_iso(), "base": base, "verdict": overall, "removed_truncated": truncated,
            "symbols": symbols, "dynamic_dispatch_sites": dynamic_sites, "projects_scanned": projects}


def references_directory(root):
    return proof_directory(root) / REFERENCES_DIRECTORY_NAME


def write_references_report(root, report):
    directory = references_directory(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_json(directory / f"{stamp}.json", report)
    write_json(directory / "latest.json", report)
    return directory / f"{stamp}.json"


def latest_references_report(root):
    return read_json(references_directory(root) / "latest.json")


def summarize_references(report, limit=6):
    if report["verdict"] == STATUS_NOT_APPLICABLE:
        return "references: no definitions were removed or renamed against " + report["base"][:10] + "."
    lines = [f"references: {report['verdict']}."]
    for symbol in report["symbols"]:
        origin = f"{symbol['file']}:{symbol['line']}" if symbol["file"] else "requested"
        lines.append(f"  {symbol['name']} ({symbol['kind']}, was {origin}) -> {symbol['verdict']}, {symbol['remaining_count']} remaining reference(s)")
        for hit in symbol["remaining"][:limit]:
            lines.append(f"    {hit['path']}:{hit['line']} [{hit['category']}] {hit['text'][:90]}")
        if symbol["remaining_count"] > limit:
            lines.append(f"    ... {symbol['remaining_count'] - limit} more")
        if symbol["still_defined_at"]:
            lines.append(f"    still defined at: {', '.join(symbol['still_defined_at'][:3])}")
    if report["dynamic_dispatch_sites"]:
        sites = report["dynamic_dispatch_sites"]
        lines.append(f"  cannot prove completeness: {len(sites)} dynamic dispatch site(s) in {', '.join(report['projects_scanned'])}, for example:")
        for site in sites[:4]:
            lines.append(f"    {site['path']}:{site['line']} {site['text'][:80]}")
    if report["removed_truncated"]:
        lines.append(f"  more than {MAX_REMOVED_NAMES} definitions removed; list truncated")
    return "\n".join(lines)


def references(arguments):
    names = [value for flag, value in zip(arguments, arguments[1:]) if flag == "--name"]
    root = repository_root(os.getcwd())
    if root is None:
        print("not inside a git repository", file=sys.stderr)
        sys.exit(2)
    report = references_report(root, names or None)
    if not names:
        written = write_references_report(root, report)
        print(f"written {written.relative_to(root)}")
    print(summarize_references(report, limit=12))


# ------------------------------------------------------------ pr-section

def pr_section():
    root = repository_root(os.getcwd())
    run_directory = latest_run(root) if root else None
    findings = read_json(run_directory / FINDINGS_FILE_NAME) if run_directory else None
    report = references_report_for(root)
    lines = ["## Verification", ""]
    if findings:
        lines.append(f"**Revert proof** `{findings.get('id')}`: **{findings.get('status')}**")
        for proof in findings.get("proofs", []):
            lines.append(f"- `{proof['spec_file']}` -e `{proof.get('example', '')}`: {proof['status']}" + (f" ({proof['reason']})" if proof.get("reason") else ""))
    else:
        lines.append("**Revert proof**: none recorded.")
    lines.append("")
    if report:
        lines.append(f"**Reference impact**: **{report['verdict']}**")
        for symbol in report["symbols"]:
            lines.append(f"- `{symbol['name']}` ({symbol['kind']}): {symbol['verdict']}, {symbol['remaining_count']} remaining reference(s)")
            for hit in symbol["remaining"][:8]:
                lines.append(f"  - `{hit['path']}:{hit['line']}` [{hit['category']}]")
        if report["verdict"] == STATUS_NOT_APPLICABLE:
            lines.append(f"- No definition was removed or renamed against `{report['base'][:10]}`, so no reference could be left behind.")
        if report["dynamic_dispatch_sites"]:
            lines.append(f"- Completeness cannot be proven: {len(report['dynamic_dispatch_sites'])} dynamic dispatch site(s) in {', '.join(report['projects_scanned'])}. "
                         "Ruby resolves `send`, `const_get`, `method_missing` and interpolated names at runtime.")
    else:
        lines.append("**Reference impact**: could not be computed here (not inside the repository).")
    print("\n".join(lines))


def references_report_for(root):
    """The stored report when it matches the current base, otherwise a fresh one. `pr-section` must never
    say `not computed`: a missing report is a check that has not run, and the reader cannot tell the
    difference between that and a clean result."""
    if root is None:
        return None
    stored = latest_references_report(root)
    if stored and stored.get("base") == diff_base(root):
        return stored
    report = references_report(root)
    write_references_report(root, report)
    return report


# ------------------------------------------------------ randomized suite

def prepare_pristine_databases():
    """Some specs commit rows outside the test transaction (encryption key pointers dated by
    UTC day, for one). A full randomized run starts from schema-only databases so its result
    does not depend on what earlier runs leaked."""
    completed = subprocess.run(["bundle", "exec", "rake", "db:test:prepare"], cwd=str(GATE_WORKTREE / "platform"),
                               env=prover_environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=RSPEC_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise RuntimeError("db:test:prepare failed: " + (completed.stderr or completed.stdout)[-500:])


def locked_rspec(arguments):
    """Ad-hoc rspec in the gate worktree, under the same lock the prover and the randomized
    suite take. Running rspec there any other way races a suite for tmp/test-git and the
    git server, and corrupts both runs."""
    GATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_LOCK_FILE, "w") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        completed = subprocess.run(["bundle", "exec", "rspec", *arguments], cwd=str(GATE_WORKTREE / "platform"),
                                   env=prover_environment(), stdin=subprocess.DEVNULL, timeout=FULL_SUITE_TIMEOUT_SECONDS)
    sys.exit(completed.returncode)


def randomized_suite(arguments):
    seed = int(arguments[arguments.index("--seed") + 1]) if "--seed" in arguments else RANDOMIZED_SUITE_SEED
    base_sha = git(MAIN_REPOSITORY, "rev-parse", "HEAD").stdout.strip()
    report_path = GATE_WORKTREE.parent / f".gate-randomized-{seed}.json"
    json_path = GATE_WORKTREE.parent / f".gate-randomized-{seed}-rspec.json"
    GATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_LOCK_FILE, "w") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        reset_gate_worktree(base_sha)
        prepare_pristine_databases()
        command = ["bundle", "exec", "rspec", "spec/unit", "--order", f"rand:{seed}", "--format", "json", "--out", str(json_path)]
        completed = subprocess.run(command, cwd=str(GATE_WORKTREE / "platform"), env=prover_environment(),
                                   capture_output=True, text=True, timeout=FULL_SUITE_TIMEOUT_SECONDS)
    result = read_json(json_path, default={}) or {}
    summary = result.get("summary", {})
    failures = [{"id": each.get("id"), "description": each.get("full_description"),
                 "message": (each.get("exception") or {}).get("message", "")[:300]}
                for each in result.get("examples", []) if each.get("status") == "failed"]
    write_json(report_path, {"seed": seed, "base_sha": base_sha, "exit_code": completed.returncode, "summary": summary,
                             "failures": failures, "finished_at": now_iso()})
    print(f"seed {seed}: {summary.get('example_count', 0)} examples, {summary.get('failure_count', 0)} failures, "
          f"{summary.get('errors_outside_of_examples_count', 0)} load errors, exit {completed.returncode}")
    for failure in failures[:30]:
        print(f"  {failure['id']}  {failure['description'][:90]}")
    print(f"report: {report_path}")

# ------------------------------------------------------------------ main

def main(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(2)
    command, arguments = argv[1], argv[2:]
    dispatch = {
        "setup": lambda: setup(),
        "launch": lambda: launch(),
        "prove": lambda: prove(arguments[0]),
        "stop": lambda: stop(),
        "surface": lambda: surface(),
        "commit-msg": lambda: commit_msg(arguments),
        "pre-push": lambda: pre_push(),
        "skip-once": lambda: skip_once(arguments),
        "guard": lambda: guard(),
        "finalize": lambda: finalize(arguments),
        "setup-checks": lambda: setup_checks(),
        "checks": lambda: checks(arguments),
        "link-worktree": lambda: link_worktree_command(arguments),
        "post-checkout": lambda: post_checkout(arguments),
        "references": lambda: references(arguments),
        "pr-section": lambda: pr_section(),
        "randomized-suite": lambda: randomized_suite(arguments),
        "rspec": lambda: locked_rspec(arguments),
    }
    if command not in dispatch:
        print(f"unknown subcommand {command}\n{__doc__}")
        sys.exit(2)
    dispatch[command]()


if __name__ == "__main__":
    main(sys.argv)
