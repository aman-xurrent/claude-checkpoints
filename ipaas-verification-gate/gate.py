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
              the checks worktree for a live check, or return it to origin/main. With no
              argument it prints the status.
  references  Reference impact of removed or renamed definitions (or --name X).
  review-record --skill S --items-file F   Record one review skill's findings against the
              current diff. review-section prints the recorded reviews as markdown.
              Verdict per symbol: Unused | Enumerated | Used.
  pr-section  Markdown block for a PR description, stamped from the findings.
  randomized-suite  Run spec/unit under --order rand:SEED in the gate worktree.
  rspec       Run rspec in the gate worktree under the gate lock (never run it any other way).
  trace       What every gate call was asked and what it answered. [--session ID] [--command NAME]
              [--last N] [--day YYYY-MM-DD] [--failures] [--full] [--sessions]
  answers     PostToolUse hook for AskUserQuestion. Stores what the owner typed in a question dialog.
  units       The unit report of the latest proof run. `units --measure [--out DIR] [--budget SECONDS]` runs
              the unit stage on this worktree's change, under the gate lock, to measure it.
  waive --unit DIGEST --evidence "<the owner's words>"   Let one surviving unit through, on the
              owner's own words typed after the run that reported it.

The proof: revert the non-spec hunks in an isolated worktree, run the declared
spec example, require it to FAIL. Restore the change, require it to PASS. Then
run the spec file under several random seeds and require it to stay green.
"""

import contextlib
import fcntl
import fnmatch
import io
import hashlib
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import sys
import time
import traceback
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import NamedTuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

# =============================================================================
# PROJECT CONFIGURATION. Every constant in this block encodes an ipaas assumption.
# Adapting the gate to another repository means changing this block, the four
# functions named in the comments, and install.sh. Nothing below the next banner
# knows about ipaas.
# =============================================================================
HOME = Path.home()
# Every stored time, and the day files named after it, compare as text in this one format.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DAY_LENGTH = len("YYYY-MM-DD")

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
LOCAL_BIN_NAMES = ("agent_task_finalize", "pr-comment", "pr-phase", "review-record", "deviation", "comment-triage", "live-record",
                   "pr-diagram", "pr-create")
LOCAL_PROTOCOL_FILE = Path(__file__).resolve().parent / "CLAUDE.local.md"
LOCAL_SETTINGS_FILE = Path(".claude/settings.local.json")
# No trailing slash: the skill entries are symlinks, and git matches a symlink as a file.
LOCAL_EXCLUDE_ENTRIES = ("**/.claude/skills/phase", "**/.claude/skills/phase-[1-7]", "**/.claude/skills/phase-comments",
                         "**/.claude/bin/agent_task_finalize", "**/.claude/bin/pr-comment", "**/.claude/bin/pr-phase",
                         "**/.claude/bin/review-record", "**/.claude/bin/deviation", "**/.claude/bin/pr-diagram",
                         "**/.claude/bin/pr-create", "**/.claude/bin/comment-triage",
                         "**/.claude/deviations/", "**/.claude/comments/", "**/.claude/bin/live-record",
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
# Phases 1 to 5 add no behaviour, so their commits skip CI. Any of the five bracket keywords works, in
# the subject or the body; the hook writes one and pre-push refuses one on a phase that must run CI.
CI_SKIP_PHASES = PROOF_EXEMPT_PHASES
CI_SKIP_KEYWORD = "[skip ci]"
CI_SKIP_PATTERN = re.compile(r"\[(?:skip ci|ci skip|no ci|skip actions|actions skip)\]", re.IGNORECASE)
TRAILER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")
CONNECTOR_FIXTURES = "connector-sdk/spec/fixtures/"
CONNECTOR_CORE = ("connector/lib/ipaas/connector/", "connector/lib/ipaas/job/", "connector/lib/ipaas/test_case/",
                  CONNECTOR_FIXTURES)
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
# Up to three leading spaces or tabs: `git merge --squash` indents the squashed commits' messages by four spaces,
# and the owner's own squash of Claude commits is the owner's commit, while a message typed with a small indent,
# spaces or a tab, is still Claude's.
CLAUDE_TRAILER_PATTERN = re.compile(r"^[ \t]{0,3}Co-authored-by:\s*Claude", re.MULTILINE | re.IGNORECASE)
GATE_EPOCH = "2026-09-06T00:00:00+00:00"

# Toolchain locations and budgets.
RBENV_SHIMS = HOME / ".rbenv/shims"
HOMEBREW_BIN = Path("/opt/homebrew/bin")
RSPEC_TIMEOUT_SECONDS = 900
VITEST_TIMEOUT_SECONDS = 600
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
# A reset falls back to the last fetched origin/main rather than wait longer on the network.
CHECKS_FETCH_TIMEOUT_SECONDS = 120

PROOF_DIRECTORY = Path(".claude/proof")
DEVIATIONS_PATH = Path(".claude/deviations")
BASE_CACHE_NAME = "base.json"
FIGMA_MISMATCH_FILE_NAME = "figma-compare.json"
LEGACY_MISMATCH_KEY = "first_mismatch_at"
COMMENTS_PATH = Path(".claude/comments")
COMMENT_FIX = "fix-in-this-pr"
COMMENT_SEPARATE = "separate-request"
COMMENT_REPLY = "follow-the-user's-words"
COMMENT_VERDICTS = (COMMENT_FIX, COMMENT_SEPARATE, COMMENT_REPLY)
# A decision given in chat is only checkable if the user's own words come with it.
EVIDENCE_MINIMUM_CHARACTERS = 12
TRIAGE_WRAPPER = ".claude/bin/comment-triage"
MARKS_READER = Path.home() / "personal/scripts/excalidraw-marks"
GITHUB_HOST = os.environ.get("GH_HOST", "git.4me.com")
# What the gate itself writes into the working tree. It is never part of the change under proof:
# counting it would move the diff digest and make a review or a proof stale the moment it is recorded.
GATE_STATE_PREFIXES = (str(PROOF_DIRECTORY), str(DEVIATIONS_PATH), str(COMMENTS_PATH))
DECLARATION_FILE_NAME = "declaration.json"
RUNS_DIRECTORY_NAME = "runs"
SURFACED_MARKER_NAME = ".surfaced"
PATCH_FILE_NAME = "patch.diff"
META_FILE_NAME = "meta.json"
FINDINGS_FILE_NAME = "findings.json"
UNITS_FILE_NAME = "units.json"
PROOF_NOT_JUDGED_AT = f"the run's {FINDINGS_FILE_NAME} under units.not_judged"
MEASURE_NOT_JUDGED_AT = f"{UNITS_FILE_NAME} under not_judged"
LOG_FILE_NAME = "prover.log"
REFERENCES_DIRECTORY_NAME = "references"
REVIEWS_DIRECTORY_NAME = "reviews"
LIVE_DIRECTORY_NAME = "live"
DEVIATIONS_DIRECTORY_NAME = "deviations"
# design and spec can excuse a measured difference when the user decided it. process cannot: it
# records a place where the method did not fit the work, so the next run of the method can be
# changed. It excuses nothing and lets nothing pass.
DEVIATION_KINDS = ("design", "spec", "process")
RECORD_ONLY_KINDS = ("process",)
# Only the user can decide that the build may differ from the design on purpose. A deviation Claude
# recorded is a note, and it never suppresses a mismatch.
DEVIATION_DECIDERS = ("user", "claude")

# How far an edge may move before the build stops matching the design, in CSS pixels.
# Per edge, not as a ratio: the same one pixel error scores 0.9986 on a 1428px card and
# 0.9535 on a 42px label, so one overlap threshold passes the card and fails the label.
FIGMA_TOLERANCE = int(os.environ.get("FIGMA_TOLERANCE", 2))
# A Figma text node is as tall as its line box; a DOM range is as tall as its ink. For 14px
# text that is 24 against 17 every time, on text that matches. Height is not compared for text.
FIGMA_TEXT_EDGES = ("left", "top", "width")
FIGMA_BOX_EDGES = ("left", "top", "width", "height")

# Three skills must look at every code change before it is handed off. The user ran them by hand and
# the sessions that forgot are the ones that shipped the avoidable findings, so the gate asks for them.
REQUIRED_REVIEWS = (
    ("simplification", "/agent-skills:code-simplify", "the change is as small as it can be"),
    ("edge-cases", "/edge-case-hunter", "the inputs and states nobody wrote a case for"),
    ("quality", "/agent-skills:code-review-and-quality", "correctness, readability, architecture, security, performance"),
)
REVIEW_SLUGS = tuple(slug for slug, _, _ in REQUIRED_REVIEWS)
# The plugin ships `code-simplify` as the command and `code-simplification` as the skill it invokes, and
# the edge case hunter is registered as `bmad-review-edge-case-hunter`.
REVIEW_SKILL_NAMES = {"simplification": ("code-simplification",), "edge-cases": ("bmad-review-edge-case-hunter",)}
REVIEW_ITEM_REQUIRED_KEYS = ("title", "file", "line", "verdict")
REVIEW_VERDICTS = ("fixed", "in-scope", "out-of-scope", "no-change-needed")

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
# A commit made while the scan could not run says so, and pre-push refuses it until an amend can say more.
VERDICT_NOT_COMPUTED = "not-computed"
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
# The owner's own words: every prompt the UserPromptSubmit hook sees and every answer given in a question
# dialog, kept in full under the fence. A decision that lets something through must quote words found
# here, typed after the refusal it answers. `--decided-by user` used to need only a non-empty string,
# and the session can run the command, so it could approve its own work.
OWNER_WORDS_DIRECTORY = SKIPS_LOG.parent / "owner-words"
# Decisions recorded before the store existed cannot be checked against it, and checking them would
# freeze every open pull request that carries one. Only later decisions are checked. This is the moment
# the store went live, not the day it was written: a decision recorded in between found no words. A machine
# that installs the gate later keeps its own moment in the epoch file, which grandfather_decisions.py writes.
OWNER_WORDS_EPOCH = "2026-10-19T07:32:00Z"
OWNER_WORDS_EPOCH_FILE = SKIPS_LOG.parent / "owner-words-epoch"
# The store holds what the owner typed, which can include a pasted secret. A decision older than this
# has to be given again.
OWNER_WORDS_KEEP_DAYS = 90
PRIVATE_FILE_MODE = 0o600
# Prompts that arrive without the owner typing them. phased sends four shapes (phased.py resume_brief and the three
# `line =` sites) and ticketd sends two (its brief line and its restart header), typed into a session window or
# passed as the launch prompt. Their briefs quote reviewers. Another agent's message arrives through the same door.
# An owner prompt that only mentions a brief path is kept.
MACHINE_PROMPT_SHAPES = re.compile(
    r"^(?:Phase \d+ of PR #\d+ is approved\. Start phase \d+: read @"
    r"|New review feedback on PR #\d+ \(phase \d+\): read @"
    r"|New feedback on PR #\d+ while phase \d+ is still open: read @"
    r"|# Restarting the session for \S+ PR #\d+, phase "
    r"|ticketd: request \d+, read @"
    r"|# Restarting the ticketd session for request \d+\b"
    r"|Another Claude session sent a message)")
# Pasted text arrives inside the prompt, and a pasted reviewer comment is the reviewer's words. The real
# closing tag carries the same id as the opening one (`</pasted_content id="65c5">`), and a paste the
# prompt cut off has no closing tag at all. Another agent's message and a task notification are the same.
QUOTED_BLOCKS = re.compile(r"<(pasted_content|agent-message|task-notification)\b[^>]*>.*?(?:</\1\b[^>]*>|\Z)",
                           re.DOTALL)
# An option label in a question dialog is text the session wrote. Selecting it is a choice, and it is
# stored, but it is not the owner's words and never satisfies an `--evidence` check. Claude Code writes the
# placeholder when the owner typed only a note.
OWNER_SOURCE_OPTION = "option"
NOTES_ONLY_ANSWER = "(notes only)"
OWNER_SOURCE_PROMPT = "prompt"
# A reply from the owner's phone arrives as a typed prompt that starts with the lead ticketd types:
# `phone Q2: `, `phone C3: `, `phone P1: ` or `phone ready: `. It is the owner's words only for the question it
# names, only in the worktree it was typed in, and never for a waiver. A decision that names no question takes
# only a `Q` reply. On a review comment, `phone C3: R:IN` or `R:OUT` is a tap: a choice, kept as the tap and
# never as words.
OWNER_SOURCE_PHONE = "phone"
PHONE_LEAD = re.compile(r"^phone (?P<question>[QCP]\d+|ready):(?: |$)")
PHONE_MARK = re.compile(r"^R\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)
# What an `R:IN` or `R:OUT` mark means, drawn on the diagram or tapped on the phone.
MARK_VERDICTS = {"IN": COMMENT_FIX, "OUT": COMMENT_SEPARATE}
# Where a sentence starts: after a line break, after a full stop, question or exclamation mark with a space
# after it (so `2.5` is one word), after a colon or a semicolon, after a dash between spaces, and at a list
# marker. An owner writes "C4: yes, fix it in this PR." and "- waive this one".
SENTENCE_BREAK = re.compile(r"\n\s*(?:[-*\u2022]|\d+[.)])\s+|\n|(?<=[.!?])\s+|[:;]\s+|\s[-\u2013\u2014]\s")
SMART_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "`": None})
# The lines in which the gate repeats what the owner said: comment-decide, review-section and skip-once. They
# are an echo of the owner, not the gate speaking, so they never make the owner's own words count as gate text.
ECHO_LINE = re.compile(r"^\s*(?:their words:|Evidence:)|Reason on record:|one-shot skip token used")
# A soft start after a piece that says no anywhere is no start: "Do not under any circumstances: merge it".
NEGATION = re.compile(r"\b(?:not|never|no|nope|nah|cannot|dont|cant|wont|shouldnt|didnt|doesnt|isnt|arent|"
                      r"wasnt|werent|wouldnt|couldnt|havent|hasnt|hadnt|aint)\b|\w*n't\b")
# The characters at either end of the evidence that do not count toward its minimum length: spaces, quotes and
# the marks . ! ? : ; , -. "yes.........." is three characters. The length check is stricter than comparison,
# which keeps "yes,,,,,,,,,," whole. A backtick never counts, because SMART_QUOTES removes it.
EVIDENCE_PADDING = " \"'.!?:;,-"
GRANDFATHERED_DECISIONS_FILE = SKIPS_LOG.parent / "grandfathered-decisions.json"
DECISION_DIGEST_LENGTH = 16
WAIVERS_DIRECTORY = SKIPS_LOG.parent / "waivers"
# Every gate call is recorded: what it was asked (the argument list and the hook payload on standard
# input) and what it answered (standard output, standard error, the exit code, the time it took). A hook
# that misfires leaves nothing in the session transcript, so without this the only evidence is what the
# model chose to repeat. The directory sits under the skips log, which the fence already protects.
TRACE_DIRECTORY = SKIPS_LOG.parent / "trace"
TRACE_MAX_TEXT = 4000
TRACE_KEEP_DAYS = 14
# Only the hook subcommands are given their standard input here. Everything else keeps the stream
# untouched, so a command the model runs from a shell can never block on a pipe that stays open.
TRACE_STDIN_COMMANDS = ("launch", "stop", "surface", "guard", "pre-push", "answers")

# The fence around the gate, applied to Claude's tools in every ipaas worktree by link_worktree():
# permission deny rules for what the rule syntax can express, and the guard hook for the rest.
PHASED_DIRECTORY = Path(__file__).resolve().parent.parent / "phased"
# The programs that write files. A redirect is judged by its target, not by the `>` sign, and an inline program
# by what its own text writes, so `cat <file> 2>/dev/null` and `json.load(open(...))` read.
WRITE_PROGRAMS = ("tee", "rm", "mv", "cp", "chmod", "truncate", "ln", "install", "dd", "touch", "mkdir", "rsync",
                  "unlink", "rmdir", "shred", "patch", "tar", "unzip", "ditto", "cpio")
IN_PLACE_EDITORS = ("sed", "gsed", "perl", "ruby")
AWK_PROGRAMS = ("awk", "gawk", "mawk", "nawk")
# perl and ruby flags that consume the rest of their single-dash cluster as a value, so an `i` after one is
# part of that value, not the in-place flag: `-rtime` is `require "time"`, not in-place.
PERL_VALUE_FLAGS = "eEMmIrxS"
DOWNLOAD_OUTPUT_FLAGS = {"curl": ("-o", "-O", "--output", "--remote-name", "--output-dir"),
                         "wget": ("-O", "-o", "-P", "--output-document", "--output-file", "--directory-prefix")}
GIT_WRITE_SUBCOMMANDS = ("checkout", "restore", "switch", "reset", "clean", "stash", "apply", "am", "mv", "rm")
GIT_VALUE_OPTIONS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
FIND_EXEC_ACTIONS = ("-exec", "-execdir", "-ok", "-okdir")
# The programs a `find -exec` block may run over a protected path: each only reads. A shell, an interpreter or
# any program not on this list is judged a write, because `-exec sh -c '...'` and `-exec rm` destroy the tree.
FIND_EXEC_READERS = ("cat", "head", "tail", "wc", "grep", "egrep", "rg", "ls", "stat", "jq", "yq", "diff", "file",
                     "find", "echo", "true", "test", "md5", "md5sum", "shasum", "sha256sum", "xxd", "od")
# What git leaves while a rebase replays commits it did not write, measured on git 2.50. A pick carries
# CHERRY_PICK_HEAD inside rebase-merge; `rebase --continue` after a conflict carries REBASE_HEAD without
# rebase-merge/amend; a reword runs `git commit` with GIT_REFLOG_ACTION `rebase (reword)`. A commit the user
# makes at an `edit` stop carries the amend file and one at a `break` stop carries neither, so both are new.
# A cherry-pick outside a rebase brings a commit into this branch, so it is new too, and `git rebase --quit`
# leaves REBASE_HEAD behind with no rebase directory, so nothing counts outside one.
MERGE_DIRECTORY = "rebase-merge"
APPLY_DIRECTORY = "rebase-apply"
REBASE_DIRECTORIES = (MERGE_DIRECTORY, APPLY_DIRECTORY)
PICK_MARKER = "CHERRY_PICK_HEAD"
CONFLICT_REPLAY_MARKER = "REBASE_HEAD"
EDIT_STOP_MARKER = f"{MERGE_DIRECTORY}/amend"
MERGE_MARKER = "MERGE_HEAD"
MERGE_MESSAGE_FILE = "MERGE_MSG"
SQUASH_MESSAGE_FILE = "SQUASH_MSG"
REBASE_REFLOG_ACTION = "rebase"
# The names whose presence says git replays, picks or merges a commit. The edit stop's amend file is not one:
# it says the user makes the commit, so replay_paths reads it beside these.
REPLAY_MARKERS = (*REBASE_DIRECTORIES, PICK_MARKER, CONFLICT_REPLAY_MARKER, MERGE_MARKER)
# Writing one of these by hand makes the commit hook read a new commit as git's replay or merge, which keeps the
# phase the message claims.
GIT_REPLAY_STATE = (*REPLAY_MARKERS, MERGE_MESSAGE_FILE, SQUASH_MESSAGE_FILE)
# `tmux send-keys` types into a session window, and what it types arrives as the owner's prompt.
GUARD_FORBIDDEN_ANYWHERE = ("--no-verify", "hooksPath", ".git/hooks", "skip-once", ".gate-skip-once", "GATE_SKIP",
                            "send-pack", "GIT_DIR=", "phased pause", "phased resume", "settings.local.json",
                            "launchctl", "send-keys")
# Reading the name is fine (`grep -n GIT_REFLOG_ACTION gate.py`); setting it is not, whatever the quoting:
# a bare `=`, a quoted or escaped one (`env VAR\=`, `VAR"=x"`), a `:=` default, or `os.environ['VAR']=` and
# `ENV["VAR"]=` from an inline program.
REFLOG_ACTION_SETTING = re.compile(r"GIT_REFLOG_ACTION['\"\]]*\s*\\?\+?:?=|"
                                   r"\b(?:export|declare|typeset|local|readonly|read|setenv)\s+(?:-\w+\s+)*"
                                   r"GIT_REFLOG_ACTION\b")
GUARD_WRITE_PROTECTED = (*(f"{PROOF_DIRECTORY}/{name}" for name in (RUNS_DIRECTORY_NAME, REFERENCES_DIRECTORY_NAME,
                                                                     REVIEWS_DIRECTORY_NAME, LIVE_DIRECTORY_NAME,
                                                                     BASE_CACHE_NAME, FIGMA_MISMATCH_FILE_NAME)),
                         str(DEVIATIONS_PATH), str(COMMENTS_PATH), "personal/scripts/gate", "personal/scripts/phased",
                         ".local/state/gate", ".local/state/phased")
# git's replay and merge files are matched only as the final segment of a path under a git directory, so
# `> .git/MERGE_MSG` is refused while `docs/rebase-merge-notes.md` and `~/.claude` are left alone.
GIT_STATE_SEGMENTS = GIT_REPLAY_STATE
GUARD_ALLOWED_COMMANDS = (".claude/bin/agent_task_finalize", ".claude/bin/pr-comment", ".claude/bin/pr-phase",
                          ".claude/bin/review-record", ".claude/bin/deviation", ".claude/bin/pr-diagram", ".claude/bin/pr-create",
                          ".claude/bin/comment-triage", ".claude/bin/live-record",
                          "phased handoff", "phased status", "phased logs", "phased adopt")
# The directories that hold a protected path: `git clean -fdx .claude/proof` and `rm -rf ~/.local` delete it,
# while `mkdir -p .claude/proof` and `touch` on one change nothing in it.
GUARD_PROTECTED_ANCESTORS = tuple(sorted({"/".join(path.split("/")[:count]) for path in GUARD_WRITE_PROTECTED
                                          for count in range(1, path.count("/") + 1)} - {""}))
GATE_WRAPPER_NAMES = tuple(sorted({Path(allowed.split()[0]).name for allowed in GUARD_ALLOWED_COMMANDS}))
SHELL_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
# Words that run the next word as the program, with how many arguments of their own they take first.
SEGMENT_WRAPPERS = {"env": 0, "nohup": 0, "command": 0, "exec": 0, "builtin": 0, "time": 0, "npx": 0, "timeout": 1,
                    "sudo": 0, "doas": 0, "nice": 0, "xargs": 0, "caffeinate": 0, "arch": 0, "stdbuf": 0}
# The options of a wrapper that take the next word as their value: `sudo -u root`, `xargs -n 1`.
WRAPPER_VALUE_OPTIONS = {"env": ("-u", "-C", "-P", "-S"), "timeout": ("-s", "-k", "--signal", "--kill-after"),
                         "sudo": ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"), "nice": ("-n",),
                         "xargs": ("-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s", "-d", "-E"),
                         "stdbuf": ("-i", "-o", "-e"), "exec": ("-a",), "caffeinate": ("-t", "-w"),
                         "doas": ("-u", "-g", "-C")}
# Words in front of the program that are the shell's own: `{ rm x; }`, `if true; then rm x; fi`, `! rm x`,
# `coproc rm x`, a `function name` or `name()` header, and a `case word in` with its pattern word.
SHELL_RESERVED_WORDS = ("{", "}", "!", "if", "then", "else", "elif", "do", "while", "until", "coproc", "in", "esac")
DIRECTORY_CHANGERS = ("cd", "pushd")
CLAUDE_PROGRAMS = ("claude", "claude-code")
PRINT_FLAGS = {"-p", "--print"}
# Posting a pull request comment goes through .claude/bin/pr-comment, which stamps the identity header and
# folds the content into a collapsed block. A raw call carries the account's name and nothing else, so a
# reader cannot tell it from a comment the account holder typed.
COMMENT_WRAPPER = ".claude/bin/pr-comment"
# The description is the record of the work, and a phase that writes the whole body rebuilds that record
# from memory: every section it does not retype is deleted, silently, including the user's own edits.
# `.claude/bin/pr-phase` reads the live description and splices in one phase section, so nothing else can
# be lost. Creating the pull request still writes a whole body, which is phase 1 and is allowed.
DESCRIPTION_WRAPPER = ".claude/bin/pr-phase"
REVIEW_WRAPPER = ".claude/bin/review-record"
CREATE_WRAPPER = ".claude/bin/pr-create"
GUARD_DESCRIPTION_WRITING = (
    re.compile(r"\bgh\s+pr\s+edit\b[^|;]*--body(-file)?\b"),
    # A body written at creation used to escape every check, which is how a description grew to
    # several hundred lines of prose before any rule could see it.
    re.compile(r"\bgh\s+pr\s+create\b[^|;]*--body(-file)?\b"),
    re.compile(r"\bgh\s+api\b[^|;]*\bpulls/\d+\b[^|;]*(-X\s*(PATCH|POST)|--method\s*(PATCH|POST))[^|;]*\bbody\b"),
)
GUARD_COMMENT_POSTING = (
    re.compile(r"\bgh\s+pr\s+comment\b"),
    re.compile(r"\bgh\s+pr\s+review\b"),
    re.compile(r"\bgh\s+pr-review\s+(reply|comment)\b"),
    re.compile(r"addPullRequestReviewThreadReply|addPullRequestReview\b|addComment\b|addPullRequestReviewComment"),
    re.compile(r"\bgh\s+api\b[^|;]*\b(issues|pulls)/\d+/comments"),
)
GUARD_ALLOWED_GATE_SUBCOMMANDS = ("setup", "references", "checks", "finalize", "pr-section", "link-worktree",
                                  "setup-checks", "rspec", "randomized-suite", "trace", "review-record",
                                  "review-section", "deviation", "design-links", "figma-compare", "live-record",
                                  "comment-fetch", "comment-decide", "comment-status", "waive", "units")
# An inline program can write anywhere, and a path can be built from pieces. One that names the state in the
# home directory, or imports the gate itself, is refused as a whole. One that names a worktree's proof state is
# refused when it writes: sessions read findings.json with `json.load` all day, 128 times in the trace. A
# script file it runs is out of a deny list's reach.
SHELL_NAMES = ("bash", "sh", "zsh", "dash", "ksh", "csh", "tcsh", "fish")
# Programs that read a heredoc as data. A heredoc fed to any other program is judged as commands, because a
# program the guard does not know may run it: `cat <<'EOF' | source /dev/stdin`.
# A heredoc fed to one of these is data. awk, sed, sqlite3, psql, mysql and curl are left out on purpose: each
# can run a command or write a file from what it reads (awk `system`, sed `w`, sqlite3 `.import`, curl `-K -`),
# so a heredoc fed to them is judged as commands.
DATA_READERS = ("cat", "tee", "git", "gh", "jq", "yq", "grep", "egrep", "rg", "head", "tail", "wc", "sort", "uniq",
                "base64", "pbcopy", "diff", "column", "xxd", "less", "tr", "cut", "gate.py")
INTERPRETERS = ("python", "python3", "ruby", "node", "perl", *SHELL_NAMES)
INTERPRETER_NAME = re.compile(rf"^(?:{'|'.join(INTERPRETERS)})(?:\.\d+)?$")
SHELL_COMMAND_FLAG = re.compile(r"^-[a-zA-Z]*c[a-zA-Z]*$")
# Python's short options that take a value, attached (`-Wignore`) or as the next word.
PYTHON_VALUE_FLAGS = "WX"
HOME_STATE_HINTS = (".local/state", "owner-words", "waivers", "unit-cache", ".gate-skip-once")
GATE_IMPORT = re.compile(r"\bimport\s+gate\b|\bfrom\s+gate\s+import\b|spec_from_file_location\([^)]*gate|"
                         r"sys\.path[^\n]*scripts/gate|runpy\.run_(?:path|module)\([^)]*gate|"
                         r"(?:import_module|__import__)\(\s*['\"]gate['\"]")
# Each redirect operator becomes a word of its own, so `echo {}>file` and `cmd &>file` redirect as surely as
# `echo {} > file`. `<<` and `<(` are read before this.
REDIRECT_OPERATOR = re.compile(r"&>>?|<>|>>|>\||>&|<&|>|<")
OUTPUT_REDIRECTS = (">", ">>", ">|", ">&", "&>", "&>>", "<>")
# Every redirect operator the scanner emits as its own word, so a redirect written before the program is
# dropped before the program is read. `<<` and `<<<` are handled in the scanner.
ALL_REDIRECTS = (*OUTPUT_REDIRECTS, "<", "<&")
MAX_GUARD_NESTING = 8
# What a substitution leaves in its segment: `f=$(ls * | head -1)` is one assignment, not a program named `*`.
SUBSTITUTION_PLACEHOLDER = "SUBSTITUTION"
PROGRAM_FLAGS = ("-c", "-e", "--eval")
# Writing to the console is printing: `json.dump(..., sys.stdout)` reads a findings file and shows it.
CONSOLE_STREAM_WRITES = re.compile(r"sys\.std(?:out|err)\.write\(|\$std(?:out|err)\.write")
JSON_DUMP_CALL = re.compile(r"json\.dump\(")
STANDARD_STREAM = re.compile(r"\s*sys\.std(?:out|err)\b")
# A Ruby File.open writes unless its mode is `r` or `rb`; one with no mode reads.
INLINE_WRITES = re.compile(r"""open\((?:[^()]|\([^()]*\))*,\s*(?:mode\s*=\s*)?['"](?:[wax]|r[bt]?\+)[bt+]*['"]|"""
                           r"""open\([^)]*,\s*['"]\+?>|\.write\(|write_(?:text|bytes)\(|json\.dump\(|"""
                           r"""os\.(?:replace|rename|remove|unlink|system|truncate)\(|shutil\.|subprocess\.|"""
                           r"""File\.(?:write|binwrite|delete|unlink|rename|truncate)\(|IO\.(?:binwrite|write)|"""
                           r"""FileUtils\.|File\.open\((?:[^()]|\([^()]*\))*,(?!\s*['"]rb?['"]\s*[,)])|"""
                           r"""(?:\.|\b)(?:writeFile|appendFile)(?:Sync)?\(|"""
                           r"""\.(?:unlink|rm|rename|copyFile|truncate)(?:Sync)?\(|`[^`]*>|"""
                           r"""\b(?:unlink|rename|truncate|system|syswrite)\s*[(\s"']|open\([^)]*,\s*['"]?[+]?>""")
GUARD_HOOK_MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit"
# Hooks every worktree must carry: the guard before a tool runs, and the store of the owner's answers
# after a question dialog.
FENCE_HOOKS = (("PreToolUse", GUARD_HOOK_MATCHER, "guard"), ("PostToolUse", "AskUserQuestion", "answers"))
ZERO_SHA = "0" * 40
ZERO_CONTEXT = 0
EMPTY_CHANGE_IDENTITY = "empty"
CHANGE_IDENTITY_LENGTH = 12
# Old records compare their stored raw digest with this one, so its length must never follow the identity's.
PATCH_DIGEST_LENGTH = 12
SHORT_SHA_LENGTH = 10
LISTED_FILES_LIMIT = 5
LISTED_EVIDENCE_LIMIT = 3
ERROR_REASON_MAX_CHARACTERS = 1000
LOG_TAIL_LINES = 8
# Every diff the gate parses is read with these, whatever the user's git configuration says: a quoted
# non-ASCII path, `diff.mnemonicPrefix` or `diff.noprefix` each turned every path into "", and rename
# detection made a moved file before `git add` look different from the same move once committed.
GATE_ATTRIBUTES_FILE = Path(__file__).resolve().parent / "units" / "gitattributes"
DIFF_CONFIGURATION = ("-c", "core.quotePath=false", "-c", f"core.attributesFile={GATE_ATTRIBUTES_FILE}")
DIFF_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")
PROCESS_MEMO = {}
PHASE_TRAILER_LINE = re.compile(rf"^{PHASE_TRAILER}:\s*(\d+)\s*$", re.MULTILINE)
# A skip reason that says nothing waives the push as surely as a real one. One skip in the log reads
# `<reason>`, copied from the gate's own hint, and it let 14 violations through.
SKIP_REASON_MINIMUM_CHARACTERS = 12
PLACEHOLDER_TEXT = re.compile(r"^\W*<[^>]*>\W*$")

STATUS_PENDING = "pending"
STATUS_PASS = "pass"
STATUS_VACUOUS = "vacuous"
STATUS_FAILS_WITH_CHANGE = "fails_with_change"
STATUS_AMBIGUOUS = "ambiguous_declaration"
STATUS_FLAKY = "flaky"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_DEFERRED = "deferred_system_tier"
STATUS_INTERRUPTED = "interrupted"
STATUS_ERROR = "error"
STATUS_SURVIVED = "survived"
STATUS_UNMEASURED = "unmeasured"
STATUS_SEVERITY = (
    STATUS_PASS,
    STATUS_NOT_APPLICABLE,
    STATUS_DEFERRED,
    STATUS_PENDING,
    STATUS_INTERRUPTED,
    STATUS_UNMEASURED,
    STATUS_FLAKY,
    STATUS_AMBIGUOUS,
    STATUS_FAILS_WITH_CHANGE,
    STATUS_SURVIVED,
    STATUS_VACUOUS,
    STATUS_ERROR,
)

# The per-unit proof. One changed unit at a time is removed in the gate worktree and the specs that ran
# it are run again; a unit whose removal leaves them all green is a survivor. Reviewers kept finding
# those by hand ("Deleting it leaves 317 examples green", PR 1111). It reports until it has been
# measured on real branches; only then does a survivor refuse the push.
UNIT_REFUSAL_ENABLED = False
# Measured on PR 1111: 705 units, a 13.5 minute baseline over 40 spec files, then about 50 seconds a unit, so
# about ten hours under the gate lock that every proof waits for. Until the owner decides where the stage
# belongs, it runs only on request, with `gate.py units --measure`.
UNIT_STAGE_IN_PROOF = False
UNITS_DIRECTORY = Path(__file__).resolve().parent / "units"
RUBY_UNIT_FINDER = UNITS_DIRECTORY / "ruby_units.rb"
SCRIPT_UNIT_FINDER = UNITS_DIRECTORY / "ts_units.js"
COVERAGE_HOOK = UNITS_DIRECTORY / "coverage_hook.rb"
UNIT_CACHE_FILE = HOME / ".local/state/gate/unit-cache.json"
UNIT_CACHE_KEEP_DAYS = 30
UNIT_RUBY_SUFFIXES = (".rb", ".rake")
# The vitest suite lives in the platform project, and the unit stage keys its spec files by runner.
SCRIPT_PROJECT = "vitest"
SCRIPT_PROJECT_DIRECTORY = "platform"
TYPESCRIPT_PACKAGE = Path(SCRIPT_PROJECT_DIRECTORY) / "node_modules/typescript"
MAX_UNIT_SPEC_FILES = 40
# The stage holds the gate lock, so the next proof waits for it. A unit not judged inside the budget is
# reported unmeasured. `units --measure` stops at the same budget unless `--budget` says otherwise; 0 is no limit.
UNIT_STAGE_BUDGET_SECONDS = 1200
UNIT_DIGEST_LENGTH = 12
BLOB_DIGEST_LENGTH = 16
UNIT_CACHE_KEY_LENGTH = 24
UNIT_TEXT_PREVIEW = 60
LINE_TEXT_PREVIEW = 120
LINE_END = 10 ** 9
UNIT_FINDER_TIMEOUT_SECONDS = 120
WAIVER_WORKTREE_KEY_LENGTH = 8
UNIT_KILLED = "killed"
UNIT_SURVIVED = "survived"
UNIT_UNEXECUTED = "unexecuted"
UNIT_UNMEASURED = "unmeasured"
UNIT_WAIVED = "waived"
UNIT_STATUSES = (UNIT_KILLED, UNIT_SURVIVED, UNIT_UNEXECUTED, UNIT_UNMEASURED, UNIT_WAIVED)
UNIT_REFUSING = (UNIT_SURVIVED, UNIT_UNEXECUTED)
# A changed line that holds only one of these produced no unit on purpose and says nothing on its own.
CLOSING_ONLY_LINE = re.compile(r"^\s*(?:end|</\w[\w.]*>|[\])};,/>]+)\s*$")


# ---------------------------------------------------------------- helpers
# A restart kills a running prover and a session holding the checks worktree, and both leave a marker
# that says "busy" behind them. The pair below tells a marker that is still owned from one that outlived
# its owner. The boot time settles a restart on its own: a process cannot have started before the boot
# it is supposedly running in. The process check catches a prover killed without a restart. Both are
# needed, because a process id is reused after a boot.

def boot_time():
    """Seconds since the epoch at which this machine booted, or 0 when it cannot be read."""
    try:
        output = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
    except OSError:
        return 0
    match = re.search(r"sec\s*=\s*(\d+)", output)
    return int(match.group(1)) if match else 0


def process_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def started_before_this_boot(started_at):
    boot = boot_time()
    if not boot or not started_at:
        return False
    try:
        return datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() < boot
    except ValueError:
        return False


def survived_a_restart(record):
    """The record was written before this boot, so whatever wrote it is gone. This is the only test the
    checks marker can use: the worktree is held across several gate calls, each a short process of its
    own, so no process id stays alive for the length of the hold."""
    if record.get("boot"):
        return record["boot"] != boot_time()
    return started_before_this_boot(record.get("at") or record.get("started_at"))


def owner_is_gone(record):
    """True when the process that wrote this record cannot still be running."""
    if started_before_this_boot(record.get("started_at") or record.get("at")):
        return True
    if record.get("boot") and record["boot"] != boot_time():
        return True
    if record.get("pid") is None:
        return False  # written before this check existed, and on this boot: assume it is alive
    return not process_alive(record["pid"])


def now_iso():
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def run(command, cwd=None, allow_exit_codes=(0,), env=None, timeout=None, input_text=None):
    """Output is decoded with surrogateescape: a Latin-1 fixture in a diff crashed launch, Stop and pre-push."""
    completed = subprocess.run(
        command, cwd=cwd, env=env, input=input_text, timeout=timeout, errors="surrogateescape",
        capture_output=True, text=True, stdin=None if input_text is not None else subprocess.DEVNULL,
    )
    if completed.returncode not in allow_exit_codes:
        raise RuntimeError(
            f"{' '.join(str(part) for part in command)} exited {completed.returncode}\n"
            f"{completed.stderr.strip()}"
        )
    return completed


def git(root, *arguments, allow_exit_codes=(0,), input_text=None, timeout=None):
    return run(["git", "-C", str(root), *arguments], allow_exit_codes=allow_exit_codes, input_text=input_text,
               timeout=timeout)


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


def write_json(path, payload, mode=None):
    write_atomically(path, json.dumps(payload, indent=2) + "\n", mode)


def write_atomically(path, text, mode=None):
    """Written beside the target and renamed over it, so a reader or a killed writer never leaves half a file.
    `mode` keeps a record that quotes the owner readable by the owner only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.partial")
    partial.write_text(text)
    if mode is not None:
        partial.chmod(mode)
    os.replace(partial, path)


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


def is_connector_fixture(path):
    """A shipped connector is source, even though it lives under connector-sdk/spec/fixtures."""
    return path.startswith(CONNECTOR_FIXTURES) and Path(path).suffix in (".rb", ".svg")


def is_spec_path(path):
    if is_connector_fixture(path):
        return False
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
    return [path for path in completed.stdout.split("\0")
            if path and not any(path.startswith(prefix) for prefix in GATE_STATE_PREFIXES)]


def memoized(key, compute):
    """A hook is one short process that asks the same git question several times, and the answer cannot
    change inside it. The Stop hook on PR 1111 called `diff_base` six times and built three snapshots.
    The memo lives as long as the process, so a caller that changes the working tree or the base and then
    asks again clears PROCESS_MEMO first, as refresh_base_cache does."""
    if key not in PROCESS_MEMO:
        PROCESS_MEMO[key] = compute()
    return PROCESS_MEMO[key]


def snapshot_patch(root, base=None, context_lines=None):
    """The change under proof: everything between the merge-base with the upstream branch and the working
    tree, committed work included. Measuring only the uncommitted diff made the same change look empty once
    it was committed, which reported a passing proof as stale and a fresh one as vacuous."""
    reference = base or diff_base(root)
    return memoized(("snapshot", str(root), reference, context_lines),
                    lambda: build_snapshot(root, reference, context_lines))


def snapshot_diff_arguments(context_lines):
    context = [f"-U{context_lines}"] if context_lines is not None else []
    return [*context, "--binary", *DIFF_FLAGS]


def gate_state_exclusions():
    return [f":(exclude){prefix}" for prefix in GATE_STATE_PREFIXES]


def git_diff(root, *arguments, **options):
    """Every diff the gate parses goes through here, so it reads the same whatever the user's configuration says."""
    return git(root, *DIFF_CONFIGURATION, "diff", *arguments, **options)


def changed_names(root, *arguments):
    """File names from a git command, separated by NUL: a quoted name (`"app/caf\\303\\251.rb"`) or one with a
    space slipped past every reader that split on whitespace, and a commit whose only code file had one skipped
    the proof check."""
    output = git(root, *DIFF_CONFIGURATION, *arguments, "-z").stdout
    return [name for name in output.split("\0") if name]


def build_snapshot(root, reference, context_lines):
    arguments = snapshot_diff_arguments(context_lines)
    pieces = [git_diff(root, reference, *arguments, "--", ".", *gate_state_exclusions()).stdout]
    for path in untracked_paths(root):
        pieces.append(git_diff(root, "--no-index", *arguments, "--", "/dev/null", path, allow_exit_codes=(0, 1)).stdout)
    return "".join(pieces)


def patch_blocks(patch):
    return [block for block in re.split(r"(?m)^(?=diff --git )", patch) if block.strip()]


def unquote_path(text):
    """git writes a path that holds a control character, a quote or a backslash as a C string."""
    return text.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")


def block_path(block):
    """With --no-renames both sides of the header name the same path, so the header's length says where
    the path ends, even when the path holds a space or ` b/`."""
    first = block.split("\n", 1)[0]
    sides = first[len("diff --git "):] if first.startswith("diff --git ") else ""
    quoted = re.match(r'"a/((?:[^"\\]|\\.)*)" "b/((?:[^"\\]|\\.)*)"$', sides)
    if quoted:
        return unquote_path(quoted.group(2))
    half = (len(sides) - 1) // 2
    old, new = sides[:half], sides[half + 1:]
    if old.startswith("a/") and new.startswith("b/") and old[2:] == new[2:]:
        return new[2:]
    renamed = re.match(r"a/.+? b/(.+)$", sides)
    return renamed.group(1) if renamed else ""


def split_patch(patch):
    spec_blocks, code_blocks, paths = [], [], []
    for block in patch_blocks(patch):
        path = block_path(block)
        paths.append(path)
        (spec_blocks if is_example_path(path) else code_blocks).append(block)
    return "".join(spec_blocks), "".join(code_blocks), paths


def patch_digest(patch):
    return hashlib.sha256(patch.encode("utf-8", "surrogateescape")).hexdigest()[:PATCH_DIGEST_LENGTH]


RUBY_DEFINITION = re.compile(r"^\s*(def|class|module)\s+((?:self\.)?[\w:]+[?!=]?)")
HUNK_HEADER = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@ ?(?P<context>.*)$")
# Documentation cannot change what the code does, and a note left untracked in a worktree is never
# committed, so counting it would call every proof stale at push time.
DOCUMENTATION_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc")


def definition_name(context):
    """The definition a hunk sits in, by name only: `def refresh_icon!(changed_by: nil)` reads `def refresh_icon!`,
    so an upstream edit to the parameters leaves a clean rebase clean."""
    named = RUBY_DEFINITION.match(context)
    return f"{named.group(1)} {named.group(2)}" if named else context


def patch_identity(patch):
    """What a change is, apart from where it sits. Every block but documentation counts, because the proof
    reverts YAML, migrations, stylesheets and lock files with the code. Each hunk keeps its lines byte for
    byte and the definition line git prints after its header, and loses its line numbers and the blob ids,
    which are what an upstream commit moves under a clean rebase. `git patch-id` was used before: it also
    dropped whitespace (`'a b'` and `'ab'` were one change) and the definition line (a guard moved to
    another class kept the id)."""
    kept = []
    # Sorted by path: the working tree lists untracked files after the tracked diff, and a commit lists every
    # file in path order, so a move made before `git add` would otherwise read as a different change.
    for block in sorted(patch_blocks(patch), key=block_path):
        if block_path(block).endswith(DOCUMENTATION_SUFFIXES):
            continue
        for line in block.split("\n"):
            if line.startswith("index "):
                continue
            header = HUNK_HEADER.match(line)
            kept.append(f"@@ {definition_name(header.group('context'))}" if header else line)
    if not kept:
        return EMPTY_CHANGE_IDENTITY
    return hashlib.sha256("\n".join(kept).encode("utf-8", "surrogateescape")).hexdigest()[:CHANGE_IDENTITY_LENGTH]


def change_identity(root, base=None):
    """What a proof, a review or a live record is about. Comparing raw diff digests called every proof
    stale after a rebase, and 21 refused pushes in the skip log were exactly that."""
    return memoized(("identity", str(root), base),
                    lambda: patch_identity(snapshot_patch(root, base, context_lines=ZERO_CONTEXT)))


def path_identities(patch):
    """One identity per changed file: what a proof saw of each file, so pre-push can judge the files a
    commit carries and leave alone the untracked pictures and scripts that sat in the worktree."""
    return {block_path(block): patch_identity(block) for block in patch_blocks(patch)
            if not block_path(block).endswith(DOCUMENTATION_SUFFIXES)}


def proof_base(root, sha, base_ref, base_sha):
    """Where the proof measured from, seen from one commit. The stored base sha stands in when the base branch
    is gone, as when a parent pull request was merged and its branch pruned."""
    if not (base_ref and reference_exists(root, base_ref)):
        ancestor = base_sha and git(root, "merge-base", "--is-ancestor", base_sha, sha, allow_exit_codes=(0, 1, 128))
        if ancestor and ancestor.returncode == 0:
            return base_sha
        base_ref = base_reference(root)
    return git(root, "merge-base", sha, base_ref, allow_exit_codes=(0, 1, 128)).stdout.strip() or None


def commit_delta(root, sha, base_ref=None, base_sha=None):
    """The branch delta at one commit, with nothing from the working tree. None when no base can be found,
    which the caller must refuse rather than skip."""
    base = proof_base(root, sha, base_ref, base_sha)
    if base is None:
        return None
    return git_diff(root, *snapshot_diff_arguments(ZERO_CONTEXT), base, sha, "--", ".", *gate_state_exclusions()).stdout


def measured_delta(root, sha, base_ref, base_sha, measure):
    """`measure` applied to the branch delta at one commit, or None when no base can be found."""
    delta = commit_delta(root, sha, base_ref, base_sha)
    return None if delta is None else measure(delta)


def commit_change_identity(root, sha, base_ref=None, base_sha=None):
    return measured_delta(root, sha, base_ref, base_sha, patch_identity)


def evidence_is_fresh(root, record):
    """One answer for proofs, reviews and live records. A record written before identities existed only
    carries the raw digest of the old snapshot, and is judged against a snapshot made the old way."""
    if record.get("change_id"):
        return record["change_id"] == change_identity(root)
    return record.get("patch_sha") == patch_digest(legacy_snapshot(root))


def legacy_snapshot(root):
    """The snapshot as the gate built it before identities: no fixed diff settings and no Ruby driver. Building
    it the new way changed every Ruby digest, which would have called every unfinished proof stale at deploy."""
    def compute():
        reference = diff_base(root)
        exclusions = [f":(exclude){PROOF_DIRECTORY}", f":(exclude){DEVIATIONS_PATH}"]
        pieces = [git(root, "diff", reference, "--binary", "--no-color", "--no-ext-diff", "--", ".",
                      *exclusions).stdout]
        for path in untracked_paths(root):
            pieces.append(git(root, "diff", "--no-index", "--binary", "--no-color", "--", "/dev/null", path,
                              allow_exit_codes=(0, 1)).stdout)
        return "".join(pieces)
    return memoized(("legacy_snapshot", str(root)), compute)


def evidence_stamp(root):
    return {"change_id": change_identity(root)}


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

    refresh_base_cache(root)
    base_sha = diff_base(root)
    patch = snapshot_patch(root, base_sha)
    spec_patch, code_patch, paths = split_patch(patch)
    zero_context = snapshot_patch(root, base_sha, context_lines=ZERO_CONTEXT)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{patch_digest(patch)}"
    run_directory = runs_directory(root) / run_id
    run_directory.mkdir(parents=True, exist_ok=True)
    (run_directory / PATCH_FILE_NAME).write_text(patch, errors="surrogateescape")
    write_json(run_directory / DECLARATION_FILE_NAME, declaration)
    write_json(run_directory / META_FILE_NAME, {
        "id": run_id, "source_root": str(root), "base_sha": base_sha, "base_ref": base_reference(root),
        "change_id": patch_identity(zero_context), "path_ids": path_identities(zero_context), "paths": paths,
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


def project_relative(path):
    """A path inside its project, the way a runner started in the project directory names it."""
    return path.split("/", 1)[1]


def rspec(project, spec_file, example, order, json_path, log):
    report, exit_code = rspec_files(project, [spec_file], json_path, log, order=order,
                                    extra=["-e", example] if example else [])
    summary = report.get("summary", {})
    return {
        "exit_code": exit_code,
        "example_count": summary.get("example_count", 0),
        "failure_count": summary.get("failure_count", 0),
        "errors_outside_examples": summary.get("errors_outside_of_examples_count", 0),
        "failed_examples": [
            {"description": each.get("full_description"), "message": (each.get("exception") or {}).get("message", "")[:400]}
            for each in report.get("examples", []) if each.get("status") == "failed"
        ],
    }


def is_javascript_test(path):
    """A vitest test file: under platform's javascript tree, named as a test, with a JS or TS suffix."""
    return path.startswith(JAVASCRIPT_PREFIX) and is_spec_path(path) and path.endswith(JAVASCRIPT_SUFFIXES)


def run_reported(command, project, json_path, log, environment=None, timeout=RSPEC_TIMEOUT_SECONDS):
    """Run one test command that writes a JSON report, and read the report. A report left by an earlier run
    must never stand in for this one, so it is deleted first; a run that writes none returns {}."""
    Path(json_path).unlink(missing_ok=True)
    log("$ " + " ".join(command))
    completed = subprocess.run(
        command, cwd=str(GATE_WORKTREE / project), env=environment or prover_environment(),
        capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-LOG_TAIL_LINES:])
    log(f"exit {completed.returncode}\n{tail}")
    return read_json(json_path, default={}) or {}, completed.returncode


def vitest_files(project, test_files, json_path, log, environment=None, extra=()):
    command = ["yarn", "vitest", "run", *test_files, "--reporter=json", f"--outputFile={json_path}", *extra]
    return run_reported(command, project, json_path, log, environment, VITEST_TIMEOUT_SECONDS)


def ran_in(suite):
    """The tests of one vitest suite that ran. `-t` reports every other test in the file as skipped."""
    return [each for each in (suite.get("assertionResults") or []) if each.get("status") in ("passed", "failed")]


def vitest_collection_errors(report):
    """A file that fails to load reports a failed suite in which no test ran. That is the analogue of
    rspec's errors outside examples, and it is the expected red for a change that adds a new export the
    test imports. Without it a load error would read as zero failures, so the red run would look vacuous."""
    return len([suite for suite in report.get("testResults") or []
                if suite.get("status") == "failed" and not ran_in(suite)])


def vitest(project, test_file, example, order, json_path, log):
    """The same contract as rspec(): run one file, optionally one example, and report the counts the
    proof logic reads. `order` is accepted for one call shape with rspec. vitest 4 takes
    `--sequence.shuffle` and `--sequence.seed`, but its report lists results in declared order either
    way, so a random order cannot be evidenced from the report; a `rand:` order runs the file as
    declared and the caller records that the run was not shuffled."""
    report, exit_code = vitest_files(project, [test_file], json_path, log, extra=["-t", example] if example else [])
    ran = [each for suite in report.get("testResults") or [] for each in ran_in(suite)]
    failed = [each for each in ran if each["status"] == "failed"]
    # The count of tests that actually ran is what the "exactly one example" rule reads, not numTotalTests.
    return {
        "exit_code": exit_code,
        "example_count": len(ran),
        "failure_count": len(failed),
        "errors_outside_examples": vitest_collection_errors(report),
        "failed_examples": [{"description": each.get("fullName"),
                             "message": (each.get("failureMessages") or [""])[0][:400]} for each in failed],
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

    relative_spec = project_relative(spec_file)
    javascript = is_javascript_test(spec_file)
    run_example = vitest if javascript else rspec
    record["runner"] = "vitest" if javascript else "rspec"
    select_flag = "-t" if javascript else "-e"
    json_directory = run_directory / f"{record['runner']}-{index}"
    json_directory.mkdir(exist_ok=True)

    log(f"--- proof {index}: {record['runner']} {spec_file} example {example!r}")
    reset_gate_worktree(base_sha)
    apply_patch(spec_patch, "spec hunks only", log)
    red = run_example(project, relative_spec, example, "defined", json_directory / "red.json", log)
    record["red"] = red
    if red["example_count"] != 1 and red["errors_outside_examples"] == 0:
        return {**record, "status": STATUS_AMBIGUOUS,
                "reason": f"{select_flag} matched {red['example_count']} examples; the declaration must select exactly one"}
    if not is_red(red):
        return {**record, "status": STATUS_VACUOUS,
                "reason": "the example passes with the change reverted, so it does not prove the change"}

    apply_patch(code_patch, "code hunks", log)
    green = run_example(project, relative_spec, example, "defined", json_directory / "green.json", log)
    record["green"] = green
    if green["example_count"] != 1:
        return {**record, "status": STATUS_AMBIGUOUS, "reason": f"{select_flag} matched {green['example_count']} examples with the change applied"}
    if not is_green(green):
        return {**record, "status": STATUS_FAILS_WITH_CHANGE, "reason": "the example fails with the change applied"}

    flake = []
    for seed in FLAKE_SEEDS:
        result = run_example(project, relative_spec, "", f"rand:{seed}", json_directory / f"flake-{seed}.json", log)
        flake.append({"seed": seed, "shuffled": not javascript, "green": is_green(result),
                      "failure_count": result["failure_count"], "failed_examples": result["failed_examples"]})
    record["flake"] = flake
    if not all(each["green"] for each in flake):
        return {**record, "status": STATUS_FLAKY,
                "reason": ("the test file is not green on every repeat" if javascript
                           else "the spec file is not green under every seed")}
    return {**record, "status": STATUS_PASS}


DELETED_FILE = re.compile(r"^deleted file mode ", re.MULTILINE)
EXAMPLE_DEFINITION = re.compile(r"""^([-+])\s*(?:it|test|specify|example)\s*\(?\s*(['"])(.+?)\2""")


def added_examples(spec_patch):
    """The example descriptions the change adds, per file.

    An example that the change adds is the one the change is supposed to prove. If it passes with
    the code reverted it proves nothing, and a file-wide check hides it: one vacuous example among
    forty working ones still leaves the file red.

    A description that the patch removes and adds was moved or reindented rather than written, so it is not
    treated as new. Without that, reindenting a block would demand that every example in it fail with the change
    reverted, and specs that are fine would read as vacuous. The pairing is per file, and across files only with a
    file the patch deletes: with --no-renames a moved spec file is a delete and an add, and its old examples are not
    new, while a short description such as "returns nil" removed from one file says nothing about another.

    A description built by interpolation is skipped, because it cannot be matched against the
    description the runner reports.
    """
    added, removed, deleted = {}, {}, set()
    for block in patch_blocks(spec_patch):
        path = block_path(block)
        gone = deleted if DELETED_FILE.search(block) else removed.setdefault(path, set())
        for line in block.split("\n"):
            match = EXAMPLE_DEFINITION.match(line)
            if not match or "#{" in match.group(3):
                continue
            if match.group(1) == "-":
                gone.add(match.group(3))
            else:
                added.setdefault(path, set()).add(match.group(3))
    return {path: descriptions - removed.get(path, set()) - deleted for path, descriptions in added.items()}


def rspec_files(project, spec_files, json_path, log, environment=None, extra=(), order="defined"):
    """One rspec invocation over several files. The sweep needs a per-example verdict across the
    whole set, not one run per file, so the red state is built once and read once."""
    command = ["bundle", "exec", "rspec", *spec_files, "--format", "json", "--out", str(json_path),
               "--order", order, *extra]
    return run_reported(command, project, json_path, log, environment)


def sweep_targets(paths, declared, log):
    """Every changed example file the declaration did not name. Fixtures and spec helpers are not
    example files, so they stay on the code side and get reverted with the rest of the change."""
    targets = []
    for path in paths:
        if not is_example_path(path) or path in declared:
            continue
        if is_system_spec(path):
            log(f"sweep: {path} skipped, system specs run in the CI tier")
            continue
        if project_of(path) is None:
            log(f"sweep: {path} skipped, not under {RUBY_PROJECTS}")
            continue
        targets.append(path)
    return targets


def sweep_undeclared(spec_patch, code_patch, base_sha, paths, declared, run_directory, log):
    """Run every changed example file that the declaration left out, with the code reverted.

    A file that is still green without the change proves nothing about the change. This is the same
    question prove_one asks of a declared example, asked of the files nobody declared.

    The revert is the whole code diff, so this finds a file that never depended on the change at
    all. It does not find a spec whose own logic is not load-bearing, nor one that rebuilds its
    expected value from the expression it is checking. Both stay green here and both are real.
    """
    targets = sweep_targets(paths, declared, log)
    if not targets:
        return []
    if not code_patch.strip():
        return [{"spec_file": path, "status": STATUS_NOT_APPLICABLE,
                 "reason": "the diff has no non-spec hunks to revert"} for path in targets]

    log(f"--- sweep: {len(targets)} changed example file(s) the declaration did not name")
    added = added_examples(spec_patch)
    log(f"sweep: the change adds {sum(len(each) for each in added.values())} named example(s)")
    reset_gate_worktree(base_sha)
    apply_patch(spec_patch, "spec hunks only", log)

    records = []
    by_project = {}

    for path in targets:
        if is_javascript_test(path):
            records.append(sweep_one_javascript(path, added, run_directory, log))
            continue
        if not (GATE_WORKTREE / path).exists():
            records.append({"spec_file": path, "status": STATUS_NOT_APPLICABLE,
                            "reason": "the change deletes this file, so there is nothing to run"})
            continue
        by_project.setdefault(project_of(path), []).append(path)

    for project, project_paths in sorted(by_project.items()):
        records += sweep_project(project, project_paths, added, run_directory, log)
    return records


def sweep_project(project, paths, added, run_directory, log):
    json_directory = run_directory / f"sweep-{project}"
    json_directory.mkdir(exist_ok=True)
    relative = [project_relative(path) for path in paths]
    report, exit_code = rspec_files(project, relative, json_directory / "red.json", log)

    if not report:
        return [{"spec_file": path, "status": STATUS_ERROR,
                 "reason": f"rspec wrote no report for the sweep (exit {exit_code})"} for path in paths]

    counts = {path: {"examples": 0, "failures": 0, "added": 0, "still_green": []} for path in paths}
    for example in report.get("examples", []):
        path = sweep_owner(example.get("file_path", ""), project, counts)
        if path is None:
            continue
        count = counts[path]
        count["examples"] += 1
        if example.get("status") == "failed":
            count["failures"] += 1
        description = matching_added(example.get("full_description", ""), added.get(path, set()))
        if description is None:
            continue
        count["added"] += 1
        if example.get("status") == "passed":
            count["still_green"].append(description)

    errors_outside = (report.get("summary") or {}).get("errors_outside_of_examples_count", 0)
    return [sweep_record(path, counts[path], errors_outside, len(paths)) for path in paths]


def matching_added(full_description, descriptions):
    """rspec reports the context chain and the description together, so the added description is a
    suffix of it. The longest match wins, so a description that is a tail of a longer one does not
    claim the longer one's result."""
    matches = [each for each in descriptions if full_description.endswith(each)]
    return max(matches, key=len) if matches else None


def sweep_owner(file_path, project, counts):
    """rspec reports a file path relative to the project directory, with a ./ prefix."""
    candidate = f"{project}/{file_path.lstrip('.').lstrip('/')}"
    return candidate if candidate in counts else None


def sweep_record(path, count, errors_outside, file_count):
    record = {"spec_file": path, "runner": "rspec", "red": count}
    if count["still_green"]:
        listed = "; ".join(f'"{each}"' for each in sorted(count["still_green"])[:5])
        return {**record, "status": STATUS_VACUOUS,
                "reason": f"{len(count['still_green'])} of the {count['added']} example(s) this change adds "
                          f"pass with the change reverted, so they prove nothing: {listed}"}
    if count["failures"] >= 1:
        return {**record, "status": STATUS_PASS}
    if count["examples"] == 0:
        if errors_outside >= 1:
            return {**record, "status": STATUS_PASS,
                    "reason": "the file does not load with the change reverted, so it depends on the change"}
        return {**record, "status": STATUS_ERROR,
                "reason": "rspec ran no example from this file and reported no load error"}
    if errors_outside >= 1 and file_count > 1:
        return {**record, "status": STATUS_ERROR,
                "reason": "another file in the sweep failed to load, so this result is not trustworthy"}
    return {**record, "status": STATUS_VACUOUS,
            "reason": f"all {count['examples']} example(s) pass with the change reverted, so the file does not prove the change"}


def sweep_one_javascript(path, added, run_directory, log):
    project = project_of(path)
    json_directory = run_directory / f"sweep-{Path(path).stem}"
    json_directory.mkdir(exist_ok=True)
    json_path = json_directory / "red.json"
    result = vitest(project, project_relative(path), "", "defined", json_path, log)
    still_green = javascript_still_green(read_json(json_path, default={}) or {}, added.get(path, set()))
    record = {"spec_file": path, "runner": "vitest", "red": result, "still_green": still_green}
    if still_green:
        listed = "; ".join(f'"{each}"' for each in sorted(still_green)[:5])
        return {**record, "status": STATUS_VACUOUS,
                "reason": f"{len(still_green)} of the test(s) this change adds pass with the change reverted, "
                          f"so they prove nothing: {listed}"}
    if is_red(result):
        return {**record, "status": STATUS_PASS}
    return {**record, "status": STATUS_VACUOUS,
            "reason": "every test in the file passes with the change reverted, so the file does not prove the change"}


def javascript_still_green(report, descriptions):
    green = []
    for suite in report.get("testResults") or []:
        for assertion in suite.get("assertionResults") or []:
            if assertion.get("status") != "passed":
                continue
            name = assertion.get("fullName") or assertion.get("title") or ""
            match = matching_added(name, descriptions)
            if match:
                green.append(match)
    return green


@contextlib.contextmanager
def gate_lock(log=None):
    """The gate worktree serves one run at a time: the prover, the unit measurement, ad-hoc rspec and the
    randomized suite all take this lock. The directory is made first, or the lock file cannot be opened."""
    GATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GATE_LOCK_FILE, "w") as lock_handle:
        if log:
            log("waiting for the gate worktree lock")
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        if log:
            log("lock acquired")
        yield


def file_logger(path):
    def log(text):
        with open(path, "a") as handle:
            handle.write(f"[{now_iso()}] {text}\n")
    return log


def prove(run_directory):
    run_directory = Path(run_directory)
    log = file_logger(run_directory / LOG_FILE_NAME)
    findings_path = run_directory / FINDINGS_FILE_NAME
    meta = read_json(run_directory / META_FILE_NAME) or {}
    declaration = read_json(run_directory / DECLARATION_FILE_NAME) or {}
    spec_patch, code_patch, _ = split_patch((run_directory / PATCH_FILE_NAME).read_text(errors="surrogateescape"))
    findings = {"id": meta.get("id"), "status": STATUS_PENDING, "base_sha": meta.get("base_sha"),
                "started_at": now_iso(), "proofs": [], "sweep": [], "pid": os.getpid(), "boot": boot_time()}
    write_json(findings_path, findings)
    with gate_lock(log):
        try:
            proof_status = run_declared_proofs(findings, declaration, spec_patch, code_patch, meta, run_directory, log)
        except Exception as error:  # the findings file must always reach a final state
            log(f"prover error: {error!r}")
            proof_status, findings["reason"] = STATUS_ERROR, str(error)[:ERROR_REASON_MAX_CHARACTERS]
        if not UNIT_REFUSAL_ENABLED:
            settle(findings, proof_status, findings_path, log)
        if proof_status == STATUS_ERROR or not UNIT_STAGE_IN_PROOF:
            reason = ("the proofs did not finish" if proof_status == STATUS_ERROR
                      else "the unit stage runs only with `gate.py units --measure` for now")
            findings["units"] = {"status": STATUS_NOT_APPLICABLE, "units": [], "reason": reason,
                                 "finished_at": now_iso()}
        else:
            findings["units"] = {"status": STATUS_PENDING}
            write_json(findings_path, findings)
            findings["units"] = unit_stage(spec_patch, code_patch, meta.get("base_sha"), meta.get("paths", []),
                                           run_directory, log, Path(meta.get("source_root") or run_directory))
        if UNIT_REFUSAL_ENABLED:
            settle(findings, worst_status([proof_status, findings["units"]["status"]]), findings_path, log)
        else:
            write_json(findings_path, findings)


def run_declared_proofs(findings, declaration, spec_patch, code_patch, meta, run_directory, log):
    for index, proof in enumerate(declaration.get("proofs", []), start=1):
        findings["proofs"].append(prove_one(proof, spec_patch, code_patch, meta["base_sha"], run_directory, index, log))
    declared = {each.get("spec_file") for each in declaration.get("proofs", [])}
    findings["sweep"] = sweep_undeclared(spec_patch, code_patch, meta["base_sha"], meta.get("paths", []), declared,
                                         run_directory, log)
    return worst_status([each["status"] for each in findings["proofs"] + findings["sweep"]])


def settle(findings, status, findings_path, log):
    """The one place a run gets its final status, its finish time and its notification."""
    findings["status"] = status
    findings["finished_at"] = now_iso()
    write_json(findings_path, findings)
    log(f"final status: {status}")
    notify("gate proof", f"{findings['id']}: {status}")


# ------------------------------------------------------------ unit proof

def hunk_lines(diff):
    """Each changed line of a diff as (kind, old line number, new line number, text), kind `+` or `-`.
    A line is a file header only before a block's first hunk, so an added `++i;` is a line, not `+++`."""
    old_line = new_line = None
    # Split on newlines only: str.splitlines also splits on U+2028 and \x0c, which a JavaScript line can hold.
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            old_line = new_line = None
            continue
        header = HUNK_HEADER.match(line)
        if header:
            old_line, new_line = int(header.group("old")), int(header.group("new"))
            continue
        if new_line is None or line.startswith("\\"):
            continue
        if line.startswith("+"):
            yield "+", old_line, new_line, line[1:]
            new_line += 1
        elif line.startswith("-"):
            yield "-", old_line, new_line, line[1:]
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1


def added_text(patch):
    """The lines each block adds, as {path: {new line number: text}}."""
    added = {}
    for block in patch_blocks(patch):
        path = block_path(block)
        for kind, _old, new, text in hunk_lines(block):
            if kind == "+":
                added.setdefault(path, {})[new] = text
    return added


def changed_files_with(paths, lines, suffixes):
    return [path for path in paths if is_code_path(path) and path.endswith(suffixes) and lines.get(path)]


def unit_files(paths, lines):
    """The changed files a unit finder reads, and the changed code files no finder reads, with the reason."""
    ruby = [path for path in changed_files_with(paths, lines, UNIT_RUBY_SUFFIXES) if project_of(path)]
    # A config file (vite.config.ts, eslint.config.js) is imported by no test, so all its units would read unexecuted.
    script = [path for path in changed_files_with(paths, lines, JAVASCRIPT_SUFFIXES)
              if path.startswith(JAVASCRIPT_PREFIX)]
    unread = []
    for path in changed_files_with(paths, lines, tuple(CODE_EXTENSIONS)):
        if path in ruby or path in script:
            continue
        unread.append({"path": path, "reason": unread_reason(path)})
    return ruby, script, unread


def unread_reason(path):
    if path.endswith(UNIT_RUBY_SUFFIXES):
        return f"outside {', '.join(RUBY_PROJECTS)}"
    if path.endswith(JAVASCRIPT_SUFFIXES):
        return f"outside {JAVASCRIPT_PREFIX}, where no test imports it"
    return f"no unit finder reads {Path(path).suffix} files"


def gate_relative(file):
    """A path inside the gate worktree, relative to it, or None for a sibling that only shares its prefix."""
    path = Path(file)
    return str(path.relative_to(GATE_WORKTREE)) if path.is_relative_to(GATE_WORKTREE) else None


def typescript_package():
    """The gate worktree links platform/node_modules from the main checkout; the main checkout's copy is
    the fallback when the link is missing."""
    for root in (GATE_WORKTREE, MAIN_REPOSITORY):
        if (root / TYPESCRIPT_PACKAGE).is_dir():
            return root / TYPESCRIPT_PACKAGE
    return GATE_WORKTREE / TYPESCRIPT_PACKAGE


def run_unit_finder(command, paths, lines):
    if not paths:
        return {"units": [], "unparsed": []}
    request = {"files": [{"path": str(GATE_WORKTREE / path), "added_lines": lines[path]} for path in paths]}
    done = subprocess.run(command, cwd=str(GATE_WORKTREE / SCRIPT_PROJECT_DIRECTORY), env=prover_environment(),
                          input=json.dumps(request), capture_output=True, text=True,
                          timeout=UNIT_FINDER_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise RuntimeError(f"unit finder failed: {done.stderr.strip()[-ERROR_REASON_MAX_CHARACTERS:]}")
    found = json.loads(done.stdout or "{}")
    for unit in found.get("units", []):
        unit["path"] = gate_relative(unit["path"])
    return {"units": found.get("units", []), "unparsed": [gate_relative(each) for each in found.get("unparsed", [])]}


def number_units(units):
    """Two identical units in one file, `return if x.nil?` in two methods, are two units. Each is named by the
    definition it sits in and its place among the units of the same text there, so they get two digests, two
    cache entries and two waivers, and a unit added in another method changes neither."""
    seen = {}
    for unit in sorted(units, key=lambda each: (each["path"], each["start"])):
        identity = (unit["path"], unit.get("scope", ""), unit["kind"], unit["original"], unit["replacement"])
        seen[identity] = seen.get(identity, 0) + 1
        text = "\0".join([*identity, str(seen[identity])])
        unit["digest"] = hashlib.sha256(text.encode()).hexdigest()[:UNIT_DIGEST_LENGTH]
    return units


def find_changed_units(ruby_files, script_files, lines):
    ruby = run_unit_finder(["ruby", str(RUBY_UNIT_FINDER)], ruby_files, lines)
    script = run_unit_finder(["node", str(SCRIPT_UNIT_FINDER), str(typescript_package())], script_files, lines)
    unparsed = [{"path": path, "reason": "the file does not parse, or is not valid UTF-8"}
                for path in ruby["unparsed"] + script["unparsed"]]
    return number_units(ruby["units"] + script["units"]), unparsed


def unjudged_lines(added, units, files):
    """Changed lines that no unit covers: a constant, an assignment, a link of a method chain, a declaration.
    They are listed, not judged, so a pass says what it did not look at."""
    covered = {}
    for unit in units:
        covered.setdefault(unit["path"], set()).update(range(unit["start_line"], unit["end_line"] + 1))
    rows = []
    for path in files:
        for number, text in sorted(added.get(path, {}).items()):
            if number in covered.get(path, set()) or is_comment_line(path, text) or CLOSING_ONLY_LINE.match(text):
                continue
            rows.append({"path": path, "line": number, "reason": "no unit", "text": text.strip()[:LINE_TEXT_PREVIEW]})
    return rows


def defined_names(path):
    if not (GATE_WORKTREE / path).is_file():
        return set()
    text = (GATE_WORKTREE / path).read_text(errors="ignore")
    if path.endswith(UNIT_RUBY_SUFFIXES):
        return set(re.findall(r"^\s*(?:class|module)\s+(?:[A-Z]\w*::)*([A-Z]\w*)", text, re.MULTILINE))
    return {Path(path).name.split(".")[0]}


def naming_scores(directory, names, pattern):
    """Spec files that mention the change's names, scored by how rare each name is. Name matching alone
    found one spec file for the presenter whose `icon_url:` line 317 examples ran across four; a plain
    alphabetical cut of every file that mentions `Solution` kept 40 arbitrary files and none of them
    loaded the presenter."""
    scores = {}
    if not directory.is_dir():
        return scores
    for name in sorted(names):
        done = subprocess.run(["rg", "-l", "-w", "--glob", pattern, "-e", name, str(directory)],
                              capture_output=True, text=True)
        hits = [line for line in done.stdout.splitlines() if line]
        for hit in hits:
            relative = str(Path(hit).relative_to(GATE_WORKTREE))
            scores[relative] = scores.get(relative, 0) + 1 / len(hits)
    return scores


def unit_spec_candidates(paths, ruby_files, script_files):
    """For each project, the example files that can run a changed unit. The changed example files and
    the ones named after a changed file are always kept; the files that name a class the change defines
    fill the rest of the budget, rarest names first. System specs never run here."""
    kept, scored = {}, {}
    for path in paths:
        # A fixture under __tests__ (`xproc/__tests__/grammar/function.txt`) is not a test file.
        ruby_spec = path.endswith("_spec.rb")
        if (ruby_spec or is_javascript_test(path)) and not is_system_spec(path) and (GATE_WORKTREE / path).exists():
            kept.setdefault(project_of(path) if ruby_spec else SCRIPT_PROJECT, set()).add(path)
    for project, files in specs_for(GATE_WORKTREE, ruby_files).items():
        kept.setdefault(project, set()).update(
            spec for spec in (f"{project}/{each}" for each in files) if not is_system_spec(spec))
    names_by_project = {}
    for path in ruby_files:
        names_by_project.setdefault(project_of(path), set()).update(defined_names(path))
    for project, names in names_by_project.items():
        found = naming_scores(GATE_WORKTREE / project / "spec", names, "*_spec.rb")
        scored[project] = {hit: score for hit, score in found.items() if not is_system_spec(hit)}
    if script_files:
        kept.setdefault(SCRIPT_PROJECT, set()).update(
            f"{SCRIPT_PROJECT_DIRECTORY}/{each}" for each in javascript_tests_for(GATE_WORKTREE, script_files))
        names = set().union(*(defined_names(path) for path in script_files))
        scored[SCRIPT_PROJECT] = naming_scores(GATE_WORKTREE / JAVASCRIPT_PREFIX.rstrip("/"), names, "*.test.*")
    candidates = {}
    for project in set(kept) | set(scored):
        chosen = sorted(kept.get(project, set()))
        ranked = sorted((hit for hit in scored.get(project, {}) if hit not in chosen),
                        key=lambda hit: (-scored[project][hit], hit))
        candidates[project] = chosen + ranked[:max(0, MAX_UNIT_SPEC_FILES - len(chosen))]
    return {project: files for project, files in candidates.items() if project and files}


def unit_environment():
    environment = prover_environment()
    # A same-size edit written in the same second as the green file reaches bootsnap's compile cache
    # with an unchanged mtime and size, and loads the cached green code (bootsnap 1.26, bootsnap.rb:194).
    environment["DISABLE_BOOTSNAP_COMPILE_CACHE"] = "1"
    environment["GATE_COVERAGE_ROOT"] = str(GATE_WORKTREE)
    return environment


class FunctionSpan(NamedTuple):
    """One function of a vitest coverage report, with its columns, because two callbacks can share a line."""
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    count: int


@dataclass
class UnitRun:
    """What every step of one unit stage reads: the green state, the runner's environment, where its reports
    go, the log, the cache and its key context, the green runs already made, and when the stage must stop."""
    baseline: dict
    environment: dict
    directory: Path
    log: object
    context: dict
    cache: dict
    deadline: float = None
    green_runs: dict = field(default_factory=dict)

    def out_of_time(self):
        return self.deadline is not None and time.time() > self.deadline


def run_outcome(reported, passed=(), failed=(), errors_outside=0, coverage=None):
    return {"reported": reported, "passed": set(passed), "failed": set(failed), "errors_outside": errors_outside,
            "coverage": coverage}


def ruby_outcome(report, coverage=None):
    examples = report.get("examples", [])
    return run_outcome(bool(report), (each["id"] for each in examples if each.get("status") == "passed"),
                       (each["id"] for each in examples if each.get("status") == "failed"),
                       (report.get("summary") or {}).get("errors_outside_of_examples_count", 0), coverage)


def script_outcome(report, coverage=None):
    results = [(f"{suite.get('name')}::{each.get('fullName')}", each["status"])
               for suite in report.get("testResults") or [] for each in ran_in(suite)]
    return run_outcome(bool(report), (name for name, status in results if status == "passed"),
                       (name for name, status in results if status == "failed"), vitest_collection_errors(report),
                       coverage)


def run_unit_specs(project, files, json_path, environment, log, with_coverage=False, fail_fast=False):
    """One run of the unit stage, in the project its files belong to. With coverage, the coverage is written
    beside the report and read back into the outcome; an old one is deleted first, so a run that writes none
    reads none. vitest writes no coverage for a run with a failing test unless it is told to."""
    relative = [project_relative(each) for each in files]
    coverage_path = Path(f"{Path(json_path).with_suffix('')}-coverage")
    if project == SCRIPT_PROJECT:
        extra = ["--bail=1"] if fail_fast else []
        if with_coverage:
            shutil.rmtree(coverage_path, ignore_errors=True)
            extra += ["--coverage.enabled=true", "--coverage.provider=v8", "--coverage.reporter=json",
                      "--coverage.reportOnFailure=true", f"--coverage.reportsDirectory={coverage_path}"]
        report, _ = vitest_files(SCRIPT_PROJECT_DIRECTORY, relative, json_path, log, environment, extra)
        return script_outcome(report, script_coverage(coverage_path) if with_coverage else None)
    extra = ["--fail-fast"] if fail_fast else []
    if with_coverage:
        coverage_path.unlink(missing_ok=True)
        extra += ["-r", str(COVERAGE_HOOK)]
        environment = {**environment, "GATE_COVERAGE_OUT": str(coverage_path)}
    report, _ = rspec_files(project, relative, json_path, log, environment, extra)
    return ruby_outcome(report, ruby_coverage(coverage_path) if with_coverage else None)


def ruby_coverage(path):
    """Line counts as one-line spans, so Ruby and vitest coverage are read by one rule. A line Ruby does
    not count (nil) is no span; a line it counts and never ran is a span with count 0. None when the run
    wrote no coverage at all."""
    counts = read_json(path)
    if counts is None:
        return None
    statements = {}
    for file, lines in counts.items():
        relative = gate_relative(file)
        if relative and lines:
            statements[relative] = [(number + 1, number + 1, count)
                                    for number, count in enumerate(lines) if count is not None]
    return {"statements": statements, "functions": {}}


def script_coverage(directory):
    """Statement spans by line, and function spans with their columns, because two callbacks can share a line."""
    report = read_json(Path(directory) / "coverage-final.json")
    if report is None:
        return None
    statements, functions = {}, {}
    for file, entry in report.items():
        relative = gate_relative(file)
        if relative is None:
            continue
        counts, calls = entry.get("s") or {}, entry.get("f") or {}
        statements[relative] = [(span["start"]["line"], span["end"]["line"], counts.get(key, 0))
                                for key, span in (entry.get("statementMap") or {}).items()]
        functions[relative] = [FunctionSpan(span["loc"]["start"]["line"], span["loc"]["start"].get("column") or 0,
                                            span["loc"]["end"]["line"], end_column(span["loc"]["end"]),
                                            calls.get(key, 0))
                               for key, span in (entry.get("fnMap") or {}).items()]
    return {"statements": statements, "functions": functions}


def end_column(position):
    """vitest 4.1.11's converter writes an end column of Infinity, which JSON stores as null, when nothing maps
    after a function's closing brace: it means "to the end of the line". A column of 0 is a column."""
    column = position.get("column")
    return LINE_END if column is None else column


def unit_baseline(candidates, run):
    """The green state: which examples pass, and which lines each spec file ran. A file red on green judges
    nothing, but its coverage is kept, so a unit only it ran is reported unmeasured rather than unexecuted. A
    run that writes no report or no coverage, or times out, is the runner failing, an error of the stage. Files
    left when the budget runs out are listed, so no unit is called unexecuted for want of them."""
    baseline = {"files": {}, "red": {}, "runner_errors": [], "unrun": [], "unloaded": []}
    for project, files in sorted(candidates.items()):
        for index, spec in enumerate(files):
            if run.out_of_time():
                baseline["unrun"].append(spec)
                continue
            try:
                outcome = run_unit_specs(project, [spec], run.directory / f"baseline-{project}-{index}.json",
                                         run.environment, run.log, with_coverage=True)
            except subprocess.TimeoutExpired:
                outcome = run_outcome(False)
            # rspec stops before its suite hooks when a file fails to load, so a load error writes no coverage:
            # that file is red, not the runner broken.
            loaded = outcome["coverage"] is not None or outcome["errors_outside"]
            if not outcome["reported"] or not loaded:
                baseline["runner_errors"].append(spec)
                run.log(f"units: {spec} wrote no report or no coverage; the runner failed, not the spec")
                continue
            if outcome["coverage"] is None:
                baseline["unloaded"].append(spec)
            coverage = outcome["coverage"] or {"statements": {}, "functions": {}}
            entry = {"project": project, "passed": outcome["passed"], "coverage": coverage}
            if outcome["failed"] or outcome["errors_outside"] or not outcome["passed"]:
                baseline["red"][spec] = entry
                run.log(f"units: {spec} is not green with the change applied; it judges nothing")
                continue
            baseline["files"][spec] = entry
    return baseline


def innermost_function(functions, unit):
    """The smallest function span around the unit, compared by line and column: `a.map(x => x.y).filter(z => z
    || w)` holds two callbacks on one line."""
    start = (unit["start_line"], unit.get("start_column", 0))
    end = (unit["end_line"], unit.get("end_column", 0))
    around = [span for span in functions
              if (span.start_line, span.start_column) <= start and (span.end_line, span.end_column) >= end]
    return min(around, key=lambda span: (span.end_line - span.start_line, span.end_column - span.start_column),
               default=None)


def unit_ran(unit, coverage):
    """Whether one spec file's run executed the unit: a counted statement starts on its lines. A vitest
    statement can span a whole component (DropdownField.tsx 150 to 234), so a test that rendered the
    component would also "run" a callback inside it that nobody called; the innermost function around the
    unit has to have run as well."""
    first, last = unit["anchor_line"], unit["end_line"]
    function = innermost_function(coverage["functions"].get(unit["path"], []), unit)
    if function and not function.count:
        return False
    return any(count and first <= start <= last for start, _end, count in coverage["statements"].get(unit["path"], []))


def covering_specs(unit, entries):
    return sorted(spec for spec, entry in entries.items() if unit_ran(unit, entry["coverage"]))


def blob_digest(path):
    return hashlib.sha256((GATE_WORKTREE / path).read_bytes()).hexdigest()[:BLOB_DIGEST_LENGTH]


def unit_cache_key(unit, covering, context):
    """A verdict holds while the base, the whole change and every spec file that judged it stay the same.
    Support files, factories and collaborators are part of the change; the base carries everything else."""
    parts = [context["base"], context["change"], unit["path"], unit["digest"]]
    parts += [f"{spec}:{blob_digest(spec)}" for spec in covering]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:UNIT_CACHE_KEY_LENGTH]


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(TIMESTAMP_FORMAT)


def read_unit_cache():
    cutoff = days_ago(UNIT_CACHE_KEEP_DAYS)
    cache = read_json(UNIT_CACHE_FILE, default={}) or {}
    return {key: value for key, value in cache.items() if isinstance(value, dict) and value.get("at", "") >= cutoff}


def green_run(project, files, run):
    """The same spec files run together with the unit in place. A kill is read against this, so an example
    that fails only beside another file is not taken for a kill. One file was already run alone."""
    if len(files) == 1:
        return run_outcome(True, run.baseline["files"][files[0]]["passed"])
    key = (project, tuple(files))
    if key not in run.green_runs:
        json_path = run.directory / f"green-{len(run.green_runs)}.json"
        run.green_runs[key] = run_unit_specs(project, files, json_path, run.environment, run.log)
    return run.green_runs[key]


def unit_verdict(status, evidence, runner_error=False):
    return {"status": status, "evidence": evidence, "runner_error": runner_error}


def judge_unit(unit, covering, run):
    """Remove the unit, run the specs that ran it, put it back and check it is back byte for byte. Each
    project's specs run in their own project: connector/lib is loaded by platform and by connector-sdk."""
    groups = {}
    for spec in covering:
        groups.setdefault(run.baseline["files"][spec]["project"], []).append(spec)
    green = {project: green_run(project, files, run) for project, files in groups.items()}
    for project, outcome in green.items():
        if not outcome["reported"]:
            return unit_verdict(UNIT_UNMEASURED, ["the runner wrote no report for the green run"], runner_error=True)
        if outcome["failed"] or outcome["errors_outside"]:
            listed = ", ".join(groups[project][:LISTED_EVIDENCE_LIMIT])
            return unit_verdict(UNIT_UNMEASURED, [f"its spec files are not green when run together: {listed}"])
    target = GATE_WORKTREE / unit["path"]
    original = target.read_bytes()
    target.write_bytes(original[:unit["start"]] + unit["replacement"].encode() + original[unit["end"]:])
    try:
        removed = {project: run_unit_specs(project, files, run.directory / f"unit-{unit['digest']}-{project}.json",
                                           run.environment, run.log, fail_fast=True)
                   for project, files in groups.items()}
    finally:
        target.write_bytes(original)
    if target.read_bytes() != original:
        raise RuntimeError(f"{unit['path']} did not come back byte for byte after its unit was removed")
    return judgement(green, removed)


def judgement(green, removed):
    killed = sorted(set().union(*(removed[project]["failed"] & green[project]["passed"] for project in removed)))
    if killed:
        return unit_verdict(UNIT_KILLED, killed[:LISTED_EVIDENCE_LIMIT])
    if not all(each["reported"] for each in removed.values()):
        return unit_verdict(UNIT_UNMEASURED, ["the runner wrote no report with the unit removed"], runner_error=True)
    if any(each["errors_outside"] for each in removed.values()):
        return unit_verdict(UNIT_UNMEASURED, ["the specs did not load with the unit removed, which says the file is "
                                              "needed, not the unit"])
    if any(green[project]["passed"] - removed[project]["passed"] for project in removed):
        return unit_verdict(UNIT_UNMEASURED, ["some examples that pass with the unit did not run without it"])
    return unit_verdict(UNIT_SURVIVED, [])


def unit_record(unit, covering, status, evidence, cached=False):
    record = {key: unit[key] for key in ("path", "kind", "start_line", "end_line", "original", "replacement", "digest")}
    record.update({"covering": covering, "status": status, "evidence": evidence})
    if cached:
        record["cached"] = True
    return record


def judged_record(unit, run):
    """One unit's record, and whether the runner failed while judging it. A cached verdict is used even after
    the budget ran out; only a unit that needs a run is cut off."""
    covering = covering_specs(unit, run.baseline["files"])
    if not covering:
        return unjudged_record(unit, run.baseline), False
    key = unit_cache_key(unit, covering, run.context)
    if key in run.cache:
        return unit_record(unit, covering, run.cache[key]["status"], run.cache[key]["evidence"], cached=True), False
    if run.out_of_time():
        return unit_record(unit, covering, UNIT_UNMEASURED, ["the stage used its time budget before this unit"]), False
    try:
        result = judge_unit(unit, covering, run)
    except subprocess.TimeoutExpired as error:
        return unit_record(unit, covering, UNIT_UNMEASURED, [f"a spec run timed out after {error.timeout}s"]), False
    except (subprocess.SubprocessError, OSError) as error:
        return unit_record(unit, covering, UNIT_UNMEASURED, [f"the runner failed: {error}"]), True
    if not result["runner_error"]:
        run.cache[key] = {"status": result["status"], "evidence": result["evidence"], "at": now_iso()}
        write_json(UNIT_CACHE_FILE, run.cache)
    return unit_record(unit, covering, result["status"], result["evidence"]), result["runner_error"]


def unjudged_record(unit, baseline):
    """A unit no green spec file ran: unmeasured when a red file ran it, the budget left files unrun or a
    candidate spec file failed to load, and unexecuted only when every candidate ran and none touched it."""
    red = covering_specs(unit, baseline["red"])
    if red:
        listed = ", ".join(red[:LISTED_EVIDENCE_LIMIT])
        return unit_record(unit, red, UNIT_UNMEASURED,
                           [f"only spec files red with the change applied ran it: {listed}"])
    if baseline["unrun"]:
        return unit_record(unit, [], UNIT_UNMEASURED, ["the time budget ran out before every candidate spec file ran"])
    if baseline["unloaded"]:
        listed = ", ".join(baseline["unloaded"][:LISTED_EVIDENCE_LIMIT])
        return unit_record(unit, [], UNIT_UNMEASURED,
                           [f"a candidate spec file failed to load, so what it would run is unknown: {listed}"])
    return unit_record(unit, [], UNIT_UNEXECUTED, ["no candidate spec file ran it"])


def unit_stage(spec_patch, code_patch, base_sha, paths, run_directory, log, source_root,
               budget=UNIT_STAGE_BUDGET_SECONDS):
    """The per-unit proof. It never raises: an error in it is its own result, next to the proofs."""
    try:
        result = prove_units(spec_patch, code_patch, base_sha, paths, run_directory, log, source_root, budget)
    except Exception as error:
        log(f"units: error {error!r}")
        result = {"status": STATUS_ERROR, "units": [], "reason": str(error)[:ERROR_REASON_MAX_CHARACTERS]}
    result["finished_at"] = now_iso()
    return result


def unit_order(unit):
    return unit["path"].endswith(JAVASCRIPT_SUFFIXES), unit["path"], unit["start"]


def prove_units(spec_patch, code_patch, base_sha, paths, run_directory, log, source_root, budget):
    started = time.time()
    added = added_text(code_patch)
    lines = {path: sorted(numbers) for path, numbers in added.items()}
    ruby_files, script_files, unread = unit_files(paths, lines)
    if not ruby_files and not script_files:
        return {"status": STATUS_NOT_APPLICABLE, "units": [], "not_judged": unread,
                "reason": "no changed Ruby or TypeScript lines"}
    directory = run_directory / "units"
    directory.mkdir(exist_ok=True)
    log(f"--- units: {len(ruby_files)} Ruby and {len(script_files)} script file(s) with added lines")
    reset_gate_worktree(base_sha)
    apply_patch(spec_patch, "spec hunks", log)
    apply_patch(code_patch, "code hunks", log)
    units, unparsed = find_changed_units(ruby_files, script_files, lines)
    not_judged = unread + unparsed + unjudged_lines(added, units, ruby_files + script_files)
    candidates = unit_spec_candidates(paths, ruby_files, script_files)
    write_json(directory / "candidates.json", candidates)
    log(f"units: {len(units)} unit(s); candidate spec files {sum(len(each) for each in candidates.values())}")
    run = UnitRun(baseline={}, environment=unit_environment(), directory=directory, log=log,
                  context={"base": base_sha, "change": patch_digest(code_patch)}, cache=read_unit_cache(),
                  deadline=started + budget if budget else None)
    run.baseline = unit_baseline(candidates, run)
    records = []
    runner_errors = list(run.baseline["runner_errors"])
    for unit in sorted(units, key=unit_order):
        record, runner_error = judged_record(unit, run)
        if runner_error:
            runner_errors.append(record["path"])
        if record["status"] in UNIT_REFUSING and waiver_for(source_root, record["digest"]):
            record["status"] = UNIT_WAIVED
        log(f"units: {record['status']:10} {record['path']}:{record['start_line']} {record['kind']} "
            f"{record['original'][:UNIT_TEXT_PREVIEW]!r}")
        records.append(record)
    return unit_summary(records, run.baseline, candidates, not_judged, runner_errors, started)


def refusing_units(records):
    return [each for each in records if each["status"] in UNIT_REFUSING]


def unit_stage_status(records, runner_errors):
    """A runner that wrote no report makes the stage an error, never a pass: a unit it could not judge is not a
    unit that was judged and held. A stage that judged units and killed none proved nothing, so it is
    unmeasured, not a pass."""
    if runner_errors:
        return STATUS_ERROR
    if refusing_units(records):
        return STATUS_SURVIVED
    judged = [each for each in records if each["status"] != UNIT_WAIVED]
    if judged and not any(each["status"] == UNIT_KILLED for each in judged):
        return STATUS_UNMEASURED
    return STATUS_PASS


def unit_summary(records, baseline, candidates, not_judged, runner_errors, started):
    counts = Counter(each["status"] for each in records)
    return {"status": unit_stage_status(records, runner_errors), "units": records,
            "counts": {each: counts.get(each, 0) for each in UNIT_STATUSES},
            "red_on_green": sorted(baseline["red"]), "runner_errors": sorted(set(runner_errors)),
            "unrun": baseline["unrun"], "unloaded": baseline["unloaded"], "not_judged": not_judged,
            "spec_files": sum(len(each) for each in candidates.values()),
            "seconds": round(time.time() - started), "refusal_enabled": UNIT_REFUSAL_ENABLED}


def waiver_path(root, digest):
    """A waiver covers one unit in one worktree. The same text on another branch is another decision."""
    worktree = hashlib.sha256(str(root).encode()).hexdigest()[:WAIVER_WORKTREE_KEY_LENGTH]
    return WAIVERS_DIRECTORY / f"{worktree}-{digest}.json"


def waiver_for(root, digest):
    record = read_json(waiver_path(root, digest))
    return record if record and owner_decision_holds(record, phone=False) else None


def first_reported(root, digest):
    """When a run in this worktree first reported the unit as a survivor: the moment its unit stage
    finished. The proof's own finish time comes before the stage starts, so it would let in words typed
    before any unit report existed."""
    for run_directory in sorted_runs(root):
        units = (read_json(run_directory / FINDINGS_FILE_NAME) or {}).get("units") or {}
        if any(each["digest"] == digest for each in refusing_units(units.get("units", []))):
            return units.get("finished_at")
    return None


def waive(arguments):
    """The owner lets one surviving unit through. The session records it with the owner's own words,
    typed after the run that reported the unit; one record covers one unit in one worktree, and it stops
    counting the moment the unit's text changes, because the digest is made from that text."""
    root = require_repository("waive")
    digest = argument_value(arguments, "--unit")
    evidence = argument_value(arguments, "--evidence")
    if not digest or not evidence:
        print("usage: gate.py waive --unit <digest> --evidence \"<the owner's words>\"")
        sys.exit(2)
    reported = first_reported(root, digest)
    if reported is None:
        refuse("waive", [f"no proof run in this worktree reported a surviving unit {digest}. Copy the digest from "
                         "the unit report (`gate.py units`). A survivor found by `units --measure` cannot be waived: "
                         "declare a proof, and waive the unit its run reports."])
    problem = owner_evidence_problem(evidence, root, after=reported)
    if problem:
        refuse("waive", [problem])
    record = {"digest": digest, "evidence": evidence, "recorded_at": now_iso(), "worktree": str(root),
              "evidence_after": reported}
    write_json(waiver_path(root, digest), record, mode=PRIVATE_FILE_MODE)
    print(f"waive: unit {digest} may survive its removal; the owner's words are on record.")


def unit_reason(unit):
    if unit["status"] == UNIT_SURVIVED:
        return f"{len(unit['covering'])} spec file(s) ran it and stay green without it"
    return "; ".join(unit.get("evidence") or []) or "no spec runs it"


def unit_report_lines(units, listed_in):
    if not units:
        return ["units: not run"]
    if not units.get("units"):
        reason = f" ({units['reason']})" if units.get("reason") else ""
        return [f"units: {units.get('status')}{reason}"]
    counts = ", ".join(f"{count} {status}" for status, count in units["counts"].items() if count)
    header = (f"units: {units.get('status')}, {len(units['units'])} changed unit(s) judged in {units['seconds']}s over "
              f"{units['spec_files']} spec file(s): {counts}")
    if not units.get("refusal_enabled"):
        header += ". Reporting only; survivors do not refuse the push yet."
    lines = [header]
    for each in refusing_units(units["units"]):
        lines.append(f"  {each['digest']}  {each['path']}:{each['start_line']}  {each['kind']}  "
                     f"{each['original'].strip()[:UNIT_TEXT_PREVIEW]!r}  ({unit_reason(each)})")
    notes = (("runner_errors", "the runner wrote no report or no coverage for {}, so the stage is an error, "
                               "not a pass"),
             ("red_on_green", "not green with the change applied, so they judged nothing: {}"),
             ("unrun", "the time budget ran out before these spec files ran: {}"),
             ("unloaded", "these spec files failed to load, so the units they would run are unmeasured: {}"))
    listed = {key: units.get(key) or [] for key, _text in notes}
    # A spec file that failed to load is red too. It is named once, under the note that says why.
    listed["red_on_green"] = [spec for spec in listed["red_on_green"] if spec not in listed["unloaded"]]
    for key, text in notes:
        if listed[key]:
            lines.append("  " + text.format(", ".join(listed[key][:LISTED_FILES_LIMIT])))
    if units.get("not_judged"):
        lines.append(f"  {len(units['not_judged'])} changed line(s) or file(s) produced no unit and were not judged; "
                     f"they are listed in {listed_in}")
    return lines


def units_command(arguments):
    """The unit report of the latest proof run, or `--measure`: run the unit stage on this worktree's change under
    the gate lock without a declaration, to measure it. `--budget SECONDS` bounds it, and 0 runs it to the end."""
    root = require_repository("units")
    if "--measure" not in arguments:
        run_directory = latest_run(root)
        findings = read_json(run_directory / FINDINGS_FILE_NAME) if run_directory else None
        print("\n".join(unit_report_lines((findings or {}).get("units"), PROOF_NOT_JUDGED_AT)))
        return
    # The measure holds the gate lock every proof waits for, so it stops at a budget unless asked for no limit.
    budget_text = argument_value(arguments, "--budget", str(UNIT_STAGE_BUDGET_SECONDS))
    if not budget_text.isdigit():
        sys.exit(f"units: --budget takes a whole number of seconds, 0 for no limit, not {budget_text!r}")
    budget = int(budget_text)
    base_sha = diff_base(root)
    spec_patch, code_patch, paths = split_patch(snapshot_patch(root, base_sha))
    given = argument_value(arguments, "--out")
    directory = Path(given) if given else Path(tempfile.mkdtemp(prefix="gate-units-"))
    directory.mkdir(parents=True, exist_ok=True)
    log = file_logger(directory / LOG_FILE_NAME)
    with gate_lock(log):
        result = unit_stage(spec_patch, code_patch, base_sha, paths, directory, log, root, budget=budget or None)
    report_path = directory / UNITS_FILE_NAME
    write_json(report_path, result)
    print("\n".join(unit_report_lines(result, MEASURE_NOT_JUDGED_AT)))
    print(f"report: {report_path}")
    print("A survivor found by --measure cannot be waived; only a declared proof's report can be.")


# ------------------------------------------------------------------ stop

def stop():
    payload = read_json_stdin()
    if payload.get("stop_hook_active"):
        return
    root = repository_root(payload.get("cwd") or os.getcwd())
    if root is not None:
        settle_interrupted_runs(root)
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
        elif code_paths and not evidence_is_fresh(root, meta):
            messages.append(f"proof {findings.get('id')} finished {status} but the diff changed since it ran. It is stale. Redeclare to re-prove.")
        elif status != STATUS_PASS:
            reasons = "; ".join(f"{each['spec_file']}: {each.get('reason', each['status'])}"
                                for each in findings.get("proofs", []) + findings.get("sweep", [])
                                if each["status"] != STATUS_PASS)
            messages.append(f"proof {findings.get('id')} finished {status}. {reasons or findings.get('reason', '')}")
    comment_message = comment_stop_message(root)
    if comment_message:
        messages.append(comment_message)
    live_message = live_stop_message(root)
    if live_message:
        messages.append(live_message)
    if code_paths:
        review_message = review_stop_message(root)
        if review_message:
            messages.append(review_message)
        try:
            messages.append(summarize_references(references_report_for(root)))
        except Exception as error:  # a broken reference check must be visible, never silent
            messages.append(f"reference check failed: {error}")
    return "\n".join(messages) if messages else None


def settle_interrupted_runs(root):
    """A prover killed by a restart leaves its run `pending` for ever, and every reader then says the
    proof is still running. Give such a run a terminal status the first time anyone looks."""
    settled = []
    for run_directory in sorted_runs(root):
        findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
        units = findings.get("units") or {}
        proof_pending = findings.get("status") == STATUS_PENDING
        units_pending = units.get("status") == STATUS_PENDING
        if not (proof_pending or units_pending) or not owner_is_gone(findings):
            continue
        if proof_pending:
            findings["status"] = STATUS_INTERRUPTED
            findings["reason"] = ("the prover did not finish: the machine restarted or the process was killed. "
                                  "Nothing was proven. Declare again to re-prove.")
            findings["finished_at"] = now_iso()
        if units_pending:
            units.update(status=STATUS_INTERRUPTED, finished_at=now_iso(),
                         reason="the prover stopped during the unit stage; the next proof resets the gate worktree")
        write_json(run_directory / FINDINGS_FILE_NAME, findings)
        settled.append(run_directory.name)
    return settled


# --------------------------------------------------------------- surface

def surface():
    payload = read_json_stdin()
    prompt = payload.get("prompt") or payload.get("user_prompt") or ""
    root = repository_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return
    store_owner_words(payload, OWNER_SOURCE_PROMPT, prompt, root=root)
    settle_interrupted_runs(root)
    marker_path = proof_directory(root) / SURFACED_MARKER_NAME
    surfaced = set((read_json(marker_path, default=[]) or []))
    lines = []
    for run_directory in sorted_runs(root):
        findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
        if findings.get("status", STATUS_PENDING) == STATUS_PENDING or run_directory.name in surfaced:
            continue
        surfaced.add(run_directory.name)
        details = "; ".join(f"{each['spec_file']} -> {each['status']}" + (f" ({each['reason']})" if each.get("reason") else "")
                            for each in findings.get("proofs", []) + findings.get("sweep", []))
        lines.append(f"gate: proof {run_directory.name} finished {findings['status']}. {details}")
    if lines:
        write_json(marker_path, sorted(surfaced))
    protocol = phase_protocol_line(root)
    if protocol:
        lines.append(protocol)
    decision = comment_decision_line(root, prompt)
    if decision:
        lines.append(decision)
    if lines:
        print("\n".join(lines))


def comment_decision_line(root, prompt):
    """When review comments are waiting and this message answers one, say so at once.

    The user should not have to run anything. They answer in their own words, in chat, and the
    session records it while the words are still in front of it. Nothing here reads a verdict out
    of the message: guessing which comment the user meant is the one mistake worth more than the
    convenience, so the message only tells the session to record what it can see and to ask when it
    cannot tell."""
    state = comment_state(root)
    if not state or not state["undecided"] or not (prompt or "").strip():
        return None
    waiting = ", ".join(
        f"{thread['token']} ({thread['path']}:{thread['line']})" for thread in state["undecided"][:8])
    return (f"gate: {len(state['undecided'])} review comment(s) on pull request {state['pr']} are waiting "
            f"for a decision: {waiting}. If the message above answers any of them, record it now, "
            f"before anything else, one call per comment:\n"
            f"  {TRIAGE_WRAPPER} decide --pr {state['pr']} --token <C> --verdict <verdict> "
            f"--evidence \"<the user's words, verbatim>\"\n"
            f"  verdicts: {', '.join(COMMENT_VERDICTS)}\n"
            f"Quote them word for word, never your summary. If you cannot tell which comment they "
            f"meant, ask which one rather than guess: a decision recorded against the wrong comment "
            f"is worse than none, because nothing later shows it went to the wrong place.")


def phase_protocol_line(root):
    """One line per prompt while a phase is active. The phase loop runs only when the user asks for it
    with /phase: the daemon drove 3 of 21 pull requests through all seven phases, so a default that
    every prompt announced was a default in name only."""
    phase = phase_number(root)
    if phase is None:
        return None
    return (f"gate: phase {phase} of 7 is active on this branch. Stay inside /phase-{phase}: its allowed locations and"
            f" done criteria bind every edit. End with .claude/bin/agent_task_finalize --phase {phase}, then hand off.")


# ------------------------------------------------------------ commit-msg

def commit_msg(arguments):
    source = arguments[1] if len(arguments) > 1 else None
    message_path = Path(arguments[0])
    root = repository_root(os.getcwd())
    if root is None:
        return
    present = replay_paths(root)
    # git reports every commit made while MERGE_MSG exists as a merge: a conflicted or `-n` cherry-pick is a new
    # commit on this branch, and only a real merge, which leaves MERGE_HEAD, is the merge's own.
    if source == "merge" and MERGE_MARKER in present:
        return
    replay = replay_marker(root, present)
    if replay:
        print(f"gate: {replay} says git is replaying a commit it did not write, so its trailers stay as they are.",
              file=sys.stderr)
        # A reword types a new message now, so a phase typed into it is not the replayed commit's own. Keep only
        # the phase the commit already on HEAD carried; a pick or a continue replays an authored message, so it
        # stays, and a squash keeps its highest trailer.
        if is_reword(replay):
            set_phase(root, message_path, head_phase(root))
        else:
            keep_highest_phase(root, message_path)
        return
    own_phase = phase_of_message(message_path)
    claude = bool(CLAUDE_TRAILER_PATTERN.search(message_path.read_text()))
    staged_code = claude and stages_code(root)
    # An amend that stages nothing still makes a commit of HEAD's code, so a phase typed into its message would
    # exempt that code from the proof and from CI, whatever the phase now active. An amend with `-m` or `-F` is
    # reported with the source `message`, not `commit`, so both are checked. Only the phase HEAD carried stays.
    amended_code = claude and source in ("commit", "message") and not staged_code and touches_code(root, "HEAD")
    if amended_code:
        set_phase(root, message_path, head_phase(root))
    else:
        stamped = stamped_phase(phase_number(root), own_phase, source)
        if stamped is not None:
            set_phase(root, message_path, stamped)
        elif staged_code:
            drop_phase(message_path)
    if not (staged_code or amended_code):
        return
    # Clear the reference verdict first, so a later amend that removes nothing drops a stale `not-computed` or
    # `Used` instead of carrying it for ever: `--if-exists replace` leaves a trailer proof_trailers omits.
    verdict_lines = (f"{REFERENCES_VERDICT_TRAILER}:", f"{UNPROVABLE_TRAILER}:")
    keep_lines(message_path, lambda line: not line.startswith(verdict_lines))
    for trailer, value in proof_trailers(root):
        git(root, "interpret-trailers", "--in-place", "--if-exists", "replace", "--trailer", f"{trailer}: {value}",
            str(message_path))


def head_phase(root):
    return phase_of_body(git(root, "log", "-1", "--format=%B", "HEAD", allow_exit_codes=(0, 128)).stdout)


def replay_paths(root):
    """Which of the names git leaves while it merges, replays or picks a commit are present now."""
    names = (*REPLAY_MARKERS, EDIT_STOP_MARKER)
    locations = git(root, "rev-parse", *[part for name in names for part in ("--git-path", name)]).stdout.splitlines()
    return {name for name, location in zip(names, locations) if (root / location).exists()}


def replay_marker(root, present=None):
    """What says git replays a commit without its author, or None. Measured on git 2.50: a rebase runs
    prepare-commit-msg for every replayed commit with the source `message`, and the old hook then wrote
    the active phase over each commit's own. On PR 1065 that turned commits titled "Phase 2", "Phase 3"
    and "Phase 4" into `Phase: 7` beside their `[skip ci]`, and pre-push refused 42 of them."""
    present = replay_paths(root) if present is None else present
    if not present & set(REBASE_DIRECTORIES):
        return None
    for name in (APPLY_DIRECTORY, PICK_MARKER):
        if name in present:
            return name
    action = os.environ.get("GIT_REFLOG_ACTION", "")
    if action.startswith(REBASE_REFLOG_ACTION):
        return f"GIT_REFLOG_ACTION={action}"
    if CONFLICT_REPLAY_MARKER in present and EDIT_STOP_MARKER not in present:
        return CONFLICT_REPLAY_MARKER
    return None


def stamped_phase(active, own, source):
    """The phase a commit carries. A new commit carries the active phase, whatever its message says: a session
    that typed `Phase: 2` during phase 7 got `[skip ci]` and no proof check. An amend, and `-C HEAD`, which git
    reports the same way, keeps the higher of the two, so code amended into a phase 5 commit during phase 7 is
    phase 7. With no phase active a commit carries none: the loop is opt-in, and a `Phase: 2` copied from an
    earlier message exempted code from the proof and from CI."""
    if active is None:
        return None
    if source == "commit" and own is not None:
        return max(own, active)
    return active


def phase_value(text):
    return int(text) if text and text.strip().isdigit() else None


def phase_of_body(body):
    """The highest phase a message claims. A squash during a rebase keeps every squashed commit's trailer, and
    reading the first one let a phase 7 change pass as phase 2."""
    phases = [int(value) for value in PHASE_TRAILER_LINE.findall(body or "")]
    return max(phases) if phases else None


def phase_of_message(message_path):
    return phase_of_body(message_path.read_text())


def write_lines(message_path, lines):
    message_path.write_text("\n".join(lines).rstrip("\n") + "\n")


def keep_lines(message_path, keep):
    """Rewrite the message without the lines `keep` rejects."""
    lines = message_path.read_text().splitlines()
    kept = [line for line in lines if keep(line)]
    if len(kept) != len(lines):
        write_lines(message_path, kept)


def keep_highest_phase(root, message_path):
    """A squash replays several messages as one, each with its own Phase trailer. One stays: the highest."""
    text = message_path.read_text()
    if len(PHASE_TRAILER_LINE.findall(text)) < 2:
        return
    highest = phase_of_body(text)
    keep_lines(message_path, lambda line: not PHASE_TRAILER_LINE.match(line))
    git(root, "interpret-trailers", "--in-place", "--trailer", f"{PHASE_TRAILER}: {highest}", str(message_path))
    write_ci_skip(message_path, highest)


def drop_phase(message_path):
    """A Claude commit made with no phase active claims no phase, and loses the `[skip ci]` the gate wrote."""
    keep_lines(message_path, lambda line: not PHASE_TRAILER_LINE.match(line) and line.strip() != CI_SKIP_KEYWORD)


def set_phase(root, message_path, phase):
    """Make the message carry exactly `phase`: its trailer and the `[skip ci]` a phase 1 to 5 commit takes, or
    neither when `phase` is None. A message amend restores the phase HEAD carried this way, so a phase typed
    into the new message cannot exempt code HEAD already holds."""
    if phase is None:
        drop_phase(message_path)
        return
    git(root, "interpret-trailers", "--in-place", "--if-exists", "replace",
        "--trailer", f"{PHASE_TRAILER}: {phase}", str(message_path))
    write_ci_skip(message_path, phase)


def is_reword(marker):
    """A reword re-types a commit's message now, so a phase in it is the session's, not the replayed commit's.
    A pick, a continue and a squash replay an authored message, which the gate keeps as it is."""
    return marker.startswith("GIT_REFLOG_ACTION=") and "reword" in marker


def write_ci_skip(message_path, phase):
    """Put the keyword in the body of a phase 1 to 5 commit, above git's comment block so a verbose commit
    template cannot cut it off. One keyword is enough; an existing one of any spelling is left alone. A
    commit that became phase 6 or 7 loses the line the gate wrote, or pre-push would refuse it for ever."""
    if phase not in CI_SKIP_PHASES:
        keep_lines(message_path, lambda line: line.strip() != CI_SKIP_KEYWORD)
        return
    text = message_path.read_text()
    if CI_SKIP_PATTERN.search(text):
        return
    lines = text.splitlines()
    index = ci_skip_insertion_point(lines)
    block = [CI_SKIP_KEYWORD, ""]
    if index > 0 and lines[index - 1].strip():
        block.insert(0, "")
    lines[index:index] = block
    write_lines(message_path, lines)


def ci_skip_insertion_point(lines):
    """Above the trailing trailer block, and above git's comment block, so the keyword stays in the body and
    the trailers stay one block."""
    # git cuts everything from its comment block, the verbose diff included, so that is the ceiling.
    ceiling = next((number for number, line in enumerate(lines) if line.startswith("#")), len(lines))
    index = ceiling
    for number in range(ceiling - 1, -1, -1):
        line = lines[number]
        if not line.strip():
            continue
        if TRAILER_LINE.match(line):
            index = number
            continue
        break
    return index


def stages_code(root):
    """Whether the commit being made adds code: for an amend, what the amend adds to HEAD."""
    return any(is_code_path(path) for path in changed_names(root, "diff", "--cached", "--name-only"))


def commit_references_report(root):
    """The report a commit trailer states: a fresh one when the stored one is stale. A scan that fails falls back
    to the stored report only when it was made for this change, so a missing ctags or ast-grep never refuses a
    commit and never stamps an older change's verdict."""
    try:
        return references_report_for(root)
    except Exception:  # noqa: BLE001 - a trailer is evidence, never a reason to refuse the commit
        try:
            stored = fresh_references_report(root)
        except Exception:  # noqa: BLE001 - the fallback must not raise the error it stands in for
            stored = None
        return stored or {"verdict": VERDICT_NOT_COMPUTED}


def proof_trailers(root):
    """Proof and reference trailers for a Claude commit that touches code."""
    trailers = []
    run_directory = latest_run(root)
    if run_directory is not None:
        findings = read_json(run_directory / FINDINGS_FILE_NAME) or {}
        meta = read_json(run_directory / META_FILE_NAME) or {}
        fresh = "yes" if evidence_is_fresh(root, meta) else "no"
        trailers += [(PROOF_ID_TRAILER, run_directory.name),
                     (PROOF_STATUS_TRAILER, findings.get("status", STATUS_PENDING)), (PROOF_FRESH_TRAILER, fresh)]
    # A commit made before any Stop hook ran carried the verdict of an older change, or none.
    report = commit_references_report(root)
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
        revisions = pushed_revisions(root, local_sha, remote_sha)
        for sha in revisions:
            violations.extend(commit_violations(root, sha))
        violations.extend(tip_freshness_violations(root, revisions))
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
    if (len(reason) < SKIP_REASON_MINIMUM_CHARACTERS or PLACEHOLDER_TEXT.match(reason)
            or gate_printed(comparable(reason))):
        print("usage: gate.py skip-once \"<why this push may bypass the gate>\"")
        print(f"  the reason goes on record; write at least {SKIP_REASON_MINIMUM_CHARACTERS} characters of your own,"
              " not the placeholder and not text the gate printed.")
        sys.exit(2)
    write_json(SKIP_TOKEN_FILE, {"reason": reason, "at": now_iso()})
    print(f"gate: the next refused push within 15 minutes goes through once. Reason on record: {reason}")


# ------------------------------------------------------------ owner words

def store_owner_words(payload, source, text, root=None):
    """Keep what the owner typed, for a later `--evidence` check. A pasted block and the daemon's own lines
    are not the owner's words. A phone reply keeps its question and the worktree root it was typed in, and
    loses its lead, so the lead can neither pad a short answer nor be quoted. The lead is read before pasted
    blocks are removed, so a lead that only shows after a paste is no lead. Only the owner can read the file,
    two sessions cannot interleave a line, and a failure here never stops the hook that called it."""
    raw = text or ""
    record = {"at": now_iso(), "source": source, "session": payload.get("session_id"), "cwd": payload.get("cwd")}
    stripped = raw.lstrip()
    lead = PHONE_LEAD.match(stripped) if source == OWNER_SOURCE_PROMPT and root is not None else None
    if lead:
        record.update(source=OWNER_SOURCE_PHONE, question=lead["question"], worktree=str(real_path(root)))
        raw = phone_words(record, stripped[lead.end():])
    text = QUOTED_BLOCKS.sub(" ", raw).strip()
    if MACHINE_PROMPT_SHAPES.match(text) or not (text or record.get("tap")):
        return
    record["text"] = text
    try:
        append_json_line(OWNER_WORDS_DIRECTORY / f"{record['at'][:DAY_LENGTH]}.jsonl", record)
        prune_owner_words()
    except OSError:
        pass


def append_json_line(path, record):
    """One record on one line of a day's file, readable by the owner only, under a lock so two processes
    never interleave a line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(record) + "\n")


def prune_owner_words():
    cutoff = days_ago(OWNER_WORDS_KEEP_DAYS)[:DAY_LENGTH]
    for path in OWNER_WORDS_DIRECTORY.glob("*.jsonl"):
        if path.stem < cutoff:
            path.unlink(missing_ok=True)


def owner_words():
    return memoized(("owner_words",), lambda: read_json_line_directory(OWNER_WORDS_DIRECTORY))


def read_json_line_directory(directory):
    return [record for path in sorted(Path(directory).glob("*.jsonl")) for record in read_json_lines(path)]


def read_json_lines(path):
    records = []
    for line in Path(path).read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def split_sentences(text):
    """A text as its sentences, each with whether it may start a quote. Case, accents written two ways, curly
    quotes, backticks, spacing, wrapping quotes and a closing full stop do not change what someone said; a closing
    question mark does. A soft start (a line break, a colon or a semicolon, a dash, a list marker) after a negation
    is no start: "Do not: waive this unit." and "Please do not" then "waive this unit" on the next line say no."""
    def compute():
        parts = SENTENCE_BREAK.split("\n" + normalized(text).casefold())
        pieces, previous_raw = [], ""
        for raw in parts:
            piece = re.sub(r"\s+", " ", raw).strip().strip("\"'").strip().rstrip(".!").strip()
            if piece:
                soft = not previous_raw.rstrip().endswith((".", "!", "?"))
                blocked = bool(pieces and soft and NEGATION.search(pieces[-1][0]))
                pieces.append((piece, not blocked))
                previous_raw = raw
        return tuple(pieces)
    return memoized(("sentences", text), compute)


def normalized(text):
    """Accents written two ways read as one, curly quotes read as straight ones, and backticks are gone."""
    return unicodedata.normalize("NFC", text or "").translate(SMART_QUOTES)


def comparable(text):
    return " ".join(piece for piece, _start in split_sentences(text))


def says_whole(text, quote):
    """The quote is whole sentences of the text: it starts where a sentence starts and ends where one ends, and
    a sentence the owner wrapped across two lines still reads as one. "waive this unit" is not said by "do not
    waive this unit, it needs a spec". `quote` is already comparable."""
    joined, starts, ends = "", set(), set()
    for piece, may_start in split_sentences(text):
        joined += " " if joined else ""
        if may_start:
            starts.add(len(joined))
        joined += piece
        ends.add(len(joined))
    position = joined.find(quote) if quote else -1
    while position != -1:
        if position in starts and position + len(quote) in ends:
            return True
        position = joined.find(quote, position + 1)
    return False


def inside_window(record, after, before):
    moment = record.get("at", "")
    return (after is None or moment >= after) and (before is None or moment <= before)


def repository_worktrees(root):
    """Every worktree of the repository that holds `root`: the owner answers about a pull request from
    whichever checkout their session started in, and words typed in another repository count for nothing."""
    def compute():
        listing = git(root, "worktree", "list", "--porcelain", allow_exit_codes=(0, 128)).stdout
        paths = [Path(line[len("worktree "):]) for line in listing.splitlines() if line.startswith("worktree ")]
        return paths or [Path(root)]
    return memoized(("worktrees", str(root)), compute)


def real_path(path):
    """Compared as real paths: on macOS a temporary folder is reached through /var and git prints /private/var."""
    return memoized(("real_path", str(path)), lambda: Path(os.path.realpath(path)))


def inside_any(cwd, places):
    return bool(cwd) and any(real_path(cwd).is_relative_to(place) for place in places)


def phone_mark(words):
    """An `R:` mark as (tap, reply): ("IN", None) for a tap, (None, "<text>") for a reply, (None, None) for none."""
    mark = PHONE_MARK.match((words or "").strip())
    if not mark:
        return None, None
    reply = mark.group(1).strip()
    return (reply.upper(), None) if reply.upper() in MARK_VERDICTS else (None, reply)


def phone_words(record, words):
    """The words of a phone reply after its lead. On a review comment a tap is kept as the tap, with no words, and
    any other reply is an instruction, kept without a leading `R:` mark."""
    if not record["question"].startswith("C"):
        return words
    tap, reply = phone_mark(words)
    if tap:
        record["tap"] = tap
        return ""
    return words if reply is None else reply


def owner_said(evidence, root, after=None, before=None, phone=False):
    """True when the owner typed these words as whole sentences, in this repository, inside the window, and
    the gate had not printed them first. `after` is what the decision answers; `before` is when it was
    recorded. An option label in a question dialog is the session's text and never counts."""
    return matching_record(evidence, root, after, before, phone) is not None


def matching_record(evidence, root, after=None, before=None, phone=False, question=None):
    """The stored record that holds the evidence as the owner's words, or None. A phone lead on the evidence is
    removed first, so "phone Q2: yes" is three characters, not thirteen."""
    evidence = PHONE_LEAD.sub("", (evidence or "").strip())
    quote = comparable(evidence)
    if evidence_length(evidence) < EVIDENCE_MINIMUM_CHARACTERS or quote.endswith("?"):
        return None
    places = [real_path(place) for place in repository_worktrees(root)]
    return next((record for record in owner_words()
                 if owner_typed(record, quote, root, places, after, before, phone, question)), None)


def evidence_length(evidence):
    """The evidence's length without the padding at its ends or a leading list marker, so "yes..........",
    "``````````yes" and "1. yes" are three characters, while the dash inside "c2 - get in A" still counts."""
    text = re.sub(r"\s+", " ", normalized(evidence))
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", text.strip(EVIDENCE_PADDING))
    return len(text.strip(EVIDENCE_PADDING))


def owner_typed(record, quote, root, places, after, before, phone=False, question=None):
    """Whether this record holds the quote as the owner's own words. An option label is the session's text."""
    if record.get("source") == OWNER_SOURCE_OPTION or not inside_window(record, after, before):
        return False
    if not typed_where_it_counts(record, root, places, phone, question) or not says_whole(record.get("text"), quote):
        return False
    return not gate_printed(quote, before=record.get("at"))


def typed_where_it_counts(record, root, places, phone, question):
    """Keyboard words count in every worktree of the repository. A phone reply counts only where the caller
    allows phone words, only in the worktree it was typed in, and only for the question named. With no question
    named, only a `Q` reply counts: a permission approval or a comment reply backs nothing else. A tap is a
    choice and never counts as words."""
    if record.get("source") != OWNER_SOURCE_PHONE:
        return inside_any(record.get("cwd"), places)
    if not phone or record.get("tap"):
        return False
    asked = record.get("question") or ""
    fits = asked == question if question else asked.startswith("Q")
    return fits and typed_in(record, root)


def typed_in(record, root):
    return bool(record.get("worktree")) and real_path(record["worktree"]) == real_path(root)


def printed_texts():
    """Everything the gate printed, once per process. The trace command only prints what was recorded, so
    it is not the gate speaking."""
    return memoized(("printed",), lambda: [(record.get("at", ""), comparable(gate_spoke(record)))
                                           for record in read_traces() if record.get("command") != "trace"])


def gate_spoke(record):
    output = f"{record.get('stdout') or ''}\n{record.get('stderr') or ''}"
    return "\n".join(line for line in output.split("\n") if not ECHO_LINE.search(line))


def gate_printed(quote, before=None):
    """Text the gate printed before the owner typed it is not their decision, even when they pasted it back.
    One skip went through with `<reason>`, copied from the gate's own hint. Only output older than the owner's
    words counts: the gate echoes a decision it records, and that echo must not undo the decision."""
    return any(quote in text for moment, text in printed_texts() if before is None or moment <= before)


def decision_digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:DECISION_DIGEST_LENGTH]


def recorded_before(record, moment):
    return (record.get("recorded_at") or "") < moment


def owner_words_epoch():
    def compute():
        try:
            return OWNER_WORDS_EPOCH_FILE.read_text().strip() or OWNER_WORDS_EPOCH
        except OSError:
            return OWNER_WORDS_EPOCH
    return memoized(("owner_words_epoch",), compute)


def grandfathered_decisions():
    return memoized(("grandfathered",), lambda: set(read_json(GRANDFATHERED_DECISIONS_FILE, default=[]) or []))


def owner_decision_holds(record, phone=False):
    """A later decision must still quote the owner, in the worktree it was recorded in, inside the window it was
    recorded with. One dated before the store went live holds only when it is on the list taken when it went
    live: a record anyone can write must not excuse itself by its own date. `phone` says whether a phone reply
    may back this kind of decision; each caller states it."""
    recorded_at = record.get("recorded_at") or ""
    if recorded_before(record, owner_words_epoch()):
        return decision_digest(record) in grandfathered_decisions()
    if not record.get("worktree"):
        return False
    return owner_said(record.get("evidence"), Path(record["worktree"]), after=record.get("evidence_after"),
                      before=recorded_at, phone=phone)


def owner_evidence_problem(evidence, root, after=None, phone=False):
    if owner_said(evidence, root, after=after, phone=phone):
        return None
    if not phone and owner_said(evidence, root, after=after, phone=True):
        return "--evidence came from the owner's phone, and this decision must be typed at the keyboard."
    return owner_words_refusal(after)


def owner_words_refusal(after):
    return ("--evidence must be the owner's own words: whole sentences copied from what they typed in this repository"
            + (f" after {after}" if after else "") + f", at least {EVIDENCE_MINIMUM_CHARACTERS} characters. "
            "Those words are not in the owner's prompts or answers. Quote from the start of a sentence or a line "
            "to its end. A question, your summary, the gate's own text, a pasted block and an option label you wrote "
            "do not count. A phone reply counts only for the question its `phone <id>: ` lead names, only in the "
            "worktree it was typed in, and the lead is not the owner's words. Ask the owner, and record their answer "
            "once they give it.")


def answers():
    """PostToolUse hook for AskUserQuestion. An answer given in a question dialog never passes through
    UserPromptSubmit, so without this the owner's most direct decisions were never stored. Only the response
    is read. An answer that is one of the option labels the session wrote is stored as a choice, not as words."""
    payload = read_json_stdin()
    if payload.get("tool_name") != "AskUserQuestion" or repository_root(payload.get("cwd") or os.getcwd()) is None:
        return
    labels = option_labels(payload.get("tool_input"))
    for text, source in answers_in(payload.get("tool_response"), labels):
        store_owner_words(payload, source, text)


def option_labels(tool_input):
    questions = (tool_input or {}).get("questions") or []
    return {comparable(option.get("label")) for question in questions if isinstance(question, dict)
            for option in question.get("options") or [] if isinstance(option, dict)}


def answers_in(response, labels):
    if not isinstance(response, dict):
        return []
    found = []
    for answer in (response.get("answers") or {}).values():
        text = str(answer or "")
        found.append((text, OWNER_SOURCE_OPTION if made_of_labels(text, labels) else "answer"))
    for annotation in (response.get("annotations") or {}).values():
        if isinstance(annotation, dict):
            found.append((str(annotation.get("notes") or ""), "note"))
    return [(text, source) for text, source in found if text.strip() and text.strip() != NOTES_ONLY_ANSWER]


def made_of_labels(text, labels):
    """One label, or several joined by ", " the way a multi-select answer is written. A label can hold a comma
    itself ("What the phase changed, with file:line"), so the parts are joined back into labels from the left."""
    parts = text.split(", ")
    position = 0
    while position < len(parts):
        ends = range(len(parts), position, -1)
        end = next((end for end in ends if comparable(", ".join(parts[position:end])) in labels), None)
        if end is None:
            return False
        position = end
    return True


def pushed_revisions(root, local_sha, remote_sha):
    """What this push actually adds to the remote: everything reachable from the new tip and from no
    remote-tracking ref, and not from the tip being replaced, newest first in topological order.

    `<remote tip>..<local>` was wrong after a rebase. The old tip stops being an ancestor, so the range fills
    with the upstream commits the branch was rebased onto and the gate blamed the session for other people's
    work. Excluding every remote-tracking ref answers the real question: which commits has nobody pushed yet."""
    excluded = ["--remotes"] + ([remote_sha] if remote_sha != ZERO_SHA else [])
    return git(root, "rev-list", "--topo-order", local_sha, "--not", *excluded,
               allow_exit_codes=(0, 128)).stdout.split()


def already_upstream(root, sha):
    completed = git(root, "merge-base", "--is-ancestor", sha, UPSTREAM_BRANCH, allow_exit_codes=(0, 1, 128))
    return completed.returncode == 0


def touches_code(root, sha):
    return any(is_code_path(path) for path in commit_paths(root, sha))


def commit_facts(root, sha):
    """What pre-push asks of one commit, asked once. It used to ask each question twice per pushed commit."""
    def compute():
        body = git(root, "log", "-1", "--format=%B", sha).stdout
        claude = not already_upstream(root, sha) and is_gated_commit(root, sha)
        return {"body": body, "claude": claude, "gated_code": claude and touches_code(root, sha),
                "phase": phase_of_body(body)}
    return memoized(("commit", str(root), sha), compute)


def owes_proof(root, sha):
    facts = commit_facts(root, sha)
    return facts["gated_code"] and facts["phase"] not in PROOF_EXEMPT_PHASES


def tip_freshness_violations(root, revisions):
    """A trailer records what was true when its commit was written. A rebase that resolves a conflict changes
    the code under the commits and keeps their trailers, so the files of the newest proof are judged again here.
    Each file is read at the newest Claude commit that touched it: a spec committed after the code belongs to
    the proof, and the owner's own commits on top are not the proof's business."""
    proven = [sha for sha in revisions if owes_proof(root, sha)]
    if not proven:
        return []
    proof_id = trailer_value(commit_facts(root, proven[0])["body"], PROOF_ID_TRAILER)
    meta = read_json(runs_directory(root) / proof_id / META_FILE_NAME) if proof_id else None
    if not meta or not meta.get("change_id"):
        return []
    stale = stale_paths(root, meta, revisions, proven[0])
    short = proven[0][:SHORT_SHA_LENGTH]
    if stale is None:
        base = meta.get("base_ref") or base_reference(root)
        return [f"{short}: the base proof {proof_id} measured from ({base}) has no merge-base with this commit in "
                "this clone. Fetch it and push again."]
    if not stale:
        return []
    listed = ", ".join(stale[:LISTED_FILES_LIMIT])
    return [f"{short}: proof {proof_id} proved a different change than the one this push carries ({listed}). A "
            "rebase or an amend changed the code under it. Redeclare to re-prove, then amend the newest commit "
            "that touches code, so it carries the new Proof-Id."]


def stale_paths(root, meta, revisions, proven):
    """The files the proof saw that the push carries differently, or None when no base can be found. A file the
    proof saw that no commit carries counts only when it owes a proof: an untracked picture or script that sat
    in the worktree is never committed, and that is no reason to refuse. A file the proof never saw belongs to a
    later change, which carries its own proof, or to the owner. A run recorded before per-file identities is
    judged as a whole, at the proven commit."""
    base_ref, base_sha, proved = meta.get("base_ref"), meta.get("base_sha"), meta.get("path_ids")
    if proved is None:
        whole = commit_change_identity(root, proven, base_ref, base_sha)
        if whole is None:
            return None
        return [] if whole == meta["change_id"] else ["the whole change"]
    touched_by = {}
    # Newest first: only the proven commit and the Claude commits after it carry what the proof saw.
    for sha in revisions[:revisions.index(proven) + 1]:
        if commit_facts(root, sha)["claude"]:
            for path in commit_paths(root, sha):
                touched_by.setdefault(path, sha)
    stale = []
    for path, identity in proved.items():
        carried = commit_path_identities(root, touched_by.get(path, proven), base_ref, base_sha)
        if carried is None:
            return None
        if (path in carried and carried[path] != identity) or (path not in carried and owes_proof_path(path)):
            stale.append(path)
    return sorted(stale)


def commit_path_identities(root, sha, base_ref, base_sha):
    """Kept for the process, because pre-push asks it once for every file a proof saw."""
    return memoized(("commit_path_identities", str(root), sha, base_ref, base_sha),
                    lambda: measured_delta(root, sha, base_ref, base_sha, path_identities))


def commit_paths(root, sha):
    return memoized(("commit_paths", str(root), sha),
                    lambda: changed_names(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha))


def owes_proof_path(path):
    return is_code_path(path) or is_spec_path(path) or is_connector_fixture(path)


def commit_violations(root, sha):
    facts = commit_facts(root, sha)
    if not facts["gated_code"]:
        return []
    body, short, phase = facts["body"], sha[:SHORT_SHA_LENGTH], facts["phase"]
    verdict = trailer_value(body, REFERENCES_VERDICT_TRAILER)
    if verdict == VERDICT_USED:
        return [f"{short}: references verdict is {VERDICT_USED}; a removed or renamed name is still referenced"]
    if verdict == VERDICT_NOT_COMPUTED:
        return [f"{short}: the reference check could not run when this commit was made, so nothing shows a removed "
                "name is unreferenced. Run `gate.py references` to see why, fix it, and amend."]
    if phase not in CI_SKIP_PHASES and CI_SKIP_PATTERN.search(body):
        which = f"phase {phase}" if phase else "a code commit outside the phase loop"
        return [f"{short}: {which} must run CI, and this message carries "
                f"`{CI_SKIP_PATTERN.search(body).group(0)}`. Remove it and amend."]
    if phase in PROOF_EXEMPT_PHASES:
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
    value = phase_value(path.read_text())
    return value if value in PHASES else None


def request_number(root):
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    match = re.match(r"(?:requests|problems)/(\d+)", branch)
    return match.group(1) if match else None


def changed_files_all(root, base):
    tracked = changed_names(root, "diff", "--name-only", base)
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
    diff = git_diff(root, "--unified=0", *DIFF_FLAGS, base, "--", path).stdout
    changed = list(hunk_lines(diff))
    return ([(new, text) for kind, _old, new, text in changed if kind == "+"],
            [(old, text) for kind, old, _new, text in changed if kind == "-"])


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
            by_project.setdefault(project_of(path), []).append(project_relative(path))
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
            per_project.setdefault(project, set()).add(project_relative(path))
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
            tests.add(project_relative(path))
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
    if not evidence_is_fresh(root, meta):
        return ("proof", "FAIL", f"{run_directory.name} passed against an older diff; redeclare")
    return ("proof", "ok", run_directory.name)


def references_check(root, paths):
    if not code_paths_of(paths):
        return ("references", "skip", "no code changed")
    report = references_report_for(root)
    verdict = report["verdict"]
    if verdict == STATUS_NOT_APPLICABLE:
        return ("references", "ok", "no definition removed or renamed")
    return ("references", "FAIL" if verdict == VERDICT_USED else "ok", summarize_references(report, limit=3))


def finalize(arguments):
    root = require_repository("finalize")
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
    refresh_base_cache(root)
    checks.append(base_check(root, phase))
    if phase in (6, 7):
        checks.append(proof_check(root, paths))
    else:
        checks.append(("proof", "skip", f"phase {phase} needs no proof"))
    checks.append(references_check(root, paths))
    checks.append(review_check(root, paths))
    checks.append(comment_check(root, paths))
    checks.append(live_check(root, phase, paths))
    print_steps(checks)
    if checks[0][1] == "FAIL":
        sys.exit(2)
    if any(status == "FAIL" for _, status, _ in checks):
        sys.exit(1)


def tool_checks_in_checks_worktree(root, paths, run_specs):
    """rubocop, yarn and specs run against the branch state applied to the checks worktree, which is
    returned to origin/main afterwards unless another worktree holds it."""
    if root.resolve() == CHECKS_WORKTREE.resolve():
        return [("checks", "FAIL", "finalize runs from the PR worktree, not from the checks worktree")]
    if not checks_ready():
        return [("checks", "FAIL", f"no checks worktree with a slot at {CHECKS_WORKTREE}; run gate.py setup-checks")]
    checks = []
    try:
        with checks_lock():
            refusal = checks_in_use_refusal(root, checks_in_use())
            if refusal:
                return [("checks", "FAIL", refusal)]
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
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                checks.append(("checks", "FAIL", str(error)))
            finally:
                try:
                    checks += reset_checks_worktree()
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    checks.append(("reset", "FAIL", f"{error}\nrun gate.py checks reset from this worktree"))
    except ChecksLockTimeout as error:
        checks.append(("checks", "FAIL", str(error)))
    return checks


# ----------------------------------------------------------------- reviews

def reviews_directory(root):
    return proof_directory(root) / REVIEWS_DIRECTORY_NAME


def review_state(root):
    """For every required skill: missing, stale, or the record itself. One function decides, so the Stop
    hook and finalize can never disagree about whether a review still counts."""
    return evidence_states(root, reviews_directory(root), REVIEW_SLUGS)


def evidence_states(root, directory, names):
    """Missing, stale or ok, with the record, for each named record in one directory."""
    state = {}
    for name in names:
        record = read_json(directory / f"{name}.json")
        if record is None:
            state[name] = ("missing", None)
        elif not evidence_is_fresh(root, record):
            state[name] = ("stale", record)
        else:
            state[name] = ("ok", record)
    return state


def review_check(root, paths):
    if not code_paths_of(paths):
        return ("reviews", "skip", "no code changed")
    state = review_state(root)
    missing = [slug for slug, (status, _) in state.items() if status == "missing"]
    stale = [slug for slug, (status, _) in state.items() if status == "stale"]
    if missing or stale:
        parts = []
        if missing:
            parts.append("never run: " + ", ".join(missing))
        if stale:
            parts.append("ran against an older diff: " + ", ".join(stale))
        return ("reviews", "FAIL", "; ".join(parts) + f". Run each skill over the diff, then record it with {REVIEW_WRAPPER}.")
    counts = ", ".join(f"{slug} {len(record.get('items', []))}" for slug, (_status, record) in state.items())
    return ("reviews", "ok", counts)


def review_stop_message(root):
    """The Stop hook's line. A session that changed code and ran no review is the failure the user hit by
    hand every time, so say it at the end of every such session, not only at handoff."""
    state = review_state(root)
    owed = [f"{slug} ({skill})" for slug, skill, _purpose in REQUIRED_REVIEWS if state[slug][0] != "ok"]
    if not owed:
        return None
    return (f"{len(owed)} of {len(REQUIRED_REVIEWS)} required reviews are missing or stale for this diff: "
            + "; ".join(owed)
            + f". Run each skill over the change, then record every item it returned with {REVIEW_WRAPPER}. "
              "Record the full list, not a summary: each item needs its source file and line.")


def require_repository(command):
    root = repository_root(os.getcwd())
    if root is None:
        print(f"{command}: not inside a git repository")
        sys.exit(2)
    return root


def refuse(command, problems, code=2):
    print(f"{command}: nothing was recorded.")
    for problem in problems:
        print(f"  {problem}")
    sys.exit(code)


def argument_value(arguments, flag, default=None):
    if flag not in arguments:
        return default
    index = arguments.index(flag) + 1
    return arguments[index] if index < len(arguments) else default


def validate_review_items(root, items):
    """Every item carries a real source. A finding without a file and a line cannot be checked, and the
    sessions that summarised instead of listing are the ones that dropped findings on the floor."""
    problems = []
    if not isinstance(items, list):
        return ["the items file must hold a JSON list"]
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            problems.append(f"item {position}: not an object")
            continue
        for key in REVIEW_ITEM_REQUIRED_KEYS:
            if item.get(key) in (None, ""):
                problems.append(f"item {position}: `{key}` is missing")
        verdict = item.get("verdict")
        if verdict is not None and verdict not in REVIEW_VERDICTS:
            problems.append(f"item {position}: verdict `{verdict}` is not one of {', '.join(REVIEW_VERDICTS)}")
        path, line = item.get("file"), item.get("line")
        if not path or line in (None, ""):
            continue
        disk_path = Path(root) / str(path)
        if not disk_path.is_file():
            problems.append(f"item {position}: `{path}` does not exist in the repository")
            continue
        try:
            count = len(disk_path.read_text(errors="ignore").splitlines())
        except OSError:
            continue
        if not isinstance(line, int) or line < 1 or line > count:
            problems.append(f"item {position}: line {line} is outside `{path}` (1 to {count})")
    return problems


def items_file_inside_diff(root, items_file):
    """An items file written into the working tree is itself part of the diff, so recording it moves the
    digest and makes the reviews recorded before it stale. Refuse it rather than let that happen silently."""
    try:
        path = Path(items_file).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    if str(path).startswith(str(PROOF_DIRECTORY)):
        return False
    return git(root, "check-ignore", "-q", str(path), allow_exit_codes=(0, 1, 128)).returncode != 0


def review_slug(name):
    """The slug, or the skill's own name in any of the spellings a session reaches for: `/edge-case-hunter`,
    `agent-skills:code-simplify`, `code-review-and-quality`. A wrong name refused two recordings."""
    if name in REVIEW_SLUGS or not name:
        return name
    bare = bare_skill_name(name)
    for slug, skill, _purpose in REQUIRED_REVIEWS:
        if bare == bare_skill_name(skill) or bare in REVIEW_SKILL_NAMES.get(slug, ()):
            return slug
    return name


def bare_skill_name(text):
    return text.lstrip("/").split(":")[-1]


def review_record(arguments):
    """Record one skill's full findings against the current diff. The skill wrapper calls this."""
    root = require_repository("review-record")
    slug = review_slug(argument_value(arguments, "--skill"))
    items_file = argument_value(arguments, "--items-file")
    if slug not in REVIEW_SLUGS:
        print(f"review-record: --skill must be one of {', '.join(REVIEW_SLUGS)}")
        sys.exit(2)
    if not items_file:
        print("review-record: --items-file <path> is required. It holds a JSON list of every item the skill returned.")
        sys.exit(2)
    inside = items_file_inside_diff(root, items_file)
    if inside:
        print(f"review-record: {items_file} sits in the working tree, so it is part of the diff. "
              "Writing it changes the diff and makes every review recorded before it stale. "
              "Write the items file outside the repository, under the session scratchpad or $TMPDIR.")
        sys.exit(2)
    items = read_json(items_file)
    if items is None:
        print(f"review-record: {items_file} is missing or is not valid JSON")
        sys.exit(2)
    problems = validate_review_items(root, items)
    if problems:
        refuse("review-record", problems, code=1)
    skill = dict((each[0], each[1]) for each in REQUIRED_REVIEWS)[slug]
    record = {"slug": slug, "skill": skill, **evidence_stamp(root), "recorded_at": now_iso(), "items": items}
    write_json(reviews_directory(root) / f"{slug}.json", record)
    print(f"review-record: {slug} recorded, {len(items)} item(s), change {record['change_id']}")


def render_review_items(record):
    lines = []
    for item in record.get("items", []):
        source = f"`{item['file']}:{item['line']}`"
        lines.append(f"- **{item['title']}** ({item['verdict']}) {source}")
        detail = (item.get("detail") or "").strip()
        if detail:
            lines.append(f"  {detail}")
    return lines


def review_section(arguments):
    """The reviews as markdown for the pull request. The full list every time: a section written from
    memory is a section that quietly loses items."""
    root = require_repository("review-section")
    state = review_state(root)
    lines = ["### Review skills", ""]
    for slug, skill, purpose in REQUIRED_REVIEWS:
        status, record = state[slug]
        if status != "ok":
            lines.append(f"**{skill}** ({purpose}): {status}.")
            lines.append("")
            continue
        items = record.get("items", [])
        lines.append(f"**{skill}** ({purpose}): {len(items)} item(s).")
        lines += render_review_items(record) if items else ["- nothing returned."]
        lines.append("")
    lines += deviations_section(root)
    print("\n".join(lines).rstrip() + "\n")


# ---------------------------------------------------------- figma compare

def figma_key(text):
    return re.sub(r"\s+", " ", text or "").strip().lower().rstrip(":")


def edge_deltas(design, built, offset_y):
    return {"left": round(built["x"] - design["x"], 1),
            "top": round((built["y"] + offset_y) - design["y"], 1),
            "width": round(built["w"] - design["w"], 1),
            "height": round(built["h"] - design["h"], 1)}


def overlap_ratio(design, built, offset_y):
    """Intersection over union. Reported as evidence, never as the verdict."""
    ax, ay, aw, ah = design["x"], design["y"], design["w"], design["h"]
    bx, by, bw, bh = built["x"], built["y"] + offset_y, built["w"], built["h"]
    wide = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    tall = max(0, min(ay + ah, by + bh) - max(ay, by))
    intersection = wide * tall
    union = aw * ah + bw * bh - intersection
    return round(intersection / union, 4) if union else 0.0


def inside_region(box, region):
    return (box["x"] >= region["x"] and box["y"] >= region["y"]
            and box["x"] + box["w"] <= region["x"] + region["width"]
            and box["y"] + box["h"] <= region["y"] + region["height"])


def excused_by(box, deviations):
    """Only a user decision excuses a difference. A note Claude wrote excuses nothing."""
    for record in deviations:
        region = record.get("region")
        if record.get("kind") in RECORD_ONLY_KINDS:
            continue
        if (record.get("decided_by") == "user" and region and inside_region(box, region)
                and owner_decision_holds(record, phone=True)):
            return record["id"]
    return None


def compare_figma(design, built, offset_y, tolerance, deviations, exempt_texts):
    findings, matched = [], []
    # A list repeats the same label on every row, so pair the nth design occurrence with the nth
    # built occurrence. Pairing every one of them to the first match reports every row after the
    # first as moved by the row pitch, which is noise that buries the real differences.
    built_texts = {}
    for item in built.get("texts", []):
        built_texts.setdefault(figma_key(item["text"]), []).append(item)
    for each in built_texts.values():
        each.sort(key=lambda item: (item["y"], item["x"]))
    taken = {}
    for item in sorted(design.get("texts", []), key=lambda each: (each["y"], each["x"])):
        key = figma_key(item["text"])
        if not key or key in exempt_texts:
            continue
        candidates = built_texts.get(key, [])
        index = taken.get(key, 0)
        partner = candidates[index] if index < len(candidates) else None
        taken[key] = index + 1
        if partner is None:
            findings.append({"kind": "missing", "text": item["text"], "id": item.get("id"),
                             "detail": "the design has this text and the build does not"})
            continue
        # A fixed-width text box reports the box, not the glyph run, so its width says nothing.
        edges = FIGMA_TEXT_EDGES if item.get("hugs", True) else ("left", "top")
        deltas = edge_deltas(item, partner, offset_y)
        over = {edge: deltas[edge] for edge in edges if abs(deltas[edge]) > tolerance}
        record = {"text": item["text"], "id": item.get("id"), "deltas": deltas,
                  "overlap": overlap_ratio(item, partner, offset_y)}
        if not over:
            matched.append(record)
            continue
        excuse = excused_by(item, deviations)
        record["kind"] = "excused" if excuse else "moved"
        record["over"] = over
        if excuse:
            record["deviation"] = excuse
        findings.append(record)
    return findings, matched


def figma_compare(arguments):
    design_path = argument_value(arguments, "--design")
    built_path = argument_value(arguments, "--built")
    if not design_path or not built_path:
        print("figma-compare: --design <file> and --built <file> are both required")
        sys.exit(2)
    design = read_json(design_path)
    built = read_json(built_path)
    if design is None or built is None:
        print("figma-compare: one of the files is missing or is not valid JSON")
        sys.exit(2)
    offset_y = float(argument_value(arguments, "--offset-y", 0))
    tolerance = float(argument_value(arguments, "--tolerance", FIGMA_TOLERANCE))
    exempt = {figma_key(each) for each in (argument_value(arguments, "--exempt-text", "") or "").split("|") if each.strip()}
    root = repository_root(os.getcwd())
    deviations = read_deviations(root) if root else []
    findings, matched = compare_figma(design, built, offset_y, tolerance, deviations, exempt)
    moved = [each for each in findings if each.get("kind") == "moved"]
    missing = [each for each in findings if each.get("kind") == "missing"]
    if root:
        record_mismatch(root, design_key(design, design_path), bool(moved or missing))
    excused = [each for each in findings if each.get("kind") == "excused"]
    if "--json" in arguments:
        print(json.dumps({"matched": matched, "moved": moved, "missing": missing, "excused": excused}, indent=2))
        return
    print(f"tolerance {tolerance:g}px per edge, y offset {offset_y:g}px, {len(design.get('texts', []))} design text node(s)")
    print(f"matched {len(matched)}, moved {len(moved)}, missing {len(missing)}, excused {len(excused)}")
    for each in missing:
        print(f"  MISSING  {each['text'][:60]!r}  ({each['id']})")
    for each in moved:
        over = ", ".join(f"{edge} {value:+g}px" for edge, value in each["over"].items())
        print(f"  MOVED    {each['text'][:44]!r}  {over}  (overlap {each['overlap']})")
    for each in excused:
        print(f"  EXCUSED  {each['text'][:44]!r}  by deviation {each['deviation']}")
    if not matched and not moved:
        print("figma-compare: nothing was compared. A run that pairs no element proves nothing.")
        sys.exit(1)
    sys.exit(1 if moved or missing else 0)


# ------------------------------------------------------------ design links

FIGMA_URL = re.compile(r"https?://(?:www\.)?figma\.com/(?:design|file|proto)/[^\s<>\"')\]]+")
FIGMA_FILE_KEY = re.compile(r"/(?:design|file|proto)/([A-Za-z0-9]+)")
FIGMA_NODE_ID = re.compile(r"[?&]node-id=([0-9]+[-:][0-9]+)")
HTML_TAG = re.compile(r"<[^>]+>")
HTML_ENTITIES = (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&"))


def plain_text(value):
    """A Xurrent field can hold HTML. Turn it into lines, because a label and its link share a line."""
    text = HTML_TAG.sub("\n", str(value))
    for entity, character in HTML_ENTITIES:
        text = text.replace(entity, character)
    return text


def collect_text(value, found=None):
    """Every string anywhere in the request payload, so a link is found whatever field holds it."""
    found = [] if found is None else found
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            collect_text(item, found)
    elif isinstance(value, list):
        for item in value:
            collect_text(item, found)
    return found


def clean_label(text):
    """The words before the link, with the markdown link scaffolding taken off: a line reads
    `Runbook List ...: [https://...](https://...)`, and the label is everything before the bracket."""
    label = re.sub(r"[\[\]()]", " ", text)
    label = re.sub(r"\s+", " ", label).strip()
    return label.strip(" -*:").strip()


def design_links_in(text):
    """A labelled Figma link: the words before the link on its line say what the design is."""
    links, seen = [], set()
    for line in plain_text(text).splitlines():
        for url in FIGMA_URL.findall(line):
            url = url.rstrip(".,;")
            if url in seen:
                continue
            seen.add(url)
            label = clean_label(line[:line.index(url)])
            file_key = FIGMA_FILE_KEY.search(url)
            node = FIGMA_NODE_ID.search(url)
            node_id = node.group(1).replace("-", ":") if node else None
            links.append({"label": label or "unlabelled", "url": url,
                          "file_key": file_key.group(1) if file_key else None, "node_id": node_id})
    return links


def design_links(arguments):
    """The labelled Figma links in a request. Feed it the request JSON:

        .claude/bin/xurrent-api "/requests/<id>?fields=custom_fields" | gate.py design-links
    """
    source = argument_value(arguments, "--file")
    raw = Path(source).read_text() if source else sys.stdin.read()
    try:
        payload = json.loads(raw)
        pieces = collect_text(payload)
    except json.JSONDecodeError:
        pieces = [raw]
    links, seen = [], set()
    for piece in pieces:
        for link in design_links_in(piece):
            if link["url"] not in seen:
                seen.add(link["url"])
                links.append(link)
    if "--json" in arguments:
        print(json.dumps(links, indent=2))
        return
    if not links:
        print("design-links: no Figma link in the request")
        return
    for link in links:
        print(f"{link['label']}\n    file {link['file_key']}  node {link['node_id']}\n    {link['url']}\n")


# -------------------------------------------------------------- deviations

def deviations_directory(root):
    return Path(root) / DEVIATIONS_PATH


def read_deviations(root):
    directory = deviations_directory(root)
    if not directory.is_dir():
        return []
    records = [read_json(path) for path in sorted(directory.glob("*.json"))]
    return [record for record in records if record]


def parse_region(text):
    """`x,y,width,height` in page pixels: the area of the design the build does not follow."""
    if not text:
        return None
    parts = [piece.strip() for piece in text.split(",")]
    if len(parts) != 4 or not all(piece.lstrip("-").isdigit() for piece in parts):
        raise ValueError("--region wants four whole numbers: x,y,width,height")
    x, y, width, height = (int(piece) for piece in parts)
    if width <= 0 or height <= 0:
        raise ValueError("--region width and height must be above zero")
    return {"x": x, "y": y, "width": width, "height": height}


def deviation_problems(record):
    problems = []
    if not record["id"] or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", record["id"]):
        problems.append("--id wants a lower case slug, for example no-avatar-column")
    if record["kind"] not in DEVIATION_KINDS:
        problems.append(f"--kind must be one of {', '.join(DEVIATION_KINDS)}")
    if not record["summary"]:
        problems.append("--summary wants one line saying what differs")
    if not record["reason"]:
        problems.append("--reason wants one line saying why")
    if record["decided_by"] not in DEVIATION_DECIDERS:
        problems.append(f"--decided-by must be one of {', '.join(DEVIATION_DECIDERS)}")
    if record["decided_by"] == "user":
        problems += owner_decision_problems(record)
    return problems


def owner_decision_problems(record):
    if record["region"] and not record["evidence_after"]:
        return ["--region excuses a difference figma-compare reported, and it has reported none in this worktree. "
                "Run figma-compare first, then record the owner's answer to what it reports."]
    problem = owner_evidence_problem(record["evidence"], Path(record["worktree"]), after=record["evidence_after"],
                                     phone=True)
    return [problem + " Only a decision the owner made lets a mismatch pass."] if problem else []


def figma_mismatch_path(root):
    return proof_directory(root) / FIGMA_MISMATCH_FILE_NAME


def design_key(design, design_path):
    """A design's own identity, its frame id, so the same frame saved at another path closes its window. A design
    extracted before the frame id was recorded is known by its path."""
    frame_id = (design.get("frame") or {}).get("id")
    return f"frame {frame_id}" if frame_id else str(Path(design_path).resolve())


def record_mismatch(root, key, differs):
    """When figma-compare first reported the difference a deliberate one answers, per design frame. A compare of
    that frame that matches ends its window, so words typed before an earlier, fixed difference cannot answer a
    new one; a match on another frame leaves this one's window alone. A file written before windows were kept per
    frame holds one `first_mismatch_at` for every design, and the first compare after it starts the file again."""
    windows = mismatch_windows(root)
    if LEGACY_MISMATCH_KEY in windows:
        windows = {}
    if not differs:
        windows.pop(key, None)
    else:
        windows.setdefault(key, now_iso())
    write_json(figma_mismatch_path(root), windows)


def latest_mismatch_at(root):
    """The newest window still open in this worktree. A deviation does not say which design it answers, so the
    owner's words must come after every difference still open: words about the list cannot answer the detail.
    A file from before windows were kept per frame counts as one window, open for every design."""
    windows = mismatch_windows(root)
    return max(windows.values()) if windows else None


def mismatch_windows(root):
    """One window per design frame, keyed by design_key, as figma-compare last wrote them."""
    windows = read_json(figma_mismatch_path(root), default={})
    return windows if isinstance(windows, dict) else {}


def deviation(arguments):
    """Record one deliberate difference between the design or the spec and the build."""
    root = require_repository("deviation")
    problems = []
    try:
        region = parse_region(argument_value(arguments, "--region"))
    except ValueError as error:
        region = None
        problems.append(str(error))
    record = {"id": argument_value(arguments, "--id"), "kind": argument_value(arguments, "--kind"),
              "summary": argument_value(arguments, "--summary"), "reason": argument_value(arguments, "--reason"),
              "source": argument_value(arguments, "--source"), "region": region,
              "decided_by": argument_value(arguments, "--decided-by", "claude"),
              "evidence": argument_value(arguments, "--evidence"), "recorded_at": now_iso(), "worktree": str(root)}
    # A record with a region excuses what figma-compare reported inside it, so the owner's words must come
    # after that report, whatever the record's kind.
    record["evidence_after"] = latest_mismatch_at(root) if region else None
    problems += deviation_problems(record)
    if problems:
        refuse("deviation", problems)
    write_json(deviations_directory(root) / f"{record['id']}.json", record, mode=PRIVATE_FILE_MODE)
    weight = ("a design mismatch inside it may pass" if record["decided_by"] == "user"
              else "it does not let any mismatch pass")
    print(f"deviation: {record['id']} recorded ({record['decided_by']} decided, {weight})")


def deviations_section(root):
    records = read_deviations(root)
    if not records:
        return []
    lines = ["### Deliberate differences from the design or the spec", ""]
    for record in records:
        where = f" `{record['source']}`" if record.get("source") else ""
        region = record.get("region")
        area = f" (area {region['x']},{region['y']} {region['width']}x{region['height']})" if region else ""
        lines.append(f"- **{record['summary']}**{where}{area}")
        decision = ("The user decided this." if record["decided_by"] == "user"
                    else "Claude recorded this. It is a note, and it lets no mismatch pass.")
        lines.append(f"  {record['reason'].rstrip('.')}. {decision}")
        if record.get("evidence"):
            lines.append(f"  Evidence: {record['evidence']}")
    lines.append("")
    return lines


# ------------------------------------------------------------------ guard

def guard():
    """PreToolUse hook. Prints a deny decision when a tool call would skip or weaken the gate; silent otherwise.
    Deny rules cover the prefix-shaped commands; this covers substrings and paths anywhere in the command. A guard
    that crashes refuses: Claude Code runs the tool when its hook fails."""
    payload = read_json_stdin()
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    try:
        reason = None
        if tool_name == "Bash":
            reason = guard_bash(tool_input.get("command") or "", payload.get("cwd"))
        elif tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            reason = guard_path(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    except Exception as error:  # noqa: BLE001 - a command the guard cannot read is a command it refuses
        reason = f"the guard could not read this command ({type(error).__name__}), so it refuses it."
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": f"gate guard: {reason}"}}))


def guard_bash(command, cwd=None, depth=0):
    reason = guard_ready(command)
    if reason:
        return reason
    for token in GUARD_FORBIDDEN_ANYWHERE:
        if token in command:
            return f"`{token}` skips or changes the gate. Only the user can do that, from their own shell (`!` prefix)."
    if REFLOG_ACTION_SETTING.search(command):
        return "setting `GIT_REFLOG_ACTION` makes a commit look replayed by a rebase, so it keeps a phase it never had."
    if re.search(r"\bgit\s+commit\b.*\s-n\b", command):
        return "`git commit -n` skips the commit hooks that stamp the proof and phase trailers."
    if re.search(r"\bgit\s+config\b.*\bhooks", command):
        return "changing the hooks configuration disables the gate."
    if depth > MAX_GUARD_NESTING:
        return f"the command nests shells more than {MAX_GUARD_NESTING} deep, deeper than the guard reads."
    scan = scan_shell(command)
    variables = {}
    segments = [(segment, segment_words(segment, variables)) for segment in scan.segments]
    # What a shell runs from a heredoc, `bash -c`, `eval`, `find -exec` or `$(...)` is a command of its own,
    # judged the same way, from the directory of the segment that runs it.
    nested, interpreter_bodies = list(scan.substitutions), []
    for heredoc in scan.heredocs:
        readers = [words for _segment, words in heredoc_readers(scan, segments, heredoc) if words]
        programs = [Path(words[0]).name for words in readers]
        if any(INTERPRETER_NAME.match(name) for name in programs):
            interpreter_bodies.append(heredoc.body)
        # A body fed to a program that writes files (patch, `git apply`, tee, dd) can carry a protected path of
        # its own, which the segment's own words never name. A body whose tag was never closed is not data either.
        if any(writes_files(words) for words in readers):
            if any(path in heredoc.body for path in GUARD_WRITE_PROTECTED):
                return "feeding a protected path into a program that writes files forges or weakens the gate."
        if not heredoc.terminated or any(runs_heredoc_as_commands(name) for name in programs):
            nested.append((heredoc.segment, heredoc.body))
    if inline_program_writes_state(interpreter_bodies, segments):
        return ("an inline program that writes the gate's state forges it, whatever the path looks like. Read the "
                "state with the Read tool, `json.load`, or a gate.py subcommand (`trace`, `units`, `checks`).")
    directory, directories = Path(cwd or os.getcwd()), []
    # A heredoc is text the raw check read only when bash leaves it as typed: a quoted tag, or a body with no
    # expansion in it.
    heredoc_fed = {heredoc.segment for heredoc in scan.heredocs
                   if heredoc.terminated and (heredoc.quoted or not re.search(r"[$`]", heredoc.body))}
    for index, (segment, words) in enumerate(segments):
        directories.append(directory)
        nested_texts = shell_program_text(words) + [shlex.join(block) for block in find_exec_blocks(words)]
        nested += [(index, text) for text in nested_texts]
        literal_stdin = index in heredoc_fed and stdin_is_the_heredoc(segment.split())
        reason = (guard_program(words)
                  or guard_loop_ready(words, literal_stdin, fed_by_xargs(segment), bool(re.search(r"\bgh\b", command)))
                  or guard_tmux(words, "tmux" in command))
        if reason:
            return reason
        directory = segment_directory(words, directory)
        reason = guard_segment(segment, words, directory)
        if reason:
            return reason
    for index, program in nested:
        reason = guard_bash(program, str(directories[index] if index < len(directories) else directory), depth + 1)
        if reason:
            return reason
    return None


class Heredoc(NamedTuple):
    segment: int
    quoted: bool
    terminated: bool
    body: str


class ShellScan(NamedTuple):
    segments: list
    pipes: list
    heredocs: list
    substitutions: list


def heredoc_readers(scan, segments, heredoc):
    """The commands a heredoc's body reaches: the one that opens it and every one it pipes into after that.
    `cat <<'EOF' | python3 -` feeds python as surely as `python3 - <<EOF`, and the `cd` before `&&` reads nothing."""
    last = heredoc.segment
    while last < len(scan.pipes) and scan.pipes[last]:
        last += 1
    return segments[heredoc.segment:last + 1]


def read_heredoc_tag(command, index):
    """The delimiter word of a heredoc opened at `index`, read as bash reads it: a `-` strips tabs, quotes and
    backslashes are removed and mark the body as literal, and the word runs to the first unquoted blank or
    operator. Returns (tag, strip_tabs, quoted, end_index); tag is empty when nothing follows `<<`."""
    position = index + 2
    strip_tabs = command[position:position + 1] == "-"
    position += 1 if strip_tabs else 0
    while command[position:position + 1] in (" ", "\t"):
        position += 1
    letters, quoted = [], False
    while position < len(command) and command[position] not in " \t\n;|&<>()":
        character = command[position]
        if character in "'\"":
            quoted, position = True, position + 1
            while position < len(command) and command[position] != character:
                letters.append(command[position])
                position += 1
            position += 1
        elif character == "\\":
            quoted = True
            letters.append(command[position + 1:position + 2])
            position += 2
        else:
            letters.append(character)
            position += 1
    return "".join(letters), strip_tabs, quoted, position


def scan_shell(command):
    """One pass over a command line as bash reads it: the simple commands, with each redirect operator a word of
    its own and each command or process substitution a placeholder, the heredocs with the command that opens each,
    and the text of every substitution with the segment it sits in, to be judged as a command of its own. A `<<`
    inside quotes, after a `#`, in `<<<` or inside a substitution opens no heredoc in the line, and a separator
    inside quotes splits nothing: `grep -E 'a|gate.py (finalize|checks)'` is one command. `|&` is a pipe and a
    background `&`, so the command after it is judged."""
    segments, pipes, heredocs, substitutions = [], [], [], []
    current, pending, quote, index = [], [], None, 0

    def end_segment(piped=False):
        text = "".join(current).strip()
        if text:
            segments.append(text)
            pipes.append(piped)
        current.clear()

    while index < len(command):
        character, two = command[index], command[index:index + 2]
        if quote == "'":
            quote = None if character == "'" else quote
            current.append(character)
            index += 1
            continue
        if character == "\\":
            # bash removes a backslash-newline before it splits words, so `r\<newline>m` runs `rm`.
            if command[index + 1:index + 2] == "\n":
                index += 2
                continue
            current.append(command[index:index + 2])
            index += 2
            continue
        if quote is None and two == "$'":
            # ANSI-C quoting: a backslash escapes the next character until the closing quote. Keep the whole span
            # as one opaque word, so a `'` written as `\'` cannot close it early and let `; rm` split out.
            k = index + 2
            while k < len(command) and command[k] != "'":
                k += 2 if command[k] == "\\" else 1
            current.append(SUBSTITUTION_PLACEHOLDER)
            index = min(k + 1, len(command))
            continue
        if quote is None and command.startswith("$[", index):
            # old-style arithmetic `$[ 1<<2 ]`: a `<<` inside it opens no heredoc.
            close = command.find("]", index)
            close = len(command) if close == -1 else close
            current.append(command[index:close + 1])
            index = close + 1
            continue
        if two == "$(" or (quote is None and two in ("<(", ">(")):
            closing = matching_paren(command, index + 1)
            substitutions.append((len(segments), command[index + 2:closing]))
            current.append(SUBSTITUTION_PLACEHOLDER)
            index = closing + 1
            continue
        if character == "`":
            closing = closing_backtick(command, index)
            substitutions.append((len(segments), command[index + 1:closing]))
            current.append(SUBSTITUTION_PLACEHOLDER)
            index = closing + 1
            continue
        if quote == '"' or character in "'\"":
            quote = None if character == quote else (quote or character)
            current.append(character)
            index += 1
            continue
        # A `#` starts a comment only at a word boundary, and an escaped space (`a\ #`) is not one.
        word_start = (index == 0 or command[index - 1] in " \t\n;|&()") and command[index - 2:index - 1] != "\\"
        if character == "#" and word_start:
            end = command.find("\n", index)
            index = len(command) if end == -1 else end
            continue
        if command.startswith("<<<", index):
            current.append(" <<< ")
            index += 3
            continue
        if two == "<<":
            tag, strip_tabs, quoted, end = read_heredoc_tag(command, index)
            if tag:
                pending.append((tag, strip_tabs, quoted, len(segments)))
                current.append(f" {command[index:end]} ")
                index = end
                continue
        if two in ("&&", "||"):
            end_segment()
            index += 2
            continue
        redirect = REDIRECT_OPERATOR.match(command, index)
        if redirect:
            current.append(f" {redirect.group(0)} ")
            index = redirect.end()
            continue
        if character in ";|&\n":
            end_segment(piped=character == "|")
            index += 1
            if character == "\n":
                index = read_heredoc_bodies(command, index, pending, heredocs, substitutions)
            continue
        current.append(character)
        index += 1
    end_segment()
    read_heredoc_bodies(command, len(command), pending, heredocs, substitutions)
    return ShellScan(segments, pipes, heredocs, substitutions)


def read_heredoc_bodies(command, index, pending, heredocs, substitutions):
    """The bodies of the heredocs opened on the line that just ended, in order, each up to its own tag. bash
    expands `$(...)` and backticks in a body whose tag is unquoted, whatever program reads it."""
    for tag, strip_tabs, quoted, segment in pending:
        body, terminated = [], False
        while index < len(command):
            end = command.find("\n", index)
            end = len(command) if end == -1 else end
            text, index = command[index:end], end + 1
            if (text.lstrip("\t") if strip_tabs else text).rstrip(" \t") == tag:
                terminated = True
                break
            body.append(text)
        heredocs.append(Heredoc(segment, quoted, terminated, "\n".join(body)))
        if not quoted:
            substitutions.extend((segment, text) for text in expanded_substitutions("\n".join(body)))
    pending.clear()
    return min(index, len(command))


def expanded_substitutions(text):
    """The `$(...)` and backtick commands in text where quotes are literal, as in an unquoted heredoc body."""
    found, index = [], 0
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text.startswith("$(", index):
            closing = matching_paren(text, index + 1, quotes=False)
            found.append(text[index + 2:closing])
            index = closing + 1
        elif text[index] == "`":
            closing = closing_backtick(text, index)
            found.append(text[index + 1:closing])
            index = closing + 1
        else:
            index += 1
    return found


def matching_paren(text, open_index, quotes=True):
    """The index of the `)` that closes the `(` at open_index, or the end of the text when nothing closes it."""
    depth, quote, index = 0, None, open_index
    while index < len(text):
        character = text[index]
        if character == "\\" and quote != "'":
            index += 2
            continue
        if quote:
            quote = None if character == quote else quote
        elif quotes and character in "'\"":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def closing_backtick(text, index):
    position = index + 1
    while position < len(text) and text[position] != "`":
        position += 2 if text[position] == "\\" else 1
    return min(position, len(text))


def runs_heredoc_as_commands(name):
    """Whether a program a heredoc reaches runs the body as commands. A shell does, and so does any program the guard
    does not know, `source` and `.` among them. A data reader does not, and an interpreter's body is judged as a
    program."""
    if name in SHELL_NAMES:
        return True
    return not (name in DATA_READERS or name in GATE_WRAPPER_NAMES or INTERPRETER_NAME.match(name))


def shell_program_text(words):
    """The text a shell runs from `bash -c '...'` (or `-lc`), or `eval ...`."""
    if not words:
        return []
    if words[0] == "eval":
        return [" ".join(words[1:])]
    if Path(words[0]).name not in SHELL_NAMES:
        return []
    return flag_values(words, SHELL_COMMAND_FLAG.match)


def find_exec_blocks(words):
    """The commands `find ... -exec ... ;` runs, each as its words."""
    if not words or Path(words[0]).name != "find":
        return []
    blocks, block = [], None
    for word in words[1:]:
        if word in FIND_EXEC_ACTIONS:
            block = []
        elif block is not None and word in (";", "+"):
            blocks.append(block)
            block = None
        elif block is not None:
            block.append(word)
    return blocks + ([block] if block else [])


def flag_values(words, is_flag):
    """The word after each flag `is_flag` accepts: the program text of `bash -c '...'` or `ruby -e '...'`."""
    return [words[position + 1] for position, word in enumerate(words[:-1]) if is_flag(word)]


def inline_program_writes_state(heredoc_bodies, segments):
    """Judged on the program's own text, so a heredoc that writes a scratch file is not refused because the same
    line also runs gate.py or reads a findings file. Printing is not writing."""
    for program in inline_programs(heredoc_bodies, segments):
        if GATE_IMPORT.search(program):
            return True
        names_state = any(hint in program for hint in (*HOME_STATE_HINTS, *GUARD_WRITE_PROTECTED))
        if names_state and INLINE_WRITES.search(without_printing(program)):
            return True
    return False


def without_printing(program):
    """The program with its printing taken out: `sys.stdout.write(` and a `json.dump` whose stream is standard
    output. Only the call itself goes, so a file write elsewhere on the line, or inside the call, still counts."""
    program = CONSOLE_STREAM_WRITES.sub("", program)
    kept, start = [], 0
    for match in JSON_DUMP_CALL.finditer(program):
        if match.start() < start:
            continue
        closing = matching_paren(program, match.end() - 1)
        inside = program[match.end():closing]
        arguments = top_level_arguments(inside)
        if len(arguments) > 1 and STANDARD_STREAM.match(arguments[1]) and not INLINE_WRITES.search(inside):
            kept.append(program[start:match.start()])
            start = closing + 1
    kept.append(program[start:])
    return "".join(kept)


def top_level_arguments(text):
    """The arguments of a call, split at the commas that sit outside every bracket and string."""
    arguments, depth, quote, start = [], 0, None, 0
    for index, character in enumerate(text):
        if quote:
            quote = None if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "," and depth == 0:
            arguments.append(text[start:index])
            start = index + 1
    arguments.append(text[start:])
    return arguments


def inline_programs(heredoc_bodies, segments):
    """The text of every inline program on the line: the body of a heredoc fed to an interpreter, with or without
    the `-`, and the argument of an eval flag, attached in a cluster (`-Bc`, `-we`) or on its own (`-c`, `-e`,
    `--eval`, node's `-p`)."""
    programs = list(heredoc_bodies)
    for _segment, words in segments:
        if words and INTERPRETER_NAME.match(Path(words[0]).name):
            programs += eval_flag_values(words)
    return programs


def eval_flag_values(words):
    """The program text after each eval flag: `-c` for python and the shells, `-e`/`-E` for ruby and perl,
    `-p`/`--print` for node, and `--eval`, whether the flag is on its own or the last letter of a cluster."""
    values = []
    for position, word in enumerate(words[:-1]):
        cluster_flag = word.startswith("-") and not word.startswith("--") and word[-1:] in "ceEp"
        if word in ("--eval", "--print") or cluster_flag:
            values.append(words[position + 1])
    return values


def segment_words(segment, variables):
    """The words of one simple command as the shell reads them: quotes removed, the variables the line set
    expanded, and a leading subshell paren and environment assignments dropped, so the first word is the program
    that runs. A segment that only assigns is remembered."""
    try:
        words = shlex.split(segment.strip().lstrip("(").rstrip(")"))
    except ValueError:
        words = segment.strip("() ").split()
    words = [expand_word(word, variables) for word in words]
    assignments = list(itertools.takewhile(SHELL_ASSIGNMENT.match, words))
    if words and len(assignments) == len(words):
        variables.update(word.split("=", 1) for word in assignments)
    return program_words(words[len(assignments):])


def strip_leading_redirects(words):
    """Drop a redirect written before the program, so `2>/dev/null rm x` and `>out tee x` read as `rm` and
    `tee`. An operator takes the next word as its target unless that word is another operator."""
    while words:
        if words[0].isdigit() and words[1:2] and words[1] in ALL_REDIRECTS:
            words = words[1:]
        elif words[0] in ALL_REDIRECTS:
            words = words[2:] if words[1:2] and words[1] not in ALL_REDIRECTS else words[1:]
        else:
            break
    return words


def without_redirects(words):
    """The words with every redirect operator and its target removed, wherever they sit, so a redirect between
    the interpreter and its script (`python3 2>/dev/null gate.py surface`) cannot hide the script."""
    clean, index = [], 0
    while index < len(words):
        word = words[index]
        if word.isdigit() and words[index + 1:index + 2] and words[index + 1] in ALL_REDIRECTS:
            index += 1
            continue
        if word in ALL_REDIRECTS:
            index += 2 if words[index + 1:index + 2] and words[index + 1] not in ALL_REDIRECTS else 1
            continue
        clean.append(word)
        index += 1
    return clean


def program_words(words):
    """The words from the program that runs: a leading redirect goes, reserved words (`then`, `{`, `!`, `coproc`)
    go, a `function name` or `name()` header and a `case word in pattern)` header go, and wrappers (`env`,
    `/usr/bin/sudo -u x`, `timeout 60`, `xargs -n 1`, `exec -a x`) go with their own options and assignments.
    A wrapper is known by its base name, so a full path cannot hide it."""
    while True:
        words = strip_leading_redirects(words)
        if not words:
            return words
        first, name = words[0], Path(words[0]).name
        if first == "function" or first.endswith("()"):
            words = words[2:] if first == "function" else words[1:]
        elif first == "case":
            words = list(itertools.dropwhile(lambda word: not word.endswith(")"), words[1:]))[1:]
        elif first in SHELL_RESERVED_WORDS or first.endswith(")"):
            words = words[1:]
        elif name in SEGMENT_WRAPPERS:
            takes_value = WRAPPER_VALUE_OPTIONS.get(name, ())
            words = words[1:]
            while words and (words[0].startswith("-") or SHELL_ASSIGNMENT.match(words[0])):
                words = words[2:] if words[0] in takes_value else words[1:]
            words = words[SEGMENT_WRAPPERS[name]:]
        else:
            return words


def expand_word(word, variables):
    def replacement(match):
        name = match.group(1) or match.group(2)
        return variables.get(name, os.environ.get(name, match.group(0)))
    word = re.sub(r"\$(\w+)|\$\{(\w+)\}", replacement, word)
    name, equals, value = word.partition("=")
    if equals and SHELL_ASSIGNMENT.match(word):
        return f"{name}={os.path.expanduser(value)}"
    return os.path.expanduser(word)


def gate_script_position(words):
    """Where a segment names gate.py as the script it runs: first, or after a python interpreter and its option
    clusters as python reads them, or as a module (`-m gate`, `-mgate`, `-um gate`). A glob that matches the name
    (`gate.p?`) counts, and so does any directory."""
    position = 0
    if words and Path(words[0]).name.startswith("python"):
        position = 1
        while position < len(words) and words[position].startswith("-") and words[position] != "-":
            cluster = "" if words[position].startswith("--") else words[position][1:]
            for offset, flag in enumerate(cluster):
                rest = cluster[offset + 1:]
                if flag == "c":
                    return None
                if flag == "m" and rest:
                    return position if rest == "gate" else None
                if flag == "m":
                    return position + 1 if words[position + 1:position + 2] == ["gate"] else None
                if flag in PYTHON_VALUE_FLAGS:
                    position += 0 if rest else 1
                    break
            position += 1
    if position < len(words) and fnmatch.fnmatch("gate.py", Path(words[position]).name):
        return position
    return None


def gate_subcommand(words):
    """The subcommand a segment runs gate.py with: None when it runs no gate.py, "" when it names none."""
    words = without_redirects(words)
    script = gate_script_position(words)
    if script is None:
        return None
    subcommand = words[script + 1] if len(words) > script + 1 else ""
    return "" if subcommand.startswith("-") else subcommand


def guard_program(words):
    """A gate.py call runs only a subcommand the protocol lists, however it is spelled: `gate.py ''surface`,
    `S=surface; gate.py $S` and `gate.p? surface` each stored the session's text as the owner's words. A nested
    print session runs this worktree's hooks, so its prompt, which the session wrote, would be stored the same way."""
    subcommand = gate_subcommand(words)
    if subcommand and subcommand not in GUARD_ALLOWED_GATE_SUBCOMMANDS:
        return (f"`gate.py {subcommand}` is not one a session runs. A hook or git runs the hook subcommands, and a "
                f"session may run: {', '.join(GUARD_ALLOWED_GATE_SUBCOMMANDS)}.")
    if words and Path(words[0]).name in CLAUDE_PROGRAMS and PRINT_FLAGS & set(words[1:]):
        return ("a nested `claude -p` runs this worktree's hooks, so its prompt, which this session wrote, would be "
                "stored as the owner's words.")
    return None


def segment_directory(words, directory):
    """After `cd x` or `pushd x`, the later segments run in x, so their relative paths are read from there."""
    if len(words) < 2 or words[0] not in DIRECTORY_CHANGERS:
        return directory
    return directory / words[1]


def full_path(word, directory):
    return os.path.normpath(directory / word)


def redirect_targets(words):
    """Where a segment redirects its output, read from its expanded words so `$d/x` is the path the line set. The
    scanner made each operator a word of its own, so a `>` inside a quoted program (`len(value) > 0`) is part of a
    longer word and redirects nothing. `/dev/null` and a copy into another stream (`2>&1`, `>&-`) write nothing."""
    targets = []
    for operator, target in zip(words, words[1:]):
        if operator not in OUTPUT_REDIRECTS or target == "/dev/null":
            continue
        if operator == ">&" and (target.isdigit() or target == "-"):
            continue
        targets.append(target)
    return targets


def glob_touches(protected, globs):
    """Whether a glob could name the protected path: it matches it directly (`.claude/proof/r*`), or its literal
    prefix before the first glob character already sits inside the protected path or holds it."""
    for glob in globs:
        if fnmatch.fnmatch(protected, glob):
            return True
        cut = min(glob.find(character) for character in "*?[" if character in glob)
        prefix = glob[:cut]
        if prefix and (prefix in protected or protected in prefix):
            return True
    return False


def guard_segment(segment, words, directory):
    if COMMENT_WRAPPER not in segment and any(pattern.search(segment) for pattern in GUARD_COMMENT_POSTING):
        return ("a pull request comment must go through `.claude/bin/pr-comment --pr N --title \"...\" --body-file <path>`, "
                "which stamps who posted it and folds the content into a collapsed block. A raw post is indistinguishable "
                "from one the account holder wrote.")
    if re.search(r"\bgh\s+pr\s+create\b", segment) and CREATE_WRAPPER not in segment \
            and any(pattern.search(segment) for pattern in GUARD_DESCRIPTION_WRITING):
        return ("a pull request is not created with a description. Use "
                "`.claude/bin/pr-create --title \"...\" --request <id>`, which opens the draft with one link "
                "and no prose, then add `## Phase 1` with `.claude/bin/pr-phase`, which takes a diagram. "
                "A body written at creation escaped every check, which is how a description grew to several "
                "hundred lines nobody reads.")
    if DESCRIPTION_WRAPPER not in segment and any(pattern.search(segment) for pattern in GUARD_DESCRIPTION_WRITING):
        return ("the pull request description must be changed through "
                "`.claude/bin/pr-phase --pr N --phase P --body-file <path>`, which reads the live description and "
                "splices in that one phase section. Writing a whole body deletes every section you did not retype, "
                "including the user's own edits, and GitHub keeps no revision history to restore them from.")
    # Every path the segment names, written out in full, so a protected path cannot hide behind `cd`, `pushd`,
    # a relative redirect, `$HOME`, `~`, a variable the line set, or a glob (`.claude/proof/r*`). A bare file
    # name inside a protected directory counts as well.
    written_out = [full_path(word, directory) for word in words]
    globs = [word for word in [*words, *written_out] if any(character in word for character in "*?[")]
    spelled = [segment, str(directory), *written_out]
    touched = [path for path in GUARD_WRITE_PROTECTED
               if any(path in text for text in spelled) or glob_touches(path, globs)]
    # Removing or emptying a directory that holds a protected path destroys the gate's evidence. Creating or
    # writing a file next to it does not, so the ancestor rule is only for a program that removes a tree.
    if removes_tree(words):
        touched += [path for path in GUARD_PROTECTED_ANCESTORS
                    if any(text == path or text.endswith(f"/{path}") for text in written_out)]
    paths_written = [*written_out, *(full_path(target, directory) for target in redirect_targets(words))]
    if any(Path(text).name in GIT_STATE_SEGMENTS and ".git" in Path(text).parts for text in paths_written):
        return "writing git's replay or merge state by hand would make a new commit look like git's own."
    if not touched:
        return None
    for target in redirect_targets(words):
        written = full_path(target, directory)
        if any(path in written for path in GUARD_WRITE_PROTECTED):
            return f"redirecting into `{target}` would forge or weaken the gate."
    if guard_allowed_invocation(words):
        return None
    if writes_files(words):
        return f"writing under `{touched[0]}` would forge or weaken the gate. Read it if you need to; never write it."
    return None


def writes_files(words):
    """Whether the segment's program writes files: `rm`, `tee`, `dd of=`, `sed -i`, `curl -o`, `git checkout`,
    `find -delete`. Judged by the program, so `git add` and `yarn install` write nothing here, and neither does a
    quoted message that holds "form"."""
    words = program_words(without_redirects(words))
    if not words:
        return False
    program = Path(words[0]).name
    if program in IN_PLACE_EDITORS:
        return in_place_edit(program, words) or (program in ("sed", "gsed") and sed_writes(words))
    if program in AWK_PROGRAMS:
        return any("system(" in word or ">" in word for word in words[1:])
    if program in DOWNLOAD_OUTPUT_FLAGS:
        return download_writes(program, words)
    if program == "git":
        return git_subcommand(words) in GIT_WRITE_SUBCOMMANDS
    if program == "find":
        return "-delete" in words or any(not exec_block_reads(block) for block in find_exec_blocks(words))
    return program in WRITE_PROGRAMS


def exec_block_reads(block):
    """Whether a `find -exec` block only reads: its program, past any wrapper, is on the read-only list and is
    not itself a writer. A shell or an interpreter is never a reader here, because its own text can write."""
    words = program_words(without_redirects(block))
    if not words:
        return True
    return Path(words[0]).name in FIND_EXEC_READERS and not writes_files(words)


def in_place_edit(program, words):
    """Whether a `sed`, `perl` or `ruby` call edits a file in place. For sed, `-i` and macOS `-I` both do; for
    perl and ruby, a value-taking flag earlier in the cluster swallows the letters after it, so `-rtime` and
    `-Mstrict` are reads, while `-pi`, `-0pi` and `-i.bak` write."""
    for word in words[1:]:
        if word == "--in-place" or word.startswith("--in-place="):
            return True
        if not word.startswith("-") or word.startswith("--"):
            continue
        cluster = word[1:]
        if program in ("sed", "gsed"):
            if any(letter in "iI" for letter in cluster):
                return True
            continue
        position = 0
        while position < len(cluster):
            letter = cluster[position]
            if letter == "i":
                return True
            if letter == "0":
                position += 1
                while position < len(cluster) and cluster[position].isdigit():
                    position += 1
                continue
            if letter in PERL_VALUE_FLAGS:
                break
            position += 1
    return False


SED_WRITE_COMMAND = re.compile(r"(?:^|[;{\s])[wW]\s|(?:^|[;{\s])e(?:\s|$)")


def sed_writes(words):
    """A sed script with a `w`, `W` or `e` command writes a file or runs a program."""
    return any(SED_WRITE_COMMAND.search(word) for word in words[1:] if not word.startswith("-"))


def download_writes(program, words):
    """`curl -o f`, `wget -O f`, and the same letter inside a single-dash cluster (`curl -sSLo f`)."""
    flags = DOWNLOAD_OUTPUT_FLAGS[program]
    letters = "oO"
    for word in words[1:]:
        if word.partition("=")[0] in flags:
            return True
        if word.startswith("-") and not word.startswith("--") and any(letter in word[1:] for letter in letters):
            return True
    return False


def removes_tree(words):
    """Whether the program empties or deletes a directory, so emptying one that holds gate state destroys it:
    `rm -rf`, `rmdir`, `shred`, `git clean`, `find ... -delete`."""
    if not words:
        return False
    program = Path(words[0]).name
    if program in ("rm", "rmdir", "shred"):
        return True
    if program == "git":
        return git_subcommand(words) == "clean"
    if program == "find":
        return "-delete" in words
    return False


def git_subcommand(words):
    words = without_redirects(words)
    position = 1
    while position < len(words) and words[position].startswith("-"):
        position += 2 if words[position] in GIT_VALUE_OPTIONS else 1
    return words[position] if position < len(words) else None


def guard_allowed_invocation(words):
    """A gate.py subcommand the protocol lists, and the wrapper and daemon commands it asks for."""
    subcommand = gate_subcommand(words)
    if subcommand is not None:
        return subcommand == "" or subcommand in GUARD_ALLOWED_GATE_SUBCOMMANDS
    for allowed in GUARD_ALLOWED_COMMANDS:
        parts = allowed.split()
        if len(words) >= len(parts) and words[0].endswith(parts[0]) and words[1:len(parts)] == parts[1:]:
            return True
    return False


READY_COMMAND = re.compile(r"\bgh\s+pr\s+ready\b")
READY_MUTATION = re.compile(r"markPullRequestReadyForReview")
# ticketd starts every loop session with this variable set to 1, and the hooks of that session inherit it.
LOOP_SESSION_VARIABLE = "TICKETD_SESSION"
DRAFT_MUTATIONS = re.compile(r"markPullRequestReadyForReview|convertPullRequestToDraft|createPullRequest|revertPullRequest")
# In a loop session the raw command text is searched too, so a program written inline (python -c, ruby -e, awk)
# or a heredoc body cannot carry these past the word check.
LOOP_DRAFT_TEXT = re.compile(r"\bgh\s+(?:pr\s+(?:ready|create|new|revert)|alias\s+(?:set|import))\b|"
                             + DRAFT_MUTATIONS.pattern)
# The gh subcommands a loop session never runs: draft state, pull request creation (`new` is gh's alias for
# `create`), and aliases that could rename either.
LOOP_REFUSED_SUBCOMMANDS = {"pr": ("ready", "create", "new", "revert"), "alias": ("set", "import")}
# Options that take the next word as their value, so a value is never read as a subcommand, an endpoint or a method.
GH_VALUE_OPTIONS = ("-R", "--repo", "-X", "--method", "-f", "-F", "--field", "--raw-field", "-H", "--header",
                    "--input", "-q", "--jq", "-t", "--template", "--hostname", "-p", "--preview", "--cache")
GH_API_FIELD_OPTIONS = ("-f", "-F", "--field", "--raw-field", "--input")
# Only these options read a value from a file (`key=@path`); `-f` sends `@` literally.
GH_API_FILE_FIELD_OPTIONS = ("-F", "--field")
# `-i` is the only true-or-false short option of `gh api`, so only a run of it can come before a short option
# that takes a value: `-iF query=@file` is `-i -F query=@file`.
GH_API_SHORT_FLAGS = "i"
PULLS_COLLECTION = re.compile(r"(?:^|/)pulls/?(?:[?#].*)?$")
# gh fills these placeholders in an endpoint itself; they are not shell words.
GH_ENDPOINT_PLACEHOLDERS = re.compile(r"\{(?:owner|repo|branch)\}")
# A GraphQL query whose whole text is a shell variable, which the guard cannot read. A query that only uses
# GraphQL variables (`query($owner:String!)`) does not start with `$`.
VARIABLE_QUERY_FIELD = re.compile(r"^query=\$")
LOOP_READY_REASON = ("in a ticketd loop session only ticketd moves a pull request in or out of draft, after the "
                     "user's signed \"ready\" from the phone. Do not run `gh pr ready`, a draft mutation or a gh "
                     "alias, and open a pull request only with `.claude/bin/pr-create`, which opens a draft. A gh api "
                     "request goes inline, never from a file or from --input. End your turn: ticketd pings the user "
                     "when CI is green.")


def in_loop_session():
    return os.environ.get(LOOP_SESSION_VARIABLE) == "1"


def unreadable(word):
    """A word the shell fills only when it runs: a substitution, or a variable the guard has no value for."""
    return SUBSTITUTION_PLACEHOLDER in word or "$" in word


def fed_by_xargs(segment):
    """Whether xargs runs this segment's program, so part of its words arrive only when it runs."""
    try:
        words = shlex.split(segment.strip().lstrip("(").rstrip(")"))
    except ValueError:
        words = segment.split()
    return any(Path(word).name == "xargs" for word in words)


def gh_positionals(words):
    """The words of a `gh` call that are not options, so `gh pr -R 4me/ipaas ready 5` reads `pr ready 5`."""
    positionals, position = [], 1
    while position < len(words):
        word = words[position]
        position += 2 if word in GH_VALUE_OPTIONS else 1
        if not word.startswith("-"):
            positionals.append(word)
    return positionals


def guard_loop_ready(words, literal_stdin=False, xargs=False, names_gh=True):
    """The gh calls a loop session never makes, read from words that are already unquoted, expanded and
    stripped of wrappers, so `gh pr "ready"` and `env gh pr -R x ready` are the same call. A subcommand the
    shell fills only at run time is refused, because the guard cannot read it, and so is a gh call that xargs
    completes. `literal_stdin` says a heredoc in this command, whose text the raw check read, is the call's
    standard input. `names_gh` says the command text names gh, so a program word the shell fills
    (`$(which gh)`) is judged as gh.

    The guard is a tripwire for ordinary spellings, not a wall. It does not read a script file the session wrote,
    a call built as a list inside an inline program, another HTTP client, a gh extension or an alias set before
    the session, or a variable that `export`, `declare` or `read` fills. ticketd watches the draft state of every
    loop pull request itself and undoes a change the user did not sign."""
    if not in_loop_session() or not words:
        return None
    words = without_redirects(words)
    program = Path(words[0]).name if words else ""
    if program != "gh" and not (names_gh and unreadable(program)):
        return None
    positionals = gh_positionals(words)
    group, command = (positionals + ["", ""])[:2]
    if unreadable(group):
        return LOOP_READY_REASON
    if xargs and (len(positionals) < 2 or not re.fullmatch(r"[a-z-]+", group) or not re.fullmatch(r"[a-z-]+", command)):
        return LOOP_READY_REASON
    refused = LOOP_REFUSED_SUBCOMMANDS.get(group, ())
    if refused and (command in refused or unreadable(command)):
        return LOOP_READY_REASON
    if DRAFT_MUTATIONS.search(" ".join(words)):
        return LOOP_READY_REASON
    if group == "api" and (api_reads_a_file(words, literal_stdin) or api_creates_a_pull_request(words)):
        return LOOP_READY_REASON
    return None


def api_reads_a_file(words, literal_stdin=False):
    """A `gh api` call whose request text the guard cannot read: `--input`, a `-F` value read from a file, standard
    input that no literal heredoc feeds, a whole word or a field value the shell fills by substitution, or a query
    held in a shell variable."""
    for index, word in enumerate(words):
        if word == "--input" or word.startswith("--input=") or word.startswith(SUBSTITUTION_PLACEHOLDER):
            return True
        field = option_value(words, index, GH_API_FIELD_OPTIONS)
        if field is not None and (SUBSTITUTION_PLACEHOLDER in field or VARIABLE_QUERY_FIELD.match(field)):
            return True
        value = option_value(words, index, GH_API_FILE_FIELD_OPTIONS)
        if value is not None and "=@" in value and not (value.endswith("=@-") and literal_stdin):
            return True
    return False


def option_value(words, index, options):
    """The value of the option at `index` when it is one of `options`, or None when the word is not that option.
    It reads every spelling gh accepts: `-F x`, `-Fx`, `-F=x`, `-iF x`, `--field x` and `--field=x`."""
    word = words[index]
    following = words[index + 1] if index + 1 < len(words) else ""
    for option in options:
        if option.startswith("--"):
            if word == option:
                return following
            if word.startswith(option + "="):
                return word[len(option) + 1:]
            continue
        short = re.fullmatch(rf"-[{GH_API_SHORT_FLAGS}]*{re.escape(option[1:])}(.*)", word, re.DOTALL)
        if short:
            return short.group(1).removeprefix("=") or following
    return None


def api_creates_a_pull_request(words):
    """A `gh api` write to the pulls collection, or to an endpoint the shell fills at run time, which can open a
    pull request that need not be a draft. gh's own `{owner}` and `{repo}` placeholders are not shell words."""
    endpoints = [GH_ENDPOINT_PLACEHOLDERS.sub("", word) for word in gh_positionals(words)[1:]]
    return any(PULLS_COLLECTION.search(word) or unreadable(word) for word in endpoints) and api_writes(words)


def api_writes(words):
    """gh sends a POST when a field is given and no method is."""
    method = api_method(words)
    has_field = any(option_value(words, index, GH_API_FIELD_OPTIONS) is not None for index in range(len(words)))
    return method not in (None, "GET") or (method is None and has_field)


def api_method(words):
    """The method of a `gh api` call, or None when it is not given. gh keeps the last one, and the value of another
    option (`--jq -XGET`) is never a method."""
    method, position = None, 1
    while position < len(words):
        word = words[position]
        value = option_value(words, position, ("-X", "--method"))
        if value is not None:
            method = value.upper()
            position += 2 if word in ("-X", "--method") or re.fullmatch(rf"-[{GH_API_SHORT_FLAGS}]*X", word) else 1
        else:
            position += 2 if word in GH_VALUE_OPTIONS else 1
    return method


TMUX_READ_COMMANDS = frozenset({"display-message", "display", "list-windows", "lsw", "list-panes", "lsp",
                                "list-sessions", "ls", "capture-pane", "capturep", "has-session", "has",
                                "show-options", "show"})
# The tmux options that take the next word as their value, also at the end of a group such as `-uT`.
TMUX_VALUE_OPTION_LETTERS = "LSfcT"
# A format that runs a shell command (`#(...)`) or applies a modifier (`#{E:...}`); a plain `#{name}` only reads.
TMUX_PLAIN_FORMAT = re.compile(r"##|#\{\w+\}")
TMUX_RUNNING_FORMAT = re.compile(r"#[({]")
TMUX_REASON = ("a session only reads tmux (display-message, the list commands, capture-pane, has-session, "
               "show-options) with plain #{name} formats. Typing into a pane or a paste buffer writes a prompt that "
               "the gate stores as the owner's words, and a #() format runs a shell command.")


def guard_tmux(words, names_tmux=False):
    """tmux can type into the session's own pane, and the gate would store that text as the owner's words:
    send-keys under any prefix or alias, a paste buffer, run-shell, a command file, control mode, or a `#()`
    format inside a command that otherwise reads. So a session runs only the commands that read, with plain
    formats. `names_tmux` says the command text names tmux, so a program word the shell fills (`$(which tmux)`)
    is judged as tmux."""
    if not words:
        return None
    program = Path(words[0]).name
    if program != "tmux" and not (names_tmux and unreadable(program)):
        return None
    if any(TMUX_RUNNING_FORMAT.search(TMUX_PLAIN_FORMAT.sub("", word)) for word in words[1:]):
        return TMUX_REASON
    position = 1
    while position < len(words) and words[position].startswith("-"):
        if "C" in words[position]:
            return TMUX_REASON
        position += 2 if words[position][-1] in TMUX_VALUE_OPTION_LETTERS else 1
    commands, expecting = [], True
    for word in words[position:]:
        if expecting:
            commands.append(word)
            expecting = False
        if word.endswith(";"):
            expecting = True
    if not commands or any(command not in TMUX_READ_COMMANDS for command in commands):
        return TMUX_REASON
    return None


def stdin_is_the_heredoc(words):
    """No redirect in the segment replaces standard input (`<`, `0<`, `<>`, `<&`, a here-string), and no heredoc
    feeds a descriptor other than 0. The words are the raw segment split on spaces, quotes still in them."""
    for position, word in enumerate(words):
        if re.match(r"0?<(?!<)|\d*<>|<<<|[1-9]\d*<<", word):
            return False
        if word.startswith("<<") and position and re.fullmatch(r"[1-9]\d*", words[position - 1]):
            return False
    return True


def guard_ready(command):
    """Taking the pull request out of draft is the moment it is offered to a reviewer.

    A session shipped a whole page and wrote "Never seen rendered: all of it", then made it
    reviewable anyway, because the live check was a checkbox in a skill and nothing read it."""
    if in_loop_session() and LOOP_DRAFT_TEXT.search(command):
        return LOOP_READY_REASON
    if not (READY_COMMAND.search(command) or READY_MUTATION.search(command)):
        return None
    root = repository_root(os.getcwd())
    if root is None:
        return None
    return ready_guard_reason(root)


def guard_path(file_path):
    expanded = str(Path(file_path).expanduser())
    for path in GUARD_WRITE_PROTECTED:
        if path in expanded:
            return f"`{expanded}` is part of the gate; the model never writes it."
    if expanded.endswith("settings.local.json") or expanded.endswith(".gate-skip-once"):
        return f"`{expanded}` holds the fence around the gate; the model never writes it."
    return undecided_comment_reason(expanded)


def undecided_comment_reason(expanded):
    """No code changes while the user still owes a decision on a review comment.

    The gate cannot tell which comment an edit belongs to, so it holds every code edit until every
    comment has a mark. The user lifts this by marking the diagram; the gate never decides for them.
    """
    root = repository_root(nearest_directory(expanded))
    relative = relative_to(root, expanded) if root is not None else None
    if relative is None or not (is_code_path(relative) or is_spec_path(relative) or is_connector_fixture(relative)):
        return None
    return comment_guard_reason(root)


def nearest_directory(expanded):
    """`git -C` needs a directory. A Write names a file that may not exist yet, so walk up to the
    first directory that does."""
    candidate = Path(expanded).parent
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def relative_to(root, expanded):
    try:
        return str(Path(expanded).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return None


def fence_deny_rules():
    gate = str(Path(__file__).resolve().parent)
    # An absolute path rule is written `//Users/...`: one extra slash in front of the absolute path.
    in_worktree = [entry for entry in GUARD_WRITE_PROTECTED if entry.startswith(".claude/")]
    protected = [f"/{gate}/**", f"/{PHASED_DIRECTORY}/**", "**/.claude/settings.local.json", f"/{SKIP_TOKEN_FILE}",
                 f"/{SKIPS_LOG.parent}/**"]
    protected += [f"**/{entry}" if Path(entry).suffix else f"**/{entry}/**" for entry in in_worktree]
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
    registered = []
    for event, matcher, subcommand in FENCE_HOOKS:
        if ensure_hook(hooks, event, matcher, f"python3 {Path(__file__).resolve()} {subcommand}"):
            registered.append(event)
    if added or stale or registered:
        write_json(settings_path, settings)
        return [f"fenced {settings_path}: {len(added)} deny rule(s) added, {len(stale)} stale removed"
                + (f", hooks registered for {', '.join(registered)}" if registered else "")]
    return []


def ensure_hook(hooks, event, matcher, command):
    entries = hooks.setdefault(event, [])
    if any(hook.get("command") == command for entry in entries for hook in entry.get("hooks", [])):
        return False
    entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": command}]})
    return True


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
    actions += ensure_copy(root / LOCAL_PROTOCOL_FILE.name, LOCAL_PROTOCOL_FILE, refresh=True)
    actions += ensure_copy(root / LOCAL_SETTINGS_FILE, MAIN_REPOSITORY / LOCAL_SETTINGS_FILE)
    actions += ensure_serena_projects(root)
    actions += ensure_fence(root)
    actions += ensure_exclude_entries(root)
    if not quiet:
        print("\n".join(actions) if actions else f"{root}: already linked")
    return actions


def ensure_serena_projects(root):
    """Give the worktree its own Serena project for each Ruby project.

    Serena indexes one project per Ruby project, not one per repository, and it keys a project by
    absolute path. A worktree is a different path, so it starts with no project and the rule to call
    find_referencing_symbols cannot be followed there.

    Serena auto-generates a config for an unknown path, but it guesses the language servers. Copying
    the main checkout's project.yml keeps ruby and typescript, and the name stays unique per worktree
    so two worktrees never collide in the Serena registry. Only project.yml is copied: the cache and
    the memories belong to the checkout that built them.
    """
    if root == MAIN_REPOSITORY:
        return []
    actions = []
    for project in RUBY_PROJECTS:
        source = MAIN_REPOSITORY / project / ".serena" / "project.yml"
        destination = root / project / ".serena" / "project.yml"
        if not source.exists() or not (root / project).is_dir() or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serena_project_text(source.read_text(), f"ipaas-{project}-{root.name}"))
        actions.append(f"seeded {destination} for Serena")
    return actions


def serena_project_text(text, name):
    replaced, count = re.subn(r'(?m)^project_name:.*$', f'project_name: "{name}"', text, count=1)
    return replaced if count else f'project_name: "{name}"\n{text}'


def ensure_symlink(link, target):
    if link.is_symlink() and link.resolve() == target.resolve():
        return []
    if link.exists() and not link.is_symlink():
        return [f"kept {link}: a real file or directory is there, not replaced"]
    if link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return [f"linked {link} -> {target}"]


def ensure_copy(destination, source, refresh=False):
    if destination.is_symlink() or not source.exists() or source.resolve() == destination.resolve():
        return []
    if destination.exists():
        if not refresh or destination.read_bytes() == source.read_bytes():
            return []
        # The protocol file is owned by the gate and excluded from git, so a worktree copy that has
        # fallen behind the source would keep serving rules the user has already replaced. Keep the
        # replaced text, because nothing else holds it.
        backup = SKIPS_LOG.parent / "protocol" / f"{destination.name}.{now_iso().replace(':', '')}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, backup)
        shutil.copy2(source, destination)
        return [f"refreshed {destination} (previous kept at {backup})"]
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


def checks_in_use():
    return read_json(checks_in_use_path()) or {}


class ChecksLockTimeout(RuntimeError):
    """The checks lock was never taken, so the checks worktree was never touched."""


def checks_holder_gone(in_use):
    """Why the holder of the checks worktree cannot come back, or None while it can: the machine restarted, or
    its folder is no longer the top of a worktree."""
    if survived_a_restart(in_use):
        return "at the restart"
    source = in_use.get("source_root")
    if source:
        top = repository_root(source) if Path(source).is_dir() else None
        if top is None or real_path(top) != real_path(source):
            return f"when {source} stopped being a worktree"
    return None


def checks_in_use_refusal(root, in_use):
    """Why a caller in worktree `root` may not touch the checks worktree, or None when it may. The holder is
    the worktree that applied, compared as a real path, because every detached worktree reports the branch
    HEAD. `root` is None for a caller outside any PR worktree, which holds nothing."""
    if not in_use or checks_holder_gone(in_use):
        return None
    source = in_use.get("source_root")
    if root is not None and source and real_path(root) == real_path(source):
        return None
    return (f"the checks worktree is in use by {in_use.get('branch')} from {in_use.get('source_root')} since "
            f"{in_use.get('at')}; wait until gate.py checks status prints free, then run this again. Only a session "
            f"working in {in_use.get('source_root')} runs gate.py checks reset for it")


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
                    raise ChecksLockTimeout(f"the checks worktree stayed locked for {CHECKS_LOCK_TIMEOUT_SECONDS} seconds")
                time.sleep(5)
                waited += 5
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def migrations_in(root, base):
    return any(path.startswith(MIGRATIONS_DIRECTORY) for path in changed_files_all(root, base))


def apply_branch_to_checks(root):
    """Put the checks worktree into the exact state of root: its HEAD commit plus its working tree."""
    in_use = checks_in_use()
    refusal = checks_in_use_refusal(root, in_use)
    if refusal:
        raise RuntimeError(refusal)
    branch = current_branch(root)
    source = in_use.get("source_root")
    left = None if source and real_path(source) == real_path(root) else checks_holder_gone(in_use)
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    git(CHECKS_WORKTREE, "checkout", "--detach", "--force", "--quiet", head)
    git(CHECKS_WORKTREE, "reset", "--hard", "--quiet")
    git(CHECKS_WORKTREE, "clean", "-fd", "--quiet")
    # HEAD, not the merge-base: the checks worktree is already at this commit, so only the working tree
    # is missing. A branch-delta patch would apply the committed part a second time.
    patch = snapshot_patch(root, "HEAD")
    if patch.strip():
        git(CHECKS_WORKTREE, "apply", "--whitespace=nowarn", "-", input_text=patch)
    # The marker carries `migrations` forward on purpose: it is the only record that the test database was
    # migrated for a branch, and dropping it leaves the worktree migrated for a branch nobody checks now.
    migrations = migrations_in(root, diff_base(root)) or bool(in_use.get("migrations"))
    write_json(checks_in_use_path(), {"branch": branch, "source_root": str(root), "head": head,
                                      "patch_sha": patch_digest(patch), "migrations": migrations,
                                      "at": now_iso(), "boot": boot_time()})
    steps = [("apply", "ok", f"{branch} at {head[:10]} plus {len(patch.splitlines())} patch line(s) in {CHECKS_WORKTREE}")]
    if left:
        steps.insert(0, ("takeover", "ok", f"{in_use.get('branch')} from {in_use.get('source_root')} left the checks "
                                           f"worktree {left}; taking it over"))
    platform = CHECKS_WORKTREE / "platform"
    if migrations:
        steps.append(command_check("db:test:prepare", ["bundle", "exec", "rake", "db:test:prepare"], platform, environment=checks_test_environment()))
        steps.append(command_check("db:prepare", ["bin/rails", "db:prepare"], platform, environment=checks_environment()))
    steps.append(command_check("routes", ["bundle", "exec", "rake", "js:routes:typescript"], platform, environment=checks_test_environment()))
    return steps


def reset_checks_worktree(rebuild_dev_db=False):
    """Back to a clean origin/main. The test database is reloaded when the branch carried migrations;
    the development database is rebuilt only on request, because that reseeds the demo data."""
    in_use = checks_in_use()
    fetch_failure = None
    try:
        git(CHECKS_WORKTREE, "fetch", "--quiet", "origin", "main", timeout=CHECKS_FETCH_TIMEOUT_SECONDS)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        fetch_failure = "; ".join(str(error).strip().splitlines()[:2])
    git(CHECKS_WORKTREE, "checkout", "--detach", "--force", "--quiet", UPSTREAM_BRANCH)
    git(CHECKS_WORKTREE, "reset", "--hard", "--quiet")
    git(CHECKS_WORKTREE, "clean", "-fd", "--quiet")
    platform = CHECKS_WORKTREE / "platform"
    steps = []
    if fetch_failure:
        steps.append(("fetch", "skip", f"reset to the last fetched {UPSTREAM_BRANCH}: {fetch_failure}"))
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
    """With no subcommand, the status: six of the nine failed `checks` calls in the trace were a bare
    `checks`, asked by a session that wanted to know whether the worktree was free."""
    arguments = arguments or ["status"]
    if arguments[0] not in ("apply", "reset", "status"):
        print("usage: gate.py checks apply | reset [--rebuild-dev-db] | status")
        sys.exit(2)
    if not checks_ready():
        print(f"no checks worktree with a slot at {CHECKS_WORKTREE}; run gate.py setup-checks")
        sys.exit(1)
    if arguments[0] == "status":
        in_use = checks_in_use()
        head = git(CHECKS_WORKTREE, "rev-parse", "--short", "HEAD").stdout.strip()
        dirty = git(CHECKS_WORKTREE, "status", "--porcelain").stdout.strip()
        values = environment_file_values(CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE)
        print(f"{CHECKS_WORKTREE}: HEAD {head}, {'dirty' if dirty else 'clean'}, slot {values.get('IPAAS_WT_SLOT')}, web port {values.get('WEB_PORT')}")
        left = checks_holder_gone(in_use)
        if not in_use:
            print("free")
        elif left:
            print(f"marked in use by {in_use['branch']} from {in_use.get('source_root')} since {in_use['at']}, but "
                  f"its holder left {left}; "
                  "the next apply takes it over")
        else:
            print(f"in use by {in_use['branch']} from {in_use.get('source_root')} since {in_use['at']}")
        return
    root = repository_root(os.getcwd())
    caller = None if root is None or root.resolve() == CHECKS_WORKTREE.resolve() else root
    try:
        with checks_lock():
            if arguments[0] == "apply":
                if caller is None:
                    print("run gate.py checks apply from the PR worktree")
                    sys.exit(2)
                try:
                    steps = apply_branch_to_checks(caller)
                except RuntimeError as error:
                    print(f"checks apply refused: {error}")
                    sys.exit(1)
                print_steps(steps)
                values = environment_file_values(CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE)
                print(f"live check: cd {CHECKS_WORKTREE} && source {WORKTREE_ENVIRONMENT_FILE} && cd platform && bin/dev   then open http://127.0.0.1:{values.get('WEB_PORT')}")
                print("when done: gate.py checks reset  (add --rebuild-dev-db after exploratory migrations)")
                sys.exit(1 if any(status == "FAIL" for _, status, _ in steps) else 0)
            refusal = checks_in_use_refusal(caller, checks_in_use())
            if refusal:
                print(f"checks reset refused: {refusal}")
                sys.exit(1)
            try:
                steps = reset_checks_worktree(rebuild_dev_db="--rebuild-dev-db" in arguments)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"checks reset failed: {error}")
                sys.exit(1)
            print_steps(steps)
            sys.exit(1 if any(status == "FAIL" for _, status, _ in steps) else 0)
    except ChecksLockTimeout as error:
        print(f"checks {arguments[0]} refused: {error}")
        sys.exit(1)


# ----------------------------------------------------------------- setup

def setup():
    """Builds the gate worktree, its environment and its databases. It holds the gate lock, because
    `db:test:prepare` resets the databases a running proof uses, and a broken red run reads as a kill."""
    with gate_lock():
        build_gate_worktree()


def build_gate_worktree():
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
    """The commit the change is measured from: the merge-base with the pull request's own base
    branch, falling back to the upstream branch when there is no recorded base."""
    return memoized(("diff_base", str(root)), lambda: find_diff_base(root))


def find_diff_base(root):
    reference = base_reference(root)
    for candidate in (reference, UPSTREAM_BRANCH):
        completed = git(root, "merge-base", "HEAD", candidate, allow_exit_codes=(0, 1, 128))
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    return "HEAD"


# ------------------------------------------------------------- the proof base
# A pull request stacked on another one does not branch from main. Measuring its proof from the
# merge-base with main reverts the parent along with the change, so the declared example fails
# because the parent vanished rather than because the change matters, and the run passes having
# proved nothing. The proof measures from the pull request's own base branch instead.
#
# The base is never taken from a file the model can write. It is read from the pull request and
# cached by the gate, because a base the model chooses is a skip switch: set it to HEAD and every
# revert reverts nothing.

def base_cache_path(root):
    return root / PROOF_DIRECTORY / BASE_CACHE_NAME


def current_branch(root):
    return git(root, "rev-parse", "--abbrev-ref", "HEAD", allow_exit_codes=(0, 128)).stdout.strip()


def read_base_cache(root):
    """The cached base, only when it belongs to the branch that is checked out."""
    cache = read_json(base_cache_path(root))
    if not cache or cache.get("branch") != current_branch(root):
        return None
    return cache


def refresh_base_cache(root):
    """Ask the pull request what it is based on. One network call, and never from a hook: the
    callers are the proof launch and finalize, not Stop or the guard."""
    branch = current_branch(root)
    if not branch or branch == "HEAD":
        return None
    done = subprocess.run(["gh", "pr", "view", branch, "--json", "number,baseRefName,state"],
                          cwd=str(root), capture_output=True, text=True, env=github_environment())
    if done.returncode != 0:
        return read_base_cache(root)
    try:
        answer = json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        return read_base_cache(root)
    base_ref = answer.get("baseRefName")
    if not base_ref:
        return read_base_cache(root)
    cache = {"branch": branch, "base_ref": base_ref, "pr": answer.get("number"),
             "pr_state": answer.get("state"), "fetched_at": now_iso()}
    write_json(base_cache_path(root), cache)
    PROCESS_MEMO.clear()
    return cache


def reference_exists(root, reference):
    return git(root, "rev-parse", "--verify", "--quiet", reference, allow_exit_codes=(0, 1, 128)).returncode == 0


def base_reference(root):
    """Which ref the proof measures from. Local files only, because this runs in the Stop hook."""
    cache = read_base_cache(root)
    if not cache:
        return UPSTREAM_BRANCH
    candidate = f"origin/{cache['base_ref']}"
    return candidate if reference_exists(root, candidate) else UPSTREAM_BRANCH


def base_behind(root):
    """How many commits the base branch carries that this branch does not.

    Whether the base has landed on main is not the question. The proof resets to the merge-base
    with the base branch, so everything the parent already shipped is present in the red run and
    only this change is reverted. A parent that is still open changes nothing about that.

    What does matter is a base that has moved. The proof then measures against a parent that no
    longer exists, and the branch has to be redone on the newer one anyway."""
    cache = read_base_cache(root)
    if not cache:
        return None
    candidate = f"origin/{cache['base_ref']}"
    if not reference_exists(root, candidate):
        return None
    done = git(root, "rev-list", "--count", f"HEAD..{candidate}", allow_exit_codes=(0, 128))
    answer = done.stdout.strip()
    return int(answer) if done.returncode == 0 and answer.isdigit() else None


def base_check(root, phase):
    """Phases 6 and 7 carry a proof, so the base they measure from has to be the real one.

    A base that has not merged is fine: the proof resets to the merge-base with it, so the parent's
    work is present in the red run and only this change is reverted. Stacking is the point. A base
    that is not fetched, or one that has moved on, is not fine."""
    cache = read_base_cache(root)
    if cache is None:
        return ("base", "skip", "no pull request base recorded for this branch")
    candidate = f"origin/{cache['base_ref']}"
    if phase not in (6, 7):
        return ("base", "ok", f"measuring from {base_reference(root)}")
    if not reference_exists(root, candidate):
        return ("base", "FAIL",
                f"the base branch {cache['base_ref']} is not fetched, so the proof cannot measure "
                f"from it. Run `git fetch origin {cache['base_ref']}`.")
    behind = base_behind(root)
    if behind:
        return ("base", "FAIL",
                f"this branch is {behind} commit(s) behind {cache['base_ref']}, so the proof would "
                f"measure against a base that has moved. Rebase onto origin/{cache['base_ref']} "
                f"first, then re-prove.")
    return ("base", "ok", f"measuring from {base_reference(root)}, level with its base")


def changed_code_files(root, base):
    tracked = changed_names(root, "diff", "--name-only", base)
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
    uncommitted = changed_names(root, "diff", "--name-only", "HEAD") + untracked_paths(root)
    committed = []
    for sha in claude_commits(root, base):
        committed += changed_names(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
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
    return {"created_at": now_iso(), "base": base, "change_id": None if names else change_identity(root),
            "verdict": overall, "removed_truncated": truncated,
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


def fresh_references_report(root):
    """The stored report when it was made from the current base and the same change, otherwise None."""
    stored = latest_references_report(root)
    if stored and stored.get("base") == diff_base(root) and stored.get("change_id") == change_identity(root):
        return stored
    return None


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
        sweep = findings.get("sweep", [])
        if sweep:
            lines.append(f"**Undeclared changed example files** ({len(sweep)}), run with the change reverted:")
            for each in sweep:
                lines.append(f"- `{each['spec_file']}`: {each['status']}" + (f" ({each['reason']})" if each.get("reason") else ""))
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
    """The stored report when it was made from the current base and the same change, otherwise a fresh one.
    The change identity covers YAML, which the reference search reads too. `pr-section` must never
    say `not computed`: a missing report is a check that has not run, and the reader cannot tell the
    difference between that and a clean result."""
    if root is None:
        return None
    stored = fresh_references_report(root)
    if stored:
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
    with gate_lock():
        completed = subprocess.run(["bundle", "exec", "rspec", *arguments], cwd=str(GATE_WORKTREE / "platform"),
                                   env=prover_environment(), stdin=subprocess.DEVNULL, timeout=FULL_SUITE_TIMEOUT_SECONDS)
    sys.exit(completed.returncode)


def randomized_suite(arguments):
    seed = int(arguments[arguments.index("--seed") + 1]) if "--seed" in arguments else RANDOMIZED_SUITE_SEED
    base_sha = git(MAIN_REPOSITORY, "rev-parse", "HEAD").stdout.strip()
    report_path = GATE_WORKTREE.parent / f".gate-randomized-{seed}.json"
    json_path = GATE_WORKTREE.parent / f".gate-randomized-{seed}-rspec.json"
    with gate_lock():
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


# ------------------------------------------------------------------ trace


class TeeStream:
    """Writes through to the real stream and keeps a copy. The hook protocol reads gate output from
    standard output, so the copy must never take the place of the write."""

    def __init__(self, stream, copy):
        self.stream = stream
        self.copy = copy

    def write(self, text):
        try:
            self.copy.write(text)
        except (ValueError, OSError):
            pass
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def clip(text, limit=TRACE_MAX_TEXT):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more characters]"


def prune_traces():
    cutoff = time.time() - TRACE_KEEP_DAYS * 86400
    for path in TRACE_DIRECTORY.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def record_trace(command, arguments, stdin_text, stdout_text, stderr_text, exit_code, failure, started_at):
    """One JSON line per call. Never raises: a broken trace must not break a hook."""
    try:
        payload = {}
        if stdin_text.strip().startswith("{"):
            try:
                payload = json.loads(stdin_text)
            except (json.JSONDecodeError, ValueError):
                payload = {}
        moment = datetime.now(timezone.utc)
        record = {
            "at": moment.strftime(TIMESTAMP_FORMAT),
            "pid": os.getpid(),
            "command": command,
            "arguments": list(arguments),
            "cwd": os.getcwd(),
            "session": payload.get("session_id"),
            "hook_event": payload.get("hook_event_name"),
            "tool": payload.get("tool_name"),
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "exit": exit_code,
            "stdin": clip(stdin_text),
            "stdout": clip(stdout_text),
            "stderr": clip(stderr_text),
        }
        if failure:
            record["error"] = clip(failure)
        # The trace holds every prompt as the stdin of `surface`, so only the owner may read it.
        append_json_line(TRACE_DIRECTORY / f"{record['at'][:DAY_LENGTH]}.jsonl", record)
        if moment.minute == 0:
            prune_traces()
    except Exception:  # tracing is evidence, never a gate of its own
        pass


def read_traces(day=None):
    if not day:
        return read_json_line_directory(TRACE_DIRECTORY)
    path = TRACE_DIRECTORY / f"{day}.jsonl"
    return read_json_lines(path) if path.exists() else []


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def trace_command(arguments):
    session = argument_after(arguments, "--session")
    command = argument_after(arguments, "--command")
    day = argument_after(arguments, "--day")
    last = int(argument_after(arguments, "--last") or 40)
    records = read_traces(day)
    if session:
        records = [record for record in records if (record.get("session") or "").startswith(session)]
    if command:
        records = [record for record in records if record.get("command") == command]
    if "--failures" in arguments:
        records = [record for record in records if record.get("exit") or record.get("error")]
    if not records:
        print(f"no gate calls recorded under {TRACE_DIRECTORY}")
        return
    if "--sessions" in arguments:
        print(f"{'session':10} {'calls':>5} {'fails':>5} {'first':20} {'last':20} commands")
        grouped = {}
        for record in records:
            grouped.setdefault(record.get("session") or "-", []).append(record)
        for name, group in sorted(grouped.items(), key=lambda pair: pair[1][-1]["at"]):
            counts = {}
            for record in group:
                counts[record.get("command", "?")] = counts.get(record.get("command", "?"), 0) + 1
            failures = len([record for record in group if record.get("exit") or record.get("error")])
            summary = " ".join(f"{command}x{count}" for command, count in sorted(counts.items(), key=lambda pair: -pair[1]))
            print(f"{name[:8]:10} {len(group):>5} {failures:>5} {group[0]['at']:20} {group[-1]['at']:20} {summary[:60]}")
        return
    records = records[-last:]
    if "--full" in arguments:
        for record in records:
            print("=" * 100)
            print(f"{record['at']}  {record['command']} {' '.join(record.get('arguments', []))}  "
                  f"exit={record.get('exit')}  {record.get('duration_ms')}ms  session={record.get('session') or '-'}")
            print(f"cwd {record.get('cwd')}   hook {record.get('hook_event') or '-'}   tool {record.get('tool') or '-'}")
            for name in ("stdin", "stdout", "stderr", "error"):
                if record.get(name):
                    print(f"--- {name} ---")
                    print(record[name])
        return
    print(f"{'time':20} {'command':14} {'exit':>4} {'ms':>6} {'session':9} {'tool':12} answer")
    for record in records:
        answer = first_line(record.get("stdout")) or first_line(record.get("stderr")) or ("error" if record.get("error") else "")
        print(f"{record['at']:20} {record.get('command', '?'):14} {record.get('exit', 0):>4} {record.get('duration_ms', 0):>6} "
              f"{(record.get('session') or '-')[:8]:9} {(record.get('tool') or '-')[:12]:12} {answer[:70]}")


def argument_after(arguments, flag, default=None):
    if flag in arguments:
        position = arguments.index(flag) + 1
        if position < len(arguments):
            return arguments[position]
    return default


def traced_main(argv):
    """Runs main and records the call. Standard input is read here once and handed back to the
    subcommand, so the payload a hook sent is on record even when the subcommand ignores it."""
    started_at = time.monotonic()
    command = argv[1] if len(argv) > 1 else ""
    stdin_text = ""
    if command in TRACE_STDIN_COMMANDS:
        try:
            if not sys.stdin.isatty():
                stdin_text = sys.stdin.read()
                sys.stdin = io.StringIO(stdin_text)
        except (OSError, ValueError):
            stdin_text = ""
    out_copy, error_copy = io.StringIO(), io.StringIO()
    real_out, real_error = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = TeeStream(real_out, out_copy), TeeStream(real_error, error_copy)
    exit_code, failure = 0, None
    try:
        main(argv)
    except SystemExit as stop:
        exit_code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
        raise
    except BaseException:
        exit_code, failure = 1, traceback.format_exc()
        raise
    finally:
        sys.stdout, sys.stderr = real_out, real_error
        record_trace(command, argv[2:], stdin_text, out_copy.getvalue(), error_copy.getvalue(),
                     exit_code, failure, started_at)


# ------------------------------------------------------------ comment triage
# The user decides every review comment before any of it is fixed. The gate draws nothing and
# decides nothing: it holds the list, reads the user's marks off the diagram, and refuses code edits
# while a comment is still unanswered.

THREAD_QUERY = """
query($owner:String!,$repo:String!,$pr:Int!){
  repository(owner:$owner,name:$repo){ pullRequest(number:$pr){
    reviewThreads(first:100){ nodes{
      id isResolved isOutdated path line startLine diffSide
      comments(first:20){ nodes{ author{login} body createdAt url } } } } } }
}
"""


def comments_directory(root, pr):
    return root / COMMENTS_PATH / str(pr)


def comment_pointer(root):
    return root / COMMENTS_PATH / "current.json"


def repository_slug(root):
    """owner/name from the origin remote, so the query does not hardcode one repository."""
    url = git(root, "remote", "get-url", "origin", allow_exit_codes=(0, 1, 2, 128)).stdout.strip()
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def pull_request_number(root, given):
    if given:
        return given
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", allow_exit_codes=(0, 128)).stdout.strip()
    done = subprocess.run(["gh", "pr", "view", branch, "--json", "number", "--jq", ".number"],
                          cwd=str(root), capture_output=True, text=True, env=github_environment())
    return done.stdout.strip() or None


def github_environment():
    environment = dict(os.environ)
    environment.setdefault("GH_HOST", GITHUB_HOST)
    return environment


def comment_fetch(arguments):
    """Store every unresolved review thread, with the reviewer's own words kept whole.

    The diagram has to carry what the reviewer wrote, not a summary of it, so the body is stored
    verbatim and never shortened here."""
    root = require_repository("comment-triage fetch")
    pr = pull_request_number(root, argument_value(arguments, "--pr"))
    slug = repository_slug(root)
    if not pr or not slug:
        refuse("comment-triage fetch", ["could not tell which pull request this branch belongs to; pass --pr N"])
    owner, name = slug.split("/", 1)
    done = subprocess.run(["gh", "api", "graphql", "-f", f"owner={owner}", "-f", f"repo={name}",
                           "-F", f"pr={pr}", "-f", f"query={THREAD_QUERY}"],
                          cwd=str(root), capture_output=True, text=True, env=github_environment())
    if done.returncode != 0:
        refuse("comment-triage fetch", [f"the thread query failed: {done.stderr.strip()[:300]}"])
    payload = json.loads(done.stdout or "{}")
    nodes = (((payload.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads", {}).get("nodes", [])
    threads = [thread_record(node) for node in nodes if not node.get("isResolved")]

    directory = comments_directory(root, pr)
    directory.mkdir(parents=True, exist_ok=True)
    stored = read_json(directory / "threads.json", default={}) or {}
    previous = {each["id"]: each for each in stored.get("threads", [])}
    fetched_at = now_iso()
    issued = issue_tokens(threads, stored, fetched_at)
    for thread in threads:
        was = previous.get(thread["id"])
        thread["reopened"] = bool(was and was.get("latest_at") and thread["latest_at"] > was["latest_at"])
    write_json(directory / "threads.json", {"pr": pr, "fetched_at": fetched_at, "threads": threads, "issued": issued})
    write_json(comment_pointer(root), {"pr": pr, "updated_at": fetched_at})
    drop_stale_decisions(root, pr, threads)
    print_thread_list(pr, threads, decisions_of(root, pr))


def issue_tokens(threads, stored, now):
    """Give every thread the token it had before, and a new thread the next unused number. A token never moves
    to another thread of the pull request, so `C3` in a reply means the thread that was C3 when the question
    was drawn, however many fetches came between. Each token also keeps when it was first given out
    (`token_since`): a phone reply typed before then names the same token on another pull request in this
    worktree, or in a store that was emptied."""
    issued = {each["id"]: {"token": each["token"], "since": each.get("token_since")}
              for each in stored.get("threads", [])}
    issued.update(stored.get("issued", {}))
    number = max([int(entry["token"][1:]) for entry in issued.values()] or [0])
    for thread in threads:
        if thread["id"] not in issued:
            number += 1
            issued[thread["id"]] = {"token": f"C{number}", "since": now}
        entry = issued[thread["id"]]
        entry["since"] = entry.get("since") or now
        thread["token"], thread["token_since"] = entry["token"], entry["since"]
    return issued


def thread_record(node):
    comments = (node.get("comments") or {}).get("nodes") or []
    first = comments[0] if comments else {}
    return {
        "id": node["id"],
        "path": node.get("path"),
        "line": node.get("line") or node.get("startLine"),
        "outdated": bool(node.get("isOutdated")),
        "author": (first.get("author") or {}).get("login", "?"),
        "url": first.get("url"),
        "latest_at": max([comment.get("createdAt", "") for comment in comments] or [""]),
        "comments": [{"author": (comment.get("author") or {}).get("login", "?"),
                      "body": comment.get("body", ""), "created_at": comment.get("createdAt", "")}
                     for comment in comments],
    }


def drop_stale_decisions(root, pr, threads):
    """A reviewer who replies on a thread reopens it. The old decision was made without those words,
    so it stops counting and the user sees the thread again."""
    directory = comments_directory(root, pr)
    decisions = read_json(directory / "decisions.json", default={}) or {}
    reopened = [thread["token"] for thread in threads if thread.get("reopened") and thread["id"] in decisions]
    for thread in threads:
        if thread.get("reopened"):
            decisions.pop(thread["id"], None)
    if reopened:
        write_json(directory / "decisions.json", decisions)
        print(f"comment-triage: {', '.join(reopened)} got a new reply, so the earlier decision no longer counts.")


def decisions_of(root, pr):
    return read_json(comments_directory(root, pr) / "decisions.json", default={}) or {}


def print_thread_list(pr, threads, decisions):
    if not threads:
        print(f"comment-triage: pull request {pr} has no unresolved review thread.")
        return
    undecided = [thread for thread in threads if thread["id"] not in decisions]
    print(f"comment-triage: pull request {pr} has {len(threads)} unresolved thread(s), "
          f"{len(undecided)} still waiting for a decision.\n")
    for thread in threads:
        decision = decisions.get(thread["id"])
        state = f"decided: {decision['verdict']}" if decision else "NO DECISION"
        outdated = " (outdated)" if thread["outdated"] else ""
        print(f"{thread['token']}  {thread['path']}:{thread['line']}{outdated}  by {thread['author']}  [{state}]")
        for comment in thread["comments"]:
            for line in comment["body"].strip().splitlines():
                print(f"      {line}")
        print()


def comment_decide(arguments):
    """Turn the user's `R:` marks on the triage diagram into one decision per thread.

    A mark is tied to a thread by the `C<n>` token on the anchor it sits next to, never by distance
    alone. Two anchors can sit the same distance from one mark, so a mark that names no token, or
    names one that is not on the list, records nothing: a decision written against the wrong comment
    is worse than no decision, because nothing later shows it went to the wrong place.
    """
    root = require_repository("comment-triage decide")
    pr = pull_request_number(root, argument_value(arguments, "--pr"))
    marks_file = argument_value(arguments, "--marks")
    token = argument_value(arguments, "--token")
    if not pr or not (marks_file or token):
        refuse("comment-triage decide", [
            "usage: comment-triage decide --pr N --marks <file.excalidraw|file.svg>",
            "   or: comment-triage decide --pr N --token C4 --verdict <verdict> --evidence \"<their words>\"",
            f"verdicts: {', '.join(COMMENT_VERDICTS)}"])
    stored = read_json(comments_directory(root, pr) / "threads.json", default={}) or {}
    threads = stored.get("threads") or []
    if not threads:
        refuse("comment-triage decide", [f"no thread list for pull request {pr}. Run `comment-triage fetch` first."])
    if token:
        comment_decide_from_chat(root, pr, threads, arguments)
        return

    marks, problem = read_marks(marks_file)
    if problem:
        refuse("comment-triage decide", [problem])
    if not marks:
        refuse("comment-triage decide", [f"{marks_file} carries no R: mark yet."])

    by_token = {thread["token"]: thread for thread in threads}
    decisions = decisions_of(root, pr)
    recorded, problems = [], []
    for mark in marks:
        token = token_of(mark.get("point") or "")
        if token is None:
            problems.append(f'the mark "R:{mark["reply"]}" sits next to "{shorten(mark.get("point"))}", '
                            f"which names no comment. Put the mark next to the `C<n> decision:` line of its column.")
            continue
        if token not in by_token:
            problems.append(f'the mark "R:{mark["reply"]}" names {token}, which is not on this pull request\'s list '
                            f"({', '.join(sorted(by_token))}).")
            continue
        thread = by_token[token]
        decisions[thread["id"]] = {
            "token": token, "verdict": verdict_of_mark(mark), "instruction": mark["reply"],
            "path": thread["path"], "line": thread["line"], "decided_at": now_iso(),
            "source": "marks", "marks_file": str(Path(marks_file).expanduser()),
        }
        recorded.append((token, decisions[thread["id"]]["verdict"], mark["reply"]))

    if problems:
        refuse("comment-triage decide", problems)
    write_json(comments_directory(root, pr) / "decisions.json", decisions)
    print(f"comment-triage: recorded {len(recorded)} decision(s) for pull request {pr}.")
    for token, verdict, reply in recorded:
        print(f"  {token}  {verdict}" + (f"  \"{reply}\"" if verdict == COMMENT_REPLY else ""))
    undecided = [thread["token"] for thread in threads if thread["id"] not in decisions]
    print(f"  still waiting: {', '.join(undecided)}" if undecided else "  every thread has a decision.")


def comment_decide_from_chat(root, pr, threads, arguments):
    """Record a decision the user gave in conversation instead of on the drawing.

    The drawing was the only way in, and the drawing is a file this tool can write, so a decision
    spoken in chat was being transcribed into a mark and read back as though the user had drawn it.
    That works, and it hides where the decision came from. This records the same decision and keeps
    the user's own words beside it, the way a deliberate design difference is recorded.

    It is weaker evidence than a mark the user typed, and it is meant to be: the quoted words are
    what makes it checkable, so a decision with nothing quoted is refused."""
    token = argument_value(arguments, "--token")
    verdict = argument_value(arguments, "--verdict")
    evidence = argument_value(arguments, "--evidence")
    note = argument_value(arguments, "--note", "")
    by_token = {thread["token"]: thread for thread in threads}
    problems = []
    if token not in by_token:
        problems.append(f"--token {token} is not on this pull request's list ({', '.join(sorted(by_token))})")
    if verdict not in COMMENT_VERDICTS:
        problems.append(f"--verdict must be one of {', '.join(COMMENT_VERDICTS)}")
    if token in by_token:
        source, evidence_problem = comment_evidence(evidence, root, by_token[token], verdict)
        if evidence_problem:
            problems.append(evidence_problem)
    if problems:
        refuse("comment-triage decide", problems)

    thread = by_token[token]
    decisions = decisions_of(root, pr)
    decisions[thread["id"]] = {
        "token": token, "verdict": verdict, "instruction": note or evidence,
        "path": thread["path"], "line": thread["line"], "decided_at": now_iso(),
        "source": source, "evidence": evidence}
    write_json(comments_directory(root, pr) / "decisions.json", decisions)
    print(f"comment-triage: recorded {token} as {verdict} from what the user said.")
    print(f'  their words: "{evidence.strip()[:160]}"')
    undecided = [each["token"] for each in threads if each["id"] not in decisions]
    print(f"  still waiting: {', '.join(undecided)}" if undecided else "  every thread has a decision.")


def comment_evidence(evidence, root, thread, verdict):
    """Where a chat decision's evidence came from (`chat` or `phone`), and why it does not hold, or None.

    Keyboard words answer the reviewer's latest comment, so they cannot come before it, nor before a newer phone
    reply on the same comment. A phone reply names its token, so it also cannot come before the token was given
    to this thread, and it counts only while it is the newest phone reply for that token. A tap (`R:IN` from
    `phone C3: R:IN`) is checked by its record, never by its words: four characters are a choice, not a quote.
    Any other phone reply on a comment is an instruction, the same rule an `R:<text>` mark on the drawing
    follows. A reply to another question never decides a comment."""
    after = thread.get("latest_at") or None
    since = max(filter(None, (after, thread.get("token_since"))), default=None)
    stripped = (evidence or "").strip()
    lead = PHONE_LEAD.match(stripped)
    if lead and lead["question"] != thread["token"]:
        return OWNER_SOURCE_PHONE, f"the evidence is a phone reply for {lead['question']}, not {thread['token']}"
    words = stripped[lead.end():] if lead else stripped
    tap, reply = phone_mark(words)
    if tap:
        return OWNER_SOURCE_PHONE, phone_tap_problem(root, thread["token"], verdict, tap, since)
    newest = newest_phone_reply(root, thread["token"], since)
    # Keyboard words count only when typed after the newest phone reply, so a second later at the earliest.
    keyboard_after = one_second_after(newest["at"]) if newest else after
    if any(owner_said(each, root, after=keyboard_after) for each in (evidence, reply) if each):
        return "chat", None
    quote = reply or words
    if newest is None or matching_record(quote, root, after=since, phone=True, question=thread["token"]) is None:
        if since != after and matching_record(quote, root, after=after, phone=True, question=thread["token"]):
            return OWNER_SOURCE_PHONE, (f"that phone reply was typed before {since}, when {thread['token']} was given "
                                        "to this comment, so it answers another comment: ask the owner about this one")
        return "chat", owner_words_refusal(after)
    if newest.get("tap") or not says_whole(newest.get("text"), comparable(quote)):
        return OWNER_SOURCE_PHONE, f"the newest phone reply for {thread['token']} is not these words"
    if verdict != COMMENT_REPLY:
        return OWNER_SOURCE_PHONE, f"a phone reply in words is an instruction: record it as {COMMENT_REPLY}"
    return OWNER_SOURCE_PHONE, None


def one_second_after(moment):
    return (datetime.strptime(moment, TIMESTAMP_FORMAT) + timedelta(seconds=1)).strftime(TIMESTAMP_FORMAT)


def newest_phone_reply(root, token, after):
    """The newest phone reply for this token, typed in this worktree after `after`, or None."""
    replies = [record for record in owner_words()
               if record.get("source") == OWNER_SOURCE_PHONE and record.get("question") == token
               and typed_in(record, root) and inside_window(record, after, None)]
    # A stable sort keeps the store's order for two replies typed in the same second.
    return sorted(replies, key=lambda record: record["at"])[-1] if replies else None


def phone_tap_problem(root, token, verdict, tap, after):
    """A tap holds when the newest phone reply for this token is that tap, and the verdict is the one it means."""
    newest = newest_phone_reply(root, token, after)
    if newest is None:
        return f"no phone reply for {token} was typed in this worktree after {after or 'the comment'}"
    if newest.get("tap") != tap:
        return f"the newest phone reply for {token} is not the tap R:{tap}"
    if MARK_VERDICTS[tap] != verdict:
        return f"the tap R:{tap} means {MARK_VERDICTS[tap]}, not {verdict}"
    return None


def read_marks(marks_file):
    path = Path(marks_file).expanduser()
    if not path.is_file():
        return None, f"{path} does not exist."
    done = subprocess.run([str(MARKS_READER), str(path), "--json"], capture_output=True, text=True)
    if done.returncode != 0:
        return None, f"could not read the marks: {done.stderr.strip()[:200]}"
    try:
        return json.loads(done.stdout or "[]"), None
    except json.JSONDecodeError:
        return None, f"the marks reader did not answer with JSON: {done.stdout.strip()[:200]}"


def token_of(anchor_text):
    match = re.search(r"\bC(\d+)\b", anchor_text)
    return f"C{match.group(1)}" if match else None


def verdict_of_mark(mark):
    return MARK_VERDICTS.get(mark["verdict"].upper(), COMMENT_REPLY)


def shorten(text):
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else "nothing"
    return first[:60]


def comment_state(root):
    """What the guard and the Stop hook read. Local files only: an edit must never wait on the network."""
    pointer = read_json(comment_pointer(root), default={}) or {}
    pr = pointer.get("pr")
    if not pr:
        return None
    stored = read_json(comments_directory(root, pr) / "threads.json", default={}) or {}
    threads = stored.get("threads") or []
    decisions = decisions_of(root, pr)
    open_threads = [thread for thread in threads if thread["id"] not in decisions]
    # An outdated thread points at code that has since changed or gone, which is why the standing
    # rule for "what is still outstanding" excludes them. Three of them, all on S3 code that had
    # been deleted, held every edit on a branch. They stay on the list so nothing is lost, and they
    # hold nothing.
    undecided = [thread for thread in open_threads if not thread.get("outdated")]
    outdated = [thread for thread in open_threads if thread.get("outdated")]
    return {"pr": pr, "threads": threads, "decisions": decisions,
            "undecided": undecided, "outdated": outdated}


def comment_status(arguments):
    root = require_repository("comment-triage status")
    state = comment_state(root)
    if state is None:
        print("comment-triage: no review comment has been fetched on this branch.")
        return
    print_thread_list(state["pr"], state["threads"], state["decisions"])


def comment_guard_reason(root):
    state = comment_state(root)
    if not state or not state["undecided"]:
        return None
    waiting = ", ".join(f"{thread['token']} ({thread['path']}:{thread['line']})" for thread in state["undecided"][:6])
    return (f"{len(state['undecided'])} review comment(s) on pull request {state['pr']} have no decision yet: {waiting}. "
            f"Draw the triage diagram, then wait. The user answers either on the drawing, read with "
            f"`{TRIAGE_WRAPPER} decide --marks <file>`, or in chat, recorded with "
            f"`{TRIAGE_WRAPPER} decide --token <C> --verdict <v> --evidence \"<their words>\"`. "
            f"You do not decide these; the user does.")


def comment_stop_message(root):
    state = comment_state(root)
    if not state:
        return None
    lines = []
    if state["undecided"]:
        lines.append(
            f"{len(state['undecided'])} of {len(state['threads'])} review comment(s) on pull request "
            f"{state['pr']} are still waiting for the user's decision: "
            + ", ".join(thread["token"] for thread in state["undecided"])
            + ". Nothing is fixed until they are decided.")
    if state["outdated"]:
        lines.append(
            f"{len(state['outdated'])} thread(s) are open but outdated, so they hold nothing: "
            + ", ".join(f"{thread['token']} ({thread['path']})" for thread in state["outdated"])
            + ". The code they point at has changed or gone. Answer them on the pull request when "
              "you are there, or decide them like any other.")
    return "\n".join(lines) if lines else None


def comment_check(root, paths):
    state = comment_state(root)
    if state is None:
        return ("comments", "skip", "no review comment fetched")
    if state["undecided"]:
        return ("comments", "FAIL",
                ", ".join(thread["token"] for thread in state["undecided"]) + " have no decision from the user yet.")
    outdated = f", {len(state['outdated'])} outdated and holding nothing" if state["outdated"] else ""
    return ("comments", "ok", f"{len(state['threads'])} thread(s) decided{outdated}")

# --------------------------------------------------------------- live checks
# A session shipped a whole page and wrote "Never seen rendered: all of it." The rule was already
# in phase-7 step 5 and in its done list as a checkbox, and a checkbox is not a check. So the
# evidence is produced by the gate, never by the session:
#
#   the browser   the gate fetches the page itself and stores what came back
#   the backend   the gate runs `bin/rails runner` itself and stores the real stdout
#
# The session chooses the url and the expression. It never writes the result.

LIVE_KINDS = ("browser", "runner")
LIVE_WRAPPER = ".claude/bin/live-record"
USER_FACING_PREFIXES = FRONTEND_LOGIC + ("platform/app/views/", "platform/app/presenters/",
                                         "platform/app/helpers/")
SCREENSHOT_MINIMUM_BYTES = 4096
# Local work runs against this account. Everything redirects to the picker until one is chosen.
LIVE_ACCOUNT = os.environ.get("IPAAS_LIVE_ACCOUNT", "xurrent")
LIVE_PHASES = (5, 7)


def live_directory(root):
    return proof_directory(root) / LIVE_DIRECTORY_NAME


def user_facing_paths(paths):
    """What a person can see. The javascript tree is the real interface here: three files in
    app/views against five hundred components."""
    return [path for path in paths if path.startswith(USER_FACING_PREFIXES) and is_code_path(path)]


def backend_paths(paths):
    """Code with no rendered surface, so a browser cannot show whether it works."""
    return [path for path in paths
            if is_code_path(path) and not path.startswith(USER_FACING_PREFIXES)]


def checks_web_port():
    """The port the checks worktree serves on, read from the slot file the setup wrote."""
    environment = CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE
    if not environment.is_file():
        return None
    match = re.search(r"^export WEB_PORT=(\d+)", environment.read_text(), re.MULTILINE)
    return int(match.group(1)) if match else None


def live_state(root):
    """What has been proved live against the diff as it stands now.

    Keyed on the change like the review records, so a code change after the page was rendered makes
    the evidence stale. That is the case the user named: a change made after phase 7 has not been
    seen, whatever was rendered before it."""
    return evidence_states(root, live_directory(root), LIVE_KINDS)


def live_record(arguments):
    """Prove the change live. The session names what to open or run; the gate does it.

    Nothing here takes the session's word. The page is fetched by the gate, the runner expression
    is executed by the gate, and the stored record carries what actually came back."""
    root = require_repository("live-record")
    kind = argument_value(arguments, "--kind")
    if kind not in LIVE_KINDS:
        refuse("live-record", [f"--kind must be one of {', '.join(LIVE_KINDS)}"])
    record = live_browser(root, arguments) if kind == "browser" else live_runner(root, arguments)
    record.update(evidence_stamp(root))
    record["recorded_at"] = now_iso()
    directory = live_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / f"{kind}.json", record)
    print(f"live-record: {kind} recorded against change {record['change_id']}.")
    for line in record.get("summary", []):
        print(f"  {line}")


def live_browser(root, arguments):
    """Fetch the page the session says it drove, and keep what came back.

    A screenshot on its own proves nothing: a session can write a png. So the gate asks the server
    for the page at record time. If the server is not up, there is no record, and there is no way
    to write one by hand."""
    path = argument_value(arguments, "--path")
    asserted = argument_value(arguments, "--asserted")
    screenshot = argument_value(arguments, "--screenshot")
    problems = []
    if not path or not path.startswith("/"):
        problems.append("--path wants the page path the browser was on, for example /solutions")
    if not asserted:
        problems.append("--asserted wants one line saying what you looked at and what you saw")
    port = checks_web_port()
    if port is None:
        problems.append(f"no WEB_PORT in {CHECKS_WORKTREE / WORKTREE_ENVIRONMENT_FILE}; run gate.py setup-checks")
    if problems:
        refuse("live-record", problems)

    url = f"http://127.0.0.1:{port}{path}"
    # Redirects are followed, because this application redirects by design: every page carries
    # redirect_to_pick_solution, so refusing a 302 refused a page that renders perfectly well. What
    # matters is where the chain ends, not that it moved.
    work = Path(tempfile.mkdtemp(prefix="live-record-"))
    headers_path = work / "headers.txt"
    jar = pick_account(work, port, argument_value(arguments, "--account", LIVE_ACCOUNT))
    done = subprocess.run(["curl", "-sL", "-o", "/dev/null", "-D", str(headers_path),
                           "-b", str(jar), "-c", str(jar),
                           "-w", "%{http_code} %{size_download} %{num_redirects} %{url_effective}",
                           "--max-time", "30", url], capture_output=True, text=True)
    answer = (done.stdout or "").split()
    status = answer[0] if answer else "000"
    size = int(answer[1]) if len(answer) > 1 and answer[1].isdigit() else 0
    redirects = int(answer[2]) if len(answer) > 2 and answer[2].isdigit() else 0
    landed = answer[3] if len(answer) > 3 else url
    headers = headers_path.read_text(errors="replace") if headers_path.is_file() else ""

    if status == "000":
        refuse("live-record", [
            f"nothing answered on {url}. The page was never rendered, so there is nothing to record.",
            "Start the server in the checks worktree, drive the page, then record it.",
            "There is no way past this. A change nobody has seen does not leave the draft."])
    if not landed.startswith(f"http://127.0.0.1:{port}"):
        refuse("live-record", [
            f"{url} left the application and ended at {landed}, so no page of this change rendered.",
            "Two things send a request off-host, and the landing url above says which:",
            "  the sign-in flow, when the current user cannot be established. Check that the server",
            "  has STUB_IDENTITY_PROVIDER=true and that User.first exists in the checks database.",
            "  an external host the page itself redirected to, which is the page's own behaviour.",
            "The gate carries no credential and will not accept a page it could not reach itself."])
    if not status.startswith("2"):
        refuse("live-record", [f"{url} ended at {landed} with HTTP {status}. The page did not render."])

    # Anything can listen on a port and answer 200. A stub of mine sat on this one for eight hours
    # answering every request with 813 bytes, kept Rails from binding, and would have been recorded
    # as a rendered page. These two headers come from ActionDispatch::RequestId and Rack::Runtime,
    # so an answer without them is not this application.
    if not re.search(r"(?mi)^x-request-id:", headers) or not re.search(r"(?mi)^x-runtime:", headers):
        refuse("live-record", [
            f"{landed} answered HTTP {status}, but without the x-request-id and x-runtime headers "
            f"that Rails sets on every response.",
            "Something other than the application is listening on this port. Find it with",
            f"  lsof -nP -iTCP:{port} -sTCP:LISTEN",
            "and stop it, then start the server in the checks worktree."])

    # Landing somewhere else means the page under test never rendered. Asking for the solutions
    # list and arriving at the account picker is a 200, and recording it would say a page was seen
    # that nobody saw.
    landed_path = re.sub(r"^https?://[^/]+", "", landed).split("?")[0] or "/"
    if landed_path.rstrip("/") != path.split("?")[0].rstrip("/"):
        refuse("live-record", [
            f"{url} ended at {landed_path}, not at {path}.",
            "The page under test never rendered: the application sent the request somewhere else,",
            "which usually means it will not show that page in this state.",
            "Record the page you actually reached, or put the application in the state that shows",
            "this one. A 200 from a different page is not evidence about this one."])

    summary = [f"{url} -> {landed} HTTP {status}, {size} bytes"
               + (f", {redirects} redirect(s)" if redirects else ""),
               f"asserted: {asserted}"]
    record = {"kind": "browser", "url": url, "path": path, "http_status": status,
              "landed": landed, "redirects": redirects,
              "bytes": size, "asserted": asserted, "summary": summary}
    if screenshot:
        shot = Path(screenshot).expanduser()
        if not shot.is_file():
            refuse("live-record", [f"--screenshot {shot} does not exist"])
        if shot.stat().st_size < SCREENSHOT_MINIMUM_BYTES:
            refuse("live-record", [f"--screenshot {shot} is {shot.stat().st_size} bytes, too small to be a page"])
        record["screenshot"] = str(shot)
        record["screenshot_bytes"] = shot.stat().st_size
        summary.append(f"screenshot {shot} ({shot.stat().st_size} bytes)")
    return record


def pick_account(work, port, account):
    """Start a session and choose the account, the way a person does before any page will render.

    Every page redirects to the account picker until one is chosen, so an unauthenticated fetch
    only ever sees the picker. The gate drives the picker itself: it is two requests and it holds
    no credential, so nothing is handed to it that it has to trust."""
    jar = work / "cookies.txt"
    picker = work / "picker.html"
    base = f"http://127.0.0.1:{port}"
    subprocess.run(["curl", "-s", "-c", str(jar), "-o", str(picker), "--max-time", "30",
                    f"{base}/pick_account"], capture_output=True, text=True)
    token = ""
    if picker.is_file():
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', picker.read_text(errors="replace"))
        token = match.group(1) if match else ""

    command = ["curl", "-s", "-b", str(jar), "-c", str(jar), "-o", "/dev/null",
               "-w", "%{http_code} %{redirect_url}", "--max-time", "30", "-X", "POST",
               "-d", f"sitename={account}"]
    if token:
        command += ["-H", f"X-CSRF-Token: {token}"]
    command.append(f"{base}/pick_account")
    done = subprocess.run(command, capture_output=True, text=True)
    answer = (done.stdout or "").split()
    status = answer[0][-3:] if answer else "000"
    went_to = answer[1] if len(answer) > 1 else ""
    if status == "429":
        refuse("live-record", [
            f"{base} is rate limiting the account picker (HTTP 429). Wait, then record again."])
    if not status.startswith("3"):
        refuse("live-record", [
            f"could not pick the {account} account on {base}: the picker answered HTTP {status}.",
            "Until an account is chosen every page redirects to the picker, so nothing can render."])
    # A sitename the application does not know sends you back to the picker instead of onward.
    if went_to.endswith("/pick_account"):
        refuse("live-record", [
            f"the application does not know an account with sitename '{account}': the picker sent "
            f"the request straight back to itself.",
            f"Run `bin/rails runner 'puts Account.pluck(:sitename).inspect'` in the checks worktree "
            f"to see which names exist."])
    return jar


def live_runner(root, arguments):
    """Run the expression through `bin/rails runner` in the checks worktree and keep the output.

    The session writes the expression. The gate runs it and stores the real stdout, the way a pull
    request comment only reaches the thread through the wrapper that stamps it."""
    expression = argument_value(arguments, "--expression")
    asserted = argument_value(arguments, "--asserted")
    problems = []
    if not expression:
        problems.append("--expression wants the ruby to run, for example 'puts Solution.count'")
    if not asserted:
        problems.append("--asserted wants one line saying what the output proves")
    if not (CHECKS_WORKTREE / "platform").is_dir():
        problems.append(f"no checks worktree at {CHECKS_WORKTREE}; run gate.py setup-checks")
    if problems:
        refuse("live-record", problems)

    done = subprocess.run(["bin/rails", "runner", expression],
                          cwd=str(CHECKS_WORKTREE / "platform"), env=checks_environment(),
                          capture_output=True, text=True, timeout=RSPEC_TIMEOUT_SECONDS)
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0:
        refuse("live-record", [f"`bin/rails runner` exited {done.returncode}. Nothing is recorded.",
                               *output.strip().splitlines()[-8:]])
    summary = [f"bin/rails runner exited 0", f"asserted: {asserted}"]
    return {"kind": "runner", "expression": expression, "exit_code": done.returncode,
            "output": output[-4000:], "asserted": asserted, "summary": summary}


def live_check(root, phase, paths):
    """What the diff needs seen, and whether it has been.

    Bound at phase 5 and phase 7, and on any code change made after phase 7, because evidence is
    keyed to the patch: a change nobody has rendered since is a change nobody has seen."""
    facing = user_facing_paths(paths)
    backend = backend_paths(paths)
    if not facing and not backend:
        return ("live", "skip", "no code changed")
    if phase is not None and phase < 5:
        return ("live", "skip", f"phase {phase} ships no behaviour to render")

    state = live_state(root)
    problems = []
    if facing:
        status, _record = state["browser"]
        if status != "ok":
            problems.append(
                f"{len(facing)} user-facing file(s) changed and no page has been rendered"
                + (" since the last edit" if status == "stale" else "")
                + f": {', '.join(facing[:3])}"
                + (f" and {len(facing) - 3} more" if len(facing) > 3 else ""))
    if backend:
        status, _record = state["runner"]
        if status != "ok":
            problems.append(
                f"{len(backend)} backend file(s) changed and nothing has been run against them"
                + (" since the last edit" if status == "stale" else "")
                + f": {', '.join(backend[:3])}"
                + (f" and {len(backend) - 3} more" if len(backend) > 3 else ""))
    if problems:
        return ("live", "FAIL", "; ".join(problems) + f". Record it with {LIVE_WRAPPER}.")
    proved = [kind for kind in LIVE_KINDS if state[kind][0] == "ok"]
    return ("live", "ok", f"{', '.join(proved)} proved against this diff")


def live_stop_message(root):
    paths = changed_files_all(root, diff_base(root))
    name, status, detail = live_check(root, phase_number(root), paths)
    if status != "FAIL":
        return None
    return (f"{detail} A change nobody has seen does not leave the draft.")


def ready_guard_reason(root):
    """`gh pr ready` is the moment the work is offered to a reviewer. Nothing owned it before, so a
    page nobody had rendered became reviewable with one command."""
    paths = changed_files_all(root, diff_base(root))
    name, status, detail = live_check(root, phase_number(root), paths)
    if status != "FAIL":
        return None
    return (f"the pull request stays a draft. {detail}\n"
            f"  Start the server in the checks worktree, drive the page or run the expression, and "
            f"record it. The gate fetches the page and runs the runner itself, so there is nothing "
            f"to write by hand and no way past this.")


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
        "trace": lambda: trace_command(arguments),
        "review-record": lambda: review_record(arguments),
        "review-section": lambda: review_section(arguments),
        "deviation": lambda: deviation(arguments),
        "design-links": lambda: design_links(arguments),
        "figma-compare": lambda: figma_compare(arguments),
        "comment-fetch": lambda: comment_fetch(arguments),
        "comment-decide": lambda: comment_decide(arguments),
        "comment-status": lambda: comment_status(arguments),
        "live-record": lambda: live_record(arguments),
        "answers": lambda: answers(),
        "waive": lambda: waive(arguments),
        "units": lambda: units_command(arguments),
    }
    if command not in dispatch:
        print(f"unknown subcommand {command}\n{__doc__}")
        sys.exit(2)
    dispatch[command]()


if __name__ == "__main__":
    traced_main(sys.argv)
