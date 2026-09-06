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
  pre-push    pre-push git hook. Refuses Claude commits that lack a passing proof.
  references  Reference impact of removed or renamed definitions (or --name X).
              Verdict per symbol: Unused | Enumerated | Used.
  pr-section  Markdown block for a PR description, stamped from the findings.
  randomized-suite  Run spec/unit under --order rand:SEED in the gate worktree.
  rspec       Run rspec in the gate worktree under the gate lock (never run it any other way).

The proof: revert the non-spec hunks in an isolated worktree, run the declared
spec example, require it to FAIL. Restore the change, require it to PASS. Then
run the spec file under several random seeds and require it to stay green.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
MAIN_REPOSITORY = HOME / "work/ipaas"
GATE_WORKTREE = HOME / "work/ipaas_worktrees/gate"
GATE_LOCK_FILE = GATE_WORKTREE.parent / ".gate.lock"
GATE_DATABASE = "ipaas_gate_test"
GATE_QUEUE_DATABASE = "ipaas_queue_gate_test"
GATE_REDIS_CONTAINER = "ipaas-gate-redis"
GATE_REDIS_URL = "redis://127.0.0.1:26380/1"
GATE_GIT_NAMESPACE = "worktree_gate/%{prefix}/%{solution_repo_name}"  # same shape setup-worktree writes
GATE_GIT_CONTAINER = "ipaas-gate-git"
GATE_GIT_PORT = "23232"  # soft-serve keeps state in SQLite, which refuses concurrent writers
SHARED_LINK_TARGETS = ("platform/node_modules", "git-server/ssh-user")
TEST_ENVIRONMENT_FILE = Path("platform/.env.test.local")
SHARED_ENVIRONMENT_FILE_NAMES = (".env.local", ".env")
RUBY_PROJECTS = ("platform", "connector", "connector-sdk")

PROOF_DIRECTORY = Path(".claude/proof")
DECLARATION_FILE_NAME = "declaration.json"
RUNS_DIRECTORY_NAME = "runs"
SURFACED_MARKER_NAME = ".surfaced"
PATCH_FILE_NAME = "patch.diff"
META_FILE_NAME = "meta.json"
FINDINGS_FILE_NAME = "findings.json"
LOG_FILE_NAME = "prover.log"

FLAKE_SEEDS = (11, 22, 33)
RSPEC_TIMEOUT_SECONDS = 900
FULL_SUITE_TIMEOUT_SECONDS = 3600  # spec/unit takes 24 to 28 minutes in the gate
SYSTEM_SPEC_MARKER = "/spec/system/"
CODE_EXTENSIONS = {".rb", ".rake", ".erb", ".ru", ".ts", ".tsx", ".js", ".jsx"}
RBENV_SHIMS = HOME / ".rbenv/shims"
HOMEBREW_BIN = Path("/opt/homebrew/bin")

CLAUDE_TRAILER_PATTERN = re.compile(r"^Co-Authored-By: Claude", re.MULTILINE)
REFERENCES_DIRECTORY_NAME = "references"
UNPROVABLE_RULES = Path(__file__).resolve().parent / "unprovable.yml"
UNPROVABLE_FIXTURE = Path(__file__).resolve().parent / "unprovable_fixture.rb"
UNPROVABLE_FIXTURE_EXPECTED_MATCHES = 9
REFERENCE_GLOBS = ("*.rb", "*.rake", "*.erb", "*.ru", "*.ts", "*.tsx", "*.js", "*.jsx", "*.yml", "*.yaml")
REFERENCE_EXCLUDES = ("!.claude/**", "!**/node_modules/**", "!**/coverage/**", "!**/log/**", "!**/tmp/**", "!**/db/schema.rb", "!**/vendor/**")
SCAN_EXCLUDES = ("!**/spec/**", "!**/node_modules/**", "!**/.claude/**", "!**/tmp/**", "!**/vendor/**")
DEFINITION_KINDS = {"class", "module", "method", "singletonMethod", "constant", "accessor", "alias",
                    "function", "interface", "type", "enum", "variable"}
NOISE_PATH_MARKERS = ("/locales/", "/db/schema.rb", "/db/migrate/")
UNSCOPED_ONLY_KINDS = {"variable", "constant"}
MAX_REMOVED_NAMES = 40
MAX_HITS_PER_NAME = 80
UPSTREAM_BRANCH = "origin/main"
VERDICT_UNUSED = "Unused"
VERDICT_ENUMERATED = "Enumerated"
VERDICT_USED = "Used"
REFERENCES_VERDICT_TRAILER = "References-Verdict"
UNPROVABLE_TRAILER = "Unprovable-References"
RANDOMIZED_SUITE_SEED = 11
GATE_EPOCH = "2026-09-06T00:00:00+00:00"  # commits authored before the gate existed are exempt
PROOF_ID_TRAILER = "Proof-Id"
PROOF_STATUS_TRAILER = "Proof-Status"
PROOF_FRESH_TRAILER = "Proof-Fresh"
SKIP_ENVIRONMENT_VARIABLE = "GATE_SKIP"
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


def is_spec_path(path):
    name = Path(path).name
    return (
        bool(re.search(r"(^|/)spec/", path))
        or name.endswith("_spec.rb")
        or ".test." in name
        or "/__tests__/" in f"/{path}"
    )


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


def snapshot_patch(root):
    tracked = git(root, "diff", "HEAD", "--binary", "--no-color", "--no-ext-diff",
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
        (spec_blocks if is_spec_path(path) else code_blocks).append(block)
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
    declaration_path = declaration_written(payload.get("tool_input") or {})
    if declaration_path is None:
        return
    root = repository_root(declaration_path.parent)
    if root is None:
        return
    declaration = read_json(declaration_path)
    if not declaration or not declaration.get("proofs"):
        emit_context(f"gate: {declaration_path} has no \"proofs\" list; nothing launched.")
        return

    patch = snapshot_patch(root)
    spec_patch, code_patch, paths = split_patch(patch)
    base_sha = git(root, "rev-parse", "HEAD").stdout.strip()
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
    if code_paths and not declaration:
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
            if report["verdict"] != STATUS_NOT_APPLICABLE:
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
        print("\n".join(lines))


# ------------------------------------------------------------ commit-msg

def commit_msg(arguments):
    if len(arguments) >= 2 and arguments[1] in ("merge", "squash"):
        return
    message_path = Path(arguments[0])
    if not CLAUDE_TRAILER_PATTERN.search(message_path.read_text()):
        return
    root = repository_root(os.getcwd())
    if root is None:
        return
    staged = git(root, "diff", "--cached", "--name-only").stdout.split()
    if not any(is_code_path(path) for path in staged):
        return
    run_directory = latest_run(root)
    if run_directory is None:
        return
    findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
    meta = read_json(run_directory / META_FILE_NAME) or {}
    fresh = "yes" if meta.get("patch_sha") == patch_digest(snapshot_patch(root)) else "no"
    trailers = [(PROOF_ID_TRAILER, run_directory.name), (PROOF_STATUS_TRAILER, findings.get("status", STATUS_PENDING)), (PROOF_FRESH_TRAILER, fresh)]
    report = latest_references_report(root)
    if report and report.get("verdict") != STATUS_NOT_APPLICABLE:
        trailers.append((REFERENCES_VERDICT_TRAILER, report["verdict"]))
        if report.get("dynamic_dispatch_sites"):
            trailers.append((UNPROVABLE_TRAILER, f"{len(report['dynamic_dispatch_sites'])} dynamic dispatch sites in {', '.join(report['projects_scanned'])}"))
    for trailer, value in trailers:
        git(root, "interpret-trailers", "--in-place", "--if-exists", "replace", "--trailer", f"{trailer}: {value}", str(message_path))


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
    if os.environ.get(SKIP_ENVIRONMENT_VARIABLE):
        print(f"gate: {SKIP_ENVIRONMENT_VARIABLE} is set, pushing anyway.", file=sys.stderr)
        return
    print(f"gate: set {SKIP_ENVIRONMENT_VARIABLE}=1 to override on purpose.", file=sys.stderr)
    sys.exit(1)


def commit_violations(root, sha):
    if not is_gated_commit(root, sha):
        return []
    body = git(root, "log", "-1", "--format=%B", sha).stdout
    files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
    if not any(is_code_path(path) for path in files):
        return []
    short = sha[:10]
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
    ensure_container(GATE_REDIS_CONTAINER, f"docker run -d --name {GATE_REDIS_CONTAINER} --restart unless-stopped -p 26380:6379 valkey/valkey:8-alpine")


def ensure_gate_git():
    ensure_container(GATE_GIT_CONTAINER, "see ci/git-server-create.sh: soft-serve from ipaas-git-test:latest on port "
                     f"{GATE_GIT_PORT} with SOFT_SERVE_INITIAL_ADMIN_KEYS, then ci/git-server-configure.sh")


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
    report = latest_references_report(root) if root else None
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
        if report["dynamic_dispatch_sites"]:
            lines.append(f"- Completeness cannot be proven: {len(report['dynamic_dispatch_sites'])} dynamic dispatch site(s) in {', '.join(report['projects_scanned'])}. "
                         "Ruby resolves `send`, `const_get`, `method_missing` and interpolated names at runtime.")
    else:
        lines.append("**Reference impact**: not computed.")
    print("\n".join(lines))


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
