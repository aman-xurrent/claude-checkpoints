"""Tests for gate.py. Every case comes from a failure the gate had in live use, or one a review reproduced.

Run: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GATE_FILE = Path(__file__).resolve().parent.parent / "gate.py"
SPEC = importlib.util.spec_from_file_location("gate", GATE_FILE)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

CLAUDE_TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EDIT_FIRST_PICK = FIXTURES / "edit_first_pick.py"
HAS_TYPESCRIPT = bool(shutil.which("node")) and gate.typescript_package().exists()


def sh(cwd, *command, check=True, env=None):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=check, env=env)


class ScratchRepository(unittest.TestCase):
    """A repository with an origin/main ref, so the gate's base lookups resolve as they do in ipaas."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="gate-test-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.addCleanup(gate.PROCESS_MEMO.clear)
        self.root = self.directory / "repo"
        self.root.mkdir()
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "test")
        self.write("app/a.rb", "one\ntwo\nthree\n")
        self.commit("base")
        self.publish_main()
        gate.PROCESS_MEMO.clear()

    def publish_main(self):
        sh(self.root, "git", "update-ref", "refs/remotes/origin/main", "main")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message, env=None):
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-q", "-m", message, env=env)
        return self.head()

    def head(self):
        return sh(self.root, "git", "rev-parse", "HEAD").stdout.strip()

    def identity_now(self):
        gate.PROCESS_MEMO.clear()
        return gate.change_identity(self.root)


class CommitHookTest(ScratchRepository):
    """PR 1065: commits titled Phase 2, 3 and 4 came back from a rebase carrying `Phase: 7`."""

    def setUp(self):
        super().setUp()
        hook = self.root / ".git/hooks/prepare-commit-msg"
        hook.write_text(f"#!/bin/sh\nexec {sys.executable} {GATE_FILE} commit-msg \"$@\"\n")
        hook.chmod(0o755)
        (self.root / ".git/info/exclude").write_text(".claude/\n")

    def set_phase(self, phase):
        self.write(".claude/proof/phase", f"{phase}\n")

    def body(self):
        return sh(self.root, "git", "log", "-1", "--format=%B").stdout

    def test_a_rebase_keeps_each_commit_phase(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.set_phase(3)
        self.write("app/a.rb", "one\nTWO\nthree\n")
        self.commit("Phase 3 work")
        sh(self.root, "git", "checkout", "-q", "main")
        self.write("app/b.rb", "upstream\n")
        self.commit("upstream")
        sh(self.root, "git", "checkout", "-q", "feature")
        self.set_phase(7)
        sh(self.root, "git", "rebase", "-q", "main")
        self.assertIn("Phase: 3", self.body())
        self.assertNotIn("Phase: 7", self.body())
        self.assertIn("[skip ci]", self.body())

    def test_an_amend_during_a_later_phase_takes_the_later_phase(self):
        self.set_phase(5)
        self.write("app/a.rb", "one\nfour\nthree\n")
        self.commit("Phase 5 work")
        self.assertIn("[skip ci]", self.body())
        self.set_phase(7)
        self.write("app/a.rb", "one\nfive\nthree\n")
        sh(self.root, "git", "commit", "-q", "-a", "--amend", "--no-edit")
        self.assertEqual(self.body().count("Phase:"), 1)
        self.assertIn("Phase: 7", self.body())
        self.assertNotIn("[skip ci]", self.body())

    def test_a_phase_typed_into_the_message_is_replaced(self):
        self.set_phase(7)
        self.write("app/a.rb", "one\nsix\nthree\n")
        self.commit("sneak it through\n\nPhase: 2")
        self.assertIn("Phase: 7", self.body())
        self.assertNotIn("Phase: 2", self.body())
        self.assertNotIn("[skip ci]", self.body())

    def test_the_replay_markers(self):
        linked = self.directory / "linked"
        sh(self.root, "git", "worktree", "add", "-q", "-b", "other", str(linked))

        def marker(name):
            location = Path(sh(linked, "git", "rev-parse", "--git-path", name).stdout.strip())
            return location if location.is_absolute() else linked / location
        self.assertIsNone(gate.replay_marker(linked))
        marker("rebase-merge").mkdir(parents=True)
        self.assertIsNone(gate.replay_marker(linked), "a break stop is the user's own commit")
        marker("REBASE_HEAD").write_text("x\n")
        self.assertEqual(gate.replay_marker(linked), "REBASE_HEAD")
        marker("rebase-merge/amend").write_text("x\n")
        self.assertIsNone(gate.replay_marker(linked), "an edit stop is the user's own commit")
        marker("CHERRY_PICK_HEAD").write_text("x\n")
        self.assertEqual(gate.replay_marker(linked), "CHERRY_PICK_HEAD")
        self.assertIsNone(gate.replay_marker(self.root))

    def test_an_amend_at_an_edit_stop_is_not_a_replay(self):
        self.write("app/b.rb", "b\n")
        self.commit("second")
        self.write("app/c.rb", "c\n")
        self.commit("third")
        environment = {**os.environ, "GIT_SEQUENCE_EDITOR": f"{sys.executable} {EDIT_FIRST_PICK}"}
        sh(self.root, "git", "rebase", "-q", "-i", "HEAD~2", env=environment)
        self.assertIsNone(gate.replay_marker(self.root))
        sh(self.root, "git", "rebase", "--abort")

    def test_with_no_phase_active_a_claude_commit_claims_none(self):
        self.write("app/a.rb", "one\nseven\nthree\n")
        self.commit(f"copied from an earlier one\n\n[skip ci]\n\nPhase: 2\n{CLAUDE_TRAILER}")
        self.assertNotIn("Phase:", self.body())
        self.assertNotIn("[skip ci]", self.body())

    def test_an_amend_with_no_phase_active_claims_none_either(self):
        self.write("app/a.rb", "one\nten\nthree\n")
        self.commit(f"work\n\n{CLAUDE_TRAILER}")
        message = self.directory / "amended"
        message.write_text(f"work\n\nPhase: 3\n{CLAUDE_TRAILER}\n")
        sh(self.root, sys.executable, str(GATE_FILE), "commit-msg", str(message), "commit", "HEAD")
        self.assertNotIn("Phase:", message.read_text())

    def test_with_no_phase_active_the_owners_own_trailer_stays(self):
        self.write("app/a.rb", "one\neight\nthree\n")
        self.commit("the owner's commit\n\nPhase: 2")
        self.assertIn("Phase: 2", self.body())

    def test_a_skip_keyword_outside_the_loop_is_refused(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nnine\nthree\n")
        message = self.directory / "message"
        message.write_text(f"work\n\n[skip ci]\n\n{CLAUDE_TRAILER}\n")
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "-c", "core.hooksPath=/dev/null", "commit", "-q", "-F", str(message))
        violations = gate.commit_violations(self.root, self.head())
        self.assertTrue(violations and "outside the phase loop" in violations[0], violations)

    def test_a_squash_keeps_the_highest_phase(self):
        message = self.directory / "squash"
        message.write_text("first\n\n[skip ci]\n\nPhase: 2\n\nsecond\n\nPhase: 7\n")
        gate.keep_highest_phase(self.root, message)
        self.assertEqual(gate.PHASE_TRAILER_LINE.findall(message.read_text()), ["7"])
        self.assertNotIn("[skip ci]", message.read_text())

    def test_the_replay_rules(self):
        def marker(name):
            location = Path(sh(self.root, "git", "rev-parse", "--git-path", name).stdout.strip())
            return location if location.is_absolute() else self.root / location
        marker("REBASE_HEAD").write_text("x\n")
        marker("CHERRY_PICK_HEAD").write_text("x\n")
        self.assertIsNone(gate.replay_marker(self.root), "outside a rebase both are new commits")
        marker("rebase-merge").mkdir()
        marker("CHERRY_PICK_HEAD").unlink()
        marker("rebase-merge/amend").write_text("x\n")
        self.assertIsNone(gate.replay_marker(self.root))
        with mock.patch.dict(os.environ, {"GIT_REFLOG_ACTION": "rebase (reword)"}):
            self.assertEqual(gate.replay_marker(self.root), "GIT_REFLOG_ACTION=rebase (reword)")

    def test_an_indented_claude_trailer_counts(self):
        squashed = f"Squashed commit of the following:\n\n    {CLAUDE_TRAILER}\n"
        self.assertTrue(gate.CLAUDE_TRAILER_PATTERN.search(squashed))


class ChangeIdentityTest(ScratchRepository):
    """21 refused pushes were proofs called stale after a rebase that changed nothing in the change."""

    def test_a_clean_rebase_keeps_the_identity_and_an_edit_changes_it(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        self.write("app/new.rb", "added\n")
        self.write("app/logo.png", "\x00\x01binary")
        tip = self.commit("change")
        before = gate.commit_change_identity(self.root, tip)
        sh(self.root, "git", "checkout", "-q", "main")
        self.write("app/a.rb", "zero\none\ntwo\nthree\n")
        self.commit("upstream moves the context")
        self.publish_main()
        sh(self.root, "git", "checkout", "-q", "feature")
        sh(self.root, "git", "rebase", "-q", "main")
        self.assertEqual(gate.commit_change_identity(self.root, self.head()), before)
        self.write("app/a.rb", "zero\none\nTWO again\nthree\n")
        self.assertNotEqual(gate.commit_change_identity(self.root, self.commit("a real edit")), before)

    def test_the_working_tree_identity_matches_the_committed_one(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        uncommitted = self.identity_now()
        self.assertEqual(gate.commit_change_identity(self.root, self.commit("change")), uncommitted)
        self.assertEqual(self.identity_now(), uncommitted)

    def test_a_record_without_an_identity_is_judged_by_its_digest(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        legacy = {"patch_sha": gate.patch_digest(gate.snapshot_patch(self.root))}
        self.assertTrue(gate.evidence_is_fresh(self.root, legacy))
        self.assertTrue(gate.evidence_is_fresh(self.root, gate.evidence_stamp(self.root)))
        self.assertFalse(gate.evidence_is_fresh(self.root, {"change_id": "something else"}))

    def test_whitespace_inside_a_line_is_part_of_the_change(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nx = 'a b'\nthree\n")
        spaced = self.identity_now()
        self.write("app/a.rb", "one\nx = 'ab'\nthree\n")
        self.assertNotEqual(self.identity_now(), spaced)

    def test_configuration_and_stylesheets_count_and_documentation_does_not(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("docs/notes.md", "a note left in the worktree\n")
        self.assertEqual(self.identity_now(), gate.EMPTY_CHANGE_IDENTITY)
        self.write("config/limits.yml", "limit: 10\n")
        with_yaml = self.identity_now()
        self.assertNotEqual(with_yaml, gate.EMPTY_CHANGE_IDENTITY)
        self.write("app/site.css", ".hidden { display: none }\n")
        self.assertNotEqual(self.identity_now(), with_yaml)

    def two_classes(self, guard_in):
        alpha = "    return if a.nil?\n" if guard_in == "Alpha" else ""
        beta = "    return if a.nil?\n" if guard_in == "Beta" else ""
        return f"class Alpha\n  def x\n{alpha}    one\n  end\nend\nclass Beta\n  def y\n{beta}    two\n  end\nend\n"

    def test_a_line_moved_to_another_class_changes_it(self):
        self.write("app/two.rb", self.two_classes(None))
        self.commit("two classes")
        self.publish_main()
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/two.rb", self.two_classes("Alpha"))
        in_alpha = self.identity_now()
        self.write("app/two.rb", self.two_classes("Beta"))
        self.assertNotEqual(self.identity_now(), in_alpha)

    def test_a_method_added_upstream_above_the_change_keeps_it(self):
        self.write("app/one.rb", "class Alpha\n  def x\n    one\n  end\n\n  def z\n    three\n  end\nend\n")
        self.commit("one class")
        self.publish_main()
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/one.rb", "class Alpha\n  def x\n    one\n  end\n\n  def z\n    three\n    four\n  end\nend\n")
        before = gate.commit_change_identity(self.root, self.commit("change z"))
        sh(self.root, "git", "checkout", "-q", "main")
        self.write("app/one.rb", "class Alpha\n  def x\n    one\n  end\n\n  def w\n    two\n  end\n\n"
                                 "  def z\n    three\n  end\nend\n")
        self.commit("upstream adds w")
        self.publish_main()
        sh(self.root, "git", "checkout", "-q", "feature")
        sh(self.root, "git", "rebase", "-q", "main")
        self.assertEqual(gate.commit_change_identity(self.root, self.head()), before)

    def test_a_move_before_git_add_matches_the_committed_rename(self):
        self.write("app/old_name.rb", "class Old\n  def x\n    1\n  end\nend\n")
        self.commit("a file to move")
        self.publish_main()
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        (self.root / "app/old_name.rb").rename(self.root / "app/new_name.rb")
        moved = self.identity_now()
        self.assertEqual(gate.commit_change_identity(self.root, self.commit("rename")), moved)

    def test_a_non_ascii_path_and_unusual_prefixes_still_name_the_file(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/café.rb", "x = 1\n")
        plain = self.identity_now()
        self.assertNotEqual(plain, gate.EMPTY_CHANGE_IDENTITY)
        paths = [gate.block_path(block) for block in gate.patch_blocks(gate.snapshot_patch(self.root))]
        self.assertIn("app/café.rb", paths)
        sh(self.root, "git", "config", "diff.mnemonicPrefix", "true")
        sh(self.root, "git", "config", "diff.noprefix", "true")
        self.assertEqual(self.identity_now(), plain)

    def test_a_missing_base_is_no_identity(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        tip = self.commit("change")
        sh(self.root, "git", "update-ref", "-d", "refs/remotes/origin/main")
        gate.PROCESS_MEMO.clear()
        self.assertIsNone(gate.commit_change_identity(self.root, tip, "origin/main"))


class ReferencesReuseTest(ScratchRepository):
    """The stored references report outlived a YAML fix, because the old identity ignored YAML."""

    def test_the_stored_report_is_reused_only_for_the_same_change(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("config/names.yml", "name: old\n")
        fresh = []

        def compute(root, names=None):
            fresh.append(1)
            return {"base": gate.diff_base(root), "change_id": gate.change_identity(root), "verdict": "Used"}
        with mock.patch.object(gate, "references_report", compute):
            gate.references_report_for(self.root)
            gate.PROCESS_MEMO.clear()
            gate.references_report_for(self.root)
            self.assertEqual(len(fresh), 1)
            self.write("config/names.yml", "name: new\n")
            gate.PROCESS_MEMO.clear()
            gate.references_report_for(self.root)
            self.assertEqual(len(fresh), 2)


class TipFreshnessTest(ScratchRepository):
    """The pushed tip used to be judged as a whole, so the owner's own commit on top refused the push."""

    def claude_commit(self, path, text, proof_id):
        self.write(path, text)
        return self.commit(f"work\n\n{gate.PROOF_ID_TRAILER}: {proof_id}\n{CLAUDE_TRAILER}")

    def record_proof(self, proof_id, sha, base_ref="origin/main"):
        meta = {"change_id": gate.commit_change_identity(self.root, sha), "base_ref": base_ref}
        gate.write_json(gate.runs_directory(self.root) / proof_id / gate.META_FILE_NAME, meta)

    def test_a_later_commit_that_owes_no_proof_is_not_held_against_it(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        proven = self.claude_commit("app/a.rb", "one\nTWO\nthree\n", "run1")
        self.record_proof("run1", proven)
        self.write("app/b.rb", "the owner's own code\n")
        owner = self.commit("the owner's commit")
        self.assertEqual(gate.tip_freshness_violations(self.root, [owner, proven]), [])

    def test_code_changed_under_the_proof_is_refused(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        proven = self.claude_commit("app/a.rb", "one\nTWO\nthree\n", "run1")
        self.record_proof("run1", proven)
        changed = self.claude_commit("app/a.rb", "one\nTWO and more\nthree\n", "run1")
        violations = gate.tip_freshness_violations(self.root, [changed, proven])
        self.assertEqual(len(violations), 1)
        self.assertIn("proved a different change", violations[0])

    def test_a_missing_base_is_refused_not_skipped(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        proven = self.claude_commit("app/a.rb", "one\nTWO\nthree\n", "run1")
        self.record_proof("run1", proven)
        sh(self.root, "git", "update-ref", "-d", "refs/remotes/origin/main")
        gate.PROCESS_MEMO.clear()
        violations = gate.tip_freshness_violations(self.root, [proven])
        self.assertEqual(len(violations), 1)
        self.assertIn("no merge-base", violations[0])

    def record_working_tree(self, proof_id, base_ref="origin/main"):
        gate.PROCESS_MEMO.clear()
        snapshot = gate.snapshot_patch(self.root, context_lines=gate.ZERO_CONTEXT)
        meta = {"change_id": gate.patch_identity(snapshot), "path_ids": gate.path_identities(snapshot),
                "base_ref": base_ref, "base_sha": gate.diff_base(self.root)}
        gate.write_json(gate.runs_directory(self.root) / proof_id / gate.META_FILE_NAME, meta)

    def test_an_untracked_file_that_is_never_committed_does_not_refuse(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        self.write("bench/preview.png", "\x00png")
        self.record_working_tree("run1")
        (self.root / "bench/preview.png").unlink()
        proven = self.claude_commit("app/a.rb", "one\nTWO\nthree\n", "run1")
        self.assertEqual(gate.tip_freshness_violations(self.root, [proven]), [])

    def test_a_spec_committed_after_the_code_belongs_to_the_proof(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        self.write("spec/a_spec.rb", "it 'works'\n")
        self.record_working_tree("run1")
        (self.root / "spec/a_spec.rb").unlink()
        code = self.claude_commit("app/a.rb", "one\nTWO\nthree\n", "run1")
        spec = self.claude_commit("spec/a_spec.rb", "it 'works'\n", "run1")
        self.assertEqual(gate.tip_freshness_violations(self.root, [spec, code]), [])

    def test_a_pruned_parent_branch_falls_back_to_the_stored_base(self):
        sh(self.root, "git", "checkout", "-q", "-b", "parent")
        self.write("app/parent.rb", "the parent's change\n")
        parent = self.commit("parent")
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/a.rb", "one\nTWO\nthree\n")
        gate.PROCESS_MEMO.clear()
        snapshot = gate.snapshot_patch(self.root, parent, context_lines=gate.ZERO_CONTEXT)
        meta = {"change_id": gate.patch_identity(snapshot), "base_ref": "origin/parent-that-was-pruned", "base_sha": parent}
        gate.write_json(gate.runs_directory(self.root) / "run1" / gate.META_FILE_NAME, meta)
        proven = self.claude_commit("app/a.rb", "one\nTWO\nthree\n", "run1")
        self.assertEqual(gate.tip_freshness_violations(self.root, [proven]), [])

    def test_a_code_file_with_a_non_ascii_name_owes_a_proof(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        self.write("app/café.rb", "x = 1\n")
        self.assertTrue(gate.touches_code(self.root, self.commit(f"accent\n\n{CLAUDE_TRAILER}")))

    def test_a_latin_1_file_does_not_crash_the_identity(self):
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        (self.root / "app/latin.csv").write_bytes(b"caf\xe9\n")
        self.assertNotEqual(self.identity_now(), gate.EMPTY_CHANGE_IDENTITY)

    def test_a_line_moved_to_another_method_changes_the_identity(self):
        self.write("app/one.rb", "class Alpha\n  def x\n    one\n  end\n\n  def y\n    two\n  end\nend\n")
        self.commit("two methods")
        self.publish_main()
        sh(self.root, "git", "checkout", "-q", "-b", "feature")
        guard = "    return if a.nil?\n"
        self.write("app/one.rb", f"class Alpha\n  def x\n{guard}    one\n  end\n\n  def y\n    two\n  end\nend\n")
        in_x = self.identity_now()
        self.write("app/one.rb", f"class Alpha\n  def x\n    one\n  end\n\n  def y\n{guard}    two\n  end\nend\n")
        self.assertNotEqual(self.identity_now(), in_x)


class SkipReasonTest(unittest.TestCase):
    """One skip went through with the reason `<reason>`, copied from the gate's own hint."""

    def setUp(self):
        self.addCleanup(gate.PROCESS_MEMO.clear)

    def test_a_placeholder_a_short_reason_and_the_gates_own_text_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace"
            trace.mkdir()
            (trace / "2026-10-16.jsonl").write_text(json.dumps(
                {"at": "2026-10-16T10:00:00Z", "command": "pre-push",
                 "stderr": "gate: only the user can skip, then push again within 15 minutes."}) + "\n")
            self.enterContext(mock.patch.object(gate, "SKIP_TOKEN_FILE", Path(directory) / "token"))
            self.enterContext(mock.patch.object(gate, "TRACE_DIRECTORY", trace))
            gate.PROCESS_MEMO.clear()
            for reason in (["<reason>"], ["reason"], [], ["too short"], ["<why this push may bypass the gate>"],
                           ["then push again within 15 minutes"]):
                with self.assertRaises(SystemExit), mock.patch("sys.stdout"):
                    gate.skip_once(reason)
                self.assertFalse((Path(directory) / "token").exists())
            with mock.patch("sys.stdout"):
                gate.skip_once(["restack onto main after 1059 merged"])
            self.assertTrue((Path(directory) / "token").exists())


class OwnerWordsTest(ScratchRepository):
    """`deviation --decided-by user --evidence "ok"` used to let the session approve its own work."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(gate, "OWNER_WORDS_DIRECTORY", self.directory / "words"))
        self.enterContext(mock.patch.object(gate, "TRACE_DIRECTORY", self.directory / "trace"))
        (self.directory / "trace").mkdir()

    def say(self, text, source="prompt", cwd=None):
        gate.store_owner_words({"session_id": "s1", "cwd": str(cwd or self.root)}, source, text)
        gate.PROCESS_MEMO.clear()

    def said(self, quote, **window):
        gate.PROCESS_MEMO.clear()
        return gate.owner_said(quote, self.root, **window)

    def printed(self, at, stdout, command="comment-decide"):
        with open(self.directory / "trace" / f"{at[:10]}.jsonl", "a") as handle:
            handle.write(json.dumps({"at": at, "command": command, "stdout": stdout}) + "\n")

    def test_whole_sentences_count_and_a_fragment_or_a_summary_does_not(self):
        self.say("Drop the icon_url check. The presenter spec covers it now.")
        self.assertTrue(self.said("Drop the icon_url check."))
        self.assertTrue(self.said("drop the  icon_url check.\nthe presenter spec covers it now."))
        self.assertFalse(self.said("the user agreed to drop the check"))
        self.assertFalse(self.said("ok"))

    def test_a_fragment_that_says_the_opposite_is_refused(self):
        self.say("do not waive this unit, it needs a spec")
        self.assertFalse(self.said("waive this unit, it needs a spec"))
        self.assertTrue(self.said("do not waive this unit, it needs a spec"))

    def test_curly_quotes_and_composed_letters_are_the_same_words(self):
        self.say("don’t refuse the café change")
        self.assertTrue(self.said("don't refuse the café change"))
        self.assertTrue(self.said('"  don\'t refuse the café change  "'))

    def test_daemon_lines_and_pasted_text_are_not_the_owners_words(self):
        daemon = "New feedback on PR #1111 while phase 7 is still open: read @/Users/x/.local/state/phased/pr/brief.md"
        self.say(daemon)
        self.say('look at this <pasted_content id="65c5">Should these come from ENVx rather than constants'
                 '</pasted_content id="65c5"> please')
        self.say('and this one <pasted_content id="ff13">Is this guard reachable at all, or dead code')
        self.say("the brief at ~/.local/state/phased/pr-1111/brief.md is out of date, ignore it")
        self.assertFalse(self.said(daemon))
        self.assertFalse(self.said("Should these come from ENVx rather than constants"))
        self.assertFalse(self.said("Is this guard reachable at all, or dead code"))
        self.assertTrue(self.said("look at this please"))
        self.assertTrue(self.said("the brief at ~/.local/state/phased/pr-1111/brief.md is out of date, ignore it"))

    def test_text_the_gate_printed_first_is_refused(self):
        self.printed("2026-10-16T10:00:00Z", "gate: run skip-once with a reason of your own words", command="pre-push")
        self.say("gate: run skip-once with a reason of your own words")
        self.assertFalse(self.said("gate: run skip-once with a reason of your own words"))

    def test_the_gate_echoing_a_decision_afterwards_does_not_undo_it(self):
        self.say("fix the scrubber comment in this pull request")
        record = gate.owner_words()[0]
        self.printed("2999-01-01T00:00:00Z", 'their words: "fix the scrubber comment in this pull request"')
        self.printed("2026-01-01T00:00:00Z", 'fix the scrubber comment in this pull request', command="trace")
        self.printed("2999-01-01T00:00:00Z", "- fix the scrubber comment in this pull request", command="review-section")
        self.assertTrue(self.said("fix the scrubber comment in this pull request", after=record["at"]))

    def test_the_words_must_fall_inside_the_window(self):
        self.say("waive the hidden overlay class, it is styling only")
        record = gate.owner_words()[0]
        self.assertFalse(self.said("waive the hidden overlay class, it is styling only", after="2999-01-01T00:00:00Z"))
        self.assertTrue(self.said("waive the hidden overlay class, it is styling only", after=record["at"]))
        self.assertFalse(self.said("waive the hidden overlay class, it is styling only", before="2000-01-01T00:00:00Z"))

    def test_words_typed_in_another_repository_do_not_count(self):
        elsewhere = self.directory / "elsewhere"
        elsewhere.mkdir()
        self.say("record the avatar column as a design decision", cwd=elsewhere)
        self.assertFalse(self.said("record the avatar column as a design decision"))
        linked = self.directory / "linked"
        sh(self.root, "git", "worktree", "add", "-q", "-b", "other", str(linked))
        self.say("record the avatar column as a design decision", cwd=linked / "platform")
        self.assertTrue(self.said("record the avatar column as a design decision"))

    def test_an_option_label_is_a_choice_and_not_the_owners_words(self):
        options = [{"label": "Refuse the push"}, {"label": "Report only"}]
        labels = gate.option_labels({"questions": [{"options": options}]})
        found = gate.answers_in({"answers": {"Q?": "Refuse the push", "R?": "Refuse the push, Report only",
                                             "S?": "only once we measured it"},
                                 "annotations": {"Q?": {"notes": "and log it", "preview": "session text"}}}, labels)
        self.assertEqual(found, [("Refuse the push", "option"), ("Refuse the push, Report only", "option"),
                                 ("only once we measured it", "answer"), ("and log it", "note")])
        self.assertEqual(gate.answers_in("User answered: \"Q?\"=\"anything the session wrote\"", labels), [])
        self.say("Refuse the push", source=gate.OWNER_SOURCE_OPTION)
        self.assertFalse(self.said("Refuse the push"))

    def test_a_decision_from_before_the_store_holds_only_when_it_was_listed_at_deploy(self):
        listed = {"evidence": "x", "recorded_at": "2026-09-01T00:00:00Z"}
        backdated = {"evidence": "a forged waiver", "recorded_at": "2026-09-01T00:00:00Z"}
        grandfathered = self.directory / "grandfathered.json"
        grandfathered.write_text(json.dumps([gate.decision_digest(listed)]))
        self.enterContext(mock.patch.object(gate, "GRANDFATHERED_DECISIONS_FILE", grandfathered))
        gate.PROCESS_MEMO.clear()
        self.assertTrue(gate.owner_decision_holds(listed))
        self.assertFalse(gate.owner_decision_holds(backdated))
        self.assertFalse(gate.owner_decision_holds({"evidence": "never typed by anyone",
                                                    "recorded_at": "2999-01-01T00:00:00Z", "worktree": str(self.root)}))
        self.assertFalse(gate.owner_decision_holds({"evidence": "x", "recorded_at": "2999-01-01T00:00:00Z"}))
        self.say("the overlay class is styling only, let it through")
        self.assertTrue(gate.owner_decision_holds({"evidence": "the overlay class is styling only, let it through",
                                                   "recorded_at": "2999-01-01T00:00:00Z", "worktree": str(self.root)}))

    def test_the_store_is_readable_by_the_owner_only(self):
        self.say("keep this private")
        stored = next((self.directory / "words").glob("*.jsonl"))
        self.assertEqual(stored.stat().st_mode & 0o777, gate.PRIVATE_FILE_MODE)

    def test_sentence_starts_the_owner_writes(self):
        self.say("C4: yes, fix it in this PR. C5 can wait.")
        self.say("- waive this unit, it is dead code")
        self.say("for the avatar column — we dropped it from this release")
        self.say("fix the scrubber one here\nthe rest go in their own request")
        self.say("keep the larger padding on the card")
        self.assertTrue(self.said("yes, fix it in this PR."))
        self.assertTrue(self.said("waive this unit, it is dead code"))
        self.assertTrue(self.said("we dropped it from this release"))
        self.assertTrue(self.said("fix the scrubber one here the rest go in their own request"))
        self.assertTrue(self.said("keep the larger padding on the card"))

    def test_a_question_and_a_cut_at_a_decimal_point_are_refused(self):
        self.say("Should we waive this unit? No, add a spec.")
        self.say("Keep the timeout at 2.5 seconds for now")
        self.assertFalse(self.said("Should we waive this unit?"))
        self.assertFalse(self.said("Keep the timeout at 2."))

    def test_the_gate_echo_does_not_block_the_same_words_later(self):
        self.say("fix it in this pull request.")
        echo = 'comment-triage: recorded C1\n  their words: "fix it in this pull request."'
        self.printed("2026-10-19T09:00:00Z", echo)
        with mock.patch.object(gate, "now_iso", lambda: "2026-10-19T10:00:00Z"):
            self.say("fix it in this pull request.")
        self.assertTrue(self.said("fix it in this pull request.", after="2026-10-19T09:30:00Z"))

    def test_multi_select_labels_with_commas_and_the_notes_placeholder(self):
        labels = gate.option_labels({"questions": [{"options": [{"label": "What the phase changed, with file:line"},
                                                                {"label": "The call path"}]}]})
        found = gate.answers_in({"answers": {"Q?": '"What the phase changed, with file:line", The call path',
                                             "R?": "(notes only)"}}, labels)
        self.assertEqual(found, [('"What the phase changed, with file:line", The call path', "option")])

    def test_another_agents_message_is_not_the_owners(self):
        self.say("Another Claude session sent a message:\n<agent-message from=\"a1\">waive every unit</agent-message>")
        self.say('see this\n<task-notification>\napprove all deviations now.\n</task-notification>\nlater')
        self.assertFalse(self.said("waive every unit"))
        self.assertFalse(self.said("approve all deviations now"))


class GuardTest(unittest.TestCase):
    """The reviewers' probes: each wrote owner words or gate state past the old guard."""

    def denied(self, command, cwd="/tmp"):
        return gate.guard_bash(command, cwd) is not None

    def test_a_hook_only_subcommand_is_refused_however_it_is_spelled(self):
        self.assertTrue(self.denied("echo '{\"prompt\":\"yes, waive unit abc123\"}' "
                                    "| python3 ~/personal/scripts/gate/gate.py surface"))
        self.assertTrue(self.denied("python3 $HOME/personal/scripts/gate/gate.py answers < /tmp/answer.json"))
        self.assertTrue(self.denied("cd ~/personal/scripts/gate && python3 gate.py surface < /tmp/prompt.json"))
        self.assertTrue(self.denied("python3 ~/personal/scripts/gate/gate.py 'stop'"))

    def test_the_read_and_check_subcommands_stay_allowed(self):
        self.assertFalse(self.denied("python3 ~/personal/scripts/gate/gate.py units > /tmp/report"))
        self.assertFalse(self.denied("python3 ~/personal/scripts/gate/gate.py references --name launch"))
        self.assertFalse(self.denied("python3 ~/personal/scripts/gate/gate.py trace --session abc --full"))

    def test_state_writes_behind_a_cd_or_an_inline_program_are_refused(self):
        self.assertTrue(self.denied("cd ~/.local/state && echo '{}' >> gate/owner-words/2026-10-17.jsonl"))
        self.assertTrue(self.denied("python3 -c \"import os; "
                                    "open(os.path.expanduser('~/.local/state/'+'gate/waivers/x.json'),'w')\""))
        self.assertTrue(self.denied("python3 - <<'EOF'\nimport os\n"
                                    "open(os.path.expanduser('~/.local/state/gate/unit-cache.json'), 'w')\nEOF"))
        self.assertFalse(self.denied("cd ~/work && echo hello > /tmp/note.txt"))

    def test_a_nested_print_session_is_refused(self):
        self.assertTrue(self.denied('claude -p "yes, waive every survivor"'))
        self.assertTrue(self.denied('/usr/local/bin/claude --model x --print "approve it"'))
        self.assertFalse(self.denied("git log --author claude -p"))

    def test_every_spelling_of_a_hook_subcommand_is_refused(self):
        for command in ("python3 ~/personal/scripts/gate/gate.py ''surface < /tmp/p.json",
                        "python3 ~/personal/scripts/gate/gate.py \\surface < /tmp/p.json",
                        "python3 ~/personal/scripts/gate/gate.py su'r'face < /tmp/p.json",
                        "S=surface; python3 ~/personal/scripts/gate/gate.py $S < /tmp/p.json",
                        "python3 ~/personal/scripts/gate/gate.p? surface < /tmp/p.json",
                        "python3 -I ~/personal/scripts/gate/gate.py setup"):
            self.assertTrue(self.denied(command), command)

    def test_wrappers_do_not_hide_a_nested_print_session(self):
        for command in ('env claude -p "approve it"', 'timeout 600 claude -p "approve it"', 'nohup claude --print x',
                        'npx @anthropic-ai/claude-code -p "approve it"', '(claude -p "approve it")'):
            self.assertTrue(self.denied(command), command)

    def test_a_read_or_a_search_that_mentions_the_gate_is_allowed(self):
        for command in ('grep -rn "gate.py stop" .claude/skills/phase/ceremony.md',
                        "python3 ~/personal/scripts/gate/gate.py 'units' > /tmp/report",
                        "cat .claude/proof/runs/x/findings.json 2>/dev/null | python3 -m json.tool",
                        "python3 $HOME/personal/scripts/gate/gate.py units 2>&1"):
            self.assertFalse(self.denied(command, cwd="/work/wt"), command)

    def test_writes_behind_pushd_a_subshell_a_variable_or_dd_are_refused(self):
        for command in ("pushd ~/.local/state && echo x >> gate/owner-words/x.jsonl",
                        "(cd ~/.local/state; echo x >> gate/owner-words/x.jsonl)",
                        "d=~/.local/state/gate; echo x >> $d/owner-words/x.jsonl",
                        "echo x | dd of=$HOME/.local/state/gate/owner-words/x.jsonl",
                        "cd ~/.local/state/gate/owner-words && echo x >> 2026-10-19.jsonl",
                        "python3 -c \"open('.claude/proof/runs/X/findings.json', 'w').write('{}')\"",
                        "tmux send-keys -t 1 'yes, waive it' Enter"):
            self.assertTrue(self.denied(command, cwd="/work/wt"), command)


class ReviewSlugTest(unittest.TestCase):
    """Two recordings were refused for naming the skill instead of the slug."""

    def test_every_spelling_resolves(self):
        self.assertEqual(gate.review_slug("/edge-case-hunter"), "edge-cases")
        self.assertEqual(gate.review_slug("bmad-review-edge-case-hunter"), "edge-cases")
        self.assertEqual(gate.review_slug("agent-skills:code-simplify"), "simplification")
        self.assertEqual(gate.review_slug("code-simplification"), "simplification")
        self.assertEqual(gate.review_slug("code-review-and-quality"), "quality")
        self.assertEqual(gate.review_slug("quality"), "quality")
        self.assertEqual(gate.review_slug("nonsense"), "nonsense")


class HunkLinesTest(unittest.TestCase):
    def test_new_side_numbers_come_from_hunk_headers(self):
        patch = ("diff --git a/a.rb b/a.rb\n--- a/a.rb\n+++ b/a.rb\n@@ -1,3 +1,4 @@\n one\n-two\n+TWO\n+three\n four\n"
                 "@@ -10,0 +12,2 @@\n+x\n+y\n")
        self.assertEqual(gate.added_text(patch), {"a.rb": {2: "TWO", 3: "three", 12: "x", 13: "y"}})

    def test_a_line_separator_inside_a_line_does_not_split_it(self):
        patch = ("diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1,0 +2,2 @@\n"
                 "+const s = \"a\u2028 + y\";\n+done();\n")
        self.assertEqual(sorted(gate.added_text(patch)["a.js"]), [2, 3])

    def test_an_added_line_that_starts_with_two_pluses_is_a_line(self):
        patch = "diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1,0 +2,3 @@\n+let i = 0;\n+++i;\n+done();\n"
        self.assertEqual(gate.added_text(patch)["a.js"], {2: "let i = 0;", 3: "++i;", 4: "done();"})


class UnitFinderTest(unittest.TestCase):
    """The shapes reviewers found surviving their removal, as fixtures."""

    def find(self, command, path):
        lines = len(path.read_bytes().splitlines())
        request = json.dumps({"files": [{"path": str(path), "added_lines": list(range(1, lines + 1))}]})
        done = subprocess.run(command, input=request, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def kinds(self, found):
        return {(unit["kind"], unit["original"].strip(" ,\n")) for unit in found["units"]}

    def unparsed(self, command, name, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(content)
            self.assertEqual(self.find(command, path), {"units": [], "unparsed": [str(path)]})

    @unittest.skipUnless(shutil.which("ruby"), "ruby is not installed")
    def test_ruby_units(self):
        command = ["ruby", str(gate.RUBY_UNIT_FINDER)]
        units = self.kinds(self.find(command, FIXTURES / "presenter.rb"))
        self.assertIn(("entry", "icon_url: icon_url"), units)
        self.assertIn(("fallback", "event['organizer'] || {}"), units)
        self.assertIn(("guard", "return {} if solutions.empty?"), units)
        self.assertIn(("statement", "allow_specialist :show"), units)
        self.assertFalse(any(original == "private" for _kind, original in units))
        self.assertFalse(any(original.startswith("organizer =") for _kind, original in units))
        self.unparsed(command, "bad.rb", b"def broken(\n")
        self.unparsed(command, "latin.rb", b"x = '\xe9'\n")

    @unittest.skipUnless(HAS_TYPESCRIPT, "node or the typescript package is missing")
    def test_script_units(self):
        command = ["node", str(gate.SCRIPT_UNIT_FINDER), str(gate.typescript_package())]
        units = self.kinds(self.find(command, FIXTURES / "Tile.tsx"))
        self.assertIn(("fallback", 'solution.name ?? "untitled"'), units)
        self.assertTrue(any(kind == "guard" and original.startswith("canEdit &&") for kind, original in units))
        self.assertIn(("attribute", 'className="hidden group-hover/icon:flex"'), units)
        self.assertIn(("entry", "icon_url: solution.icon_url"), units)
        self.assertFalse(any(original.startswith("key=") for _kind, original in units))
        self.assertFalse(any(original.startswith(("import", "type ")) for _kind, original in units))
        self.unparsed(command, "bad.js", b"const x = 'caf\xe9';\nconst y = a || b;\n")

    @unittest.skipUnless(HAS_TYPESCRIPT, "node or the typescript package is missing")
    def test_script_units_carry_their_scope_and_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "two.ts"
            path.write_text("export function first() {\n  return a || b;\n}\nexport const second = () => a || b;\n")
            found = self.find(["node", str(gate.SCRIPT_UNIT_FINDER), str(gate.typescript_package())], path)
            fallbacks = [(unit["scope"], unit["start_column"]) for unit in found["units"] if unit["kind"] == "fallback"]
            self.assertEqual(fallbacks, [("first", 9), ("second", 28)])

    @unittest.skipUnless(HAS_TYPESCRIPT, "node or the typescript package is missing")
    def test_script_offsets_are_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wide.ts"
            path.write_text("const label = 'é\U0001f600';\nconst y = a || b;\n")
            found = self.find(["node", str(gate.SCRIPT_UNIT_FINDER), str(gate.typescript_package())], path)
            unit = next(each for each in found["units"] if each["kind"] == "fallback")
            self.assertEqual(path.read_bytes()[unit["start"]:unit["end"]], b"a || b")


class UnitBookkeepingTest(unittest.TestCase):
    def unit(self, start, original="return if x.nil?", path="platform/app/a.rb"):
        return {"path": path, "kind": "guard", "original": original, "replacement": "", "start": start}

    def test_identical_units_get_their_own_digests_that_survive_an_edit_above(self):
        first, second = gate.number_units([self.unit(10), self.unit(80)])
        self.assertNotEqual(first["digest"], second["digest"])
        moved = gate.number_units([self.unit(30), self.unit(100)])
        self.assertEqual([each["digest"] for each in moved], [first["digest"], second["digest"]])

    def test_lines_without_a_unit_are_listed_and_closing_lines_are_not(self):
        added = {"platform/app/a.rb": {3: "  LIMIT = 100", 4: "  return if x.nil?", 5: "  end", 6: "  # why", 7: ""}}
        units = [{"path": "platform/app/a.rb", "start_line": 4, "end_line": 4}]
        self.assertEqual([row["line"] for row in gate.unjudged_lines(added, units, ["platform/app/a.rb"])], [3])

    def test_a_ruby_file_outside_the_projects_is_listed_not_judged(self):
        lines = {"demo/script.rb": [1], "platform/app/a.rb": [1], "platform/app/views/x.erb": [1]}
        ruby, script, unread = gate.unit_files(list(lines), lines)
        self.assertEqual((ruby, script), (["platform/app/a.rb"], []))
        self.assertEqual(sorted(row["path"] for row in unread), ["demo/script.rb", "platform/app/views/x.erb"])

    def test_a_sibling_that_shares_the_prefix_is_outside_the_worktree(self):
        with mock.patch.object(gate, "GATE_WORKTREE", Path("/work/gate")):
            self.assertEqual(gate.gate_relative("/work/gate/platform/a.rb"), "platform/a.rb")
            self.assertIsNone(gate.gate_relative("/work/gate2/platform/a.rb"))

    def test_a_runner_that_wrote_no_report_makes_the_stage_an_error(self):
        baseline = {"red": {}, "unrun": []}
        summary = gate.unit_summary([], baseline, {}, [], ["platform/spec/a_spec.rb"], 0)
        self.assertEqual(summary["status"], gate.STATUS_ERROR)
        self.assertEqual(gate.unit_summary([], baseline, {}, [], [], 0)["status"], gate.STATUS_PASS)

    def test_a_stage_that_killed_nothing_proved_nothing(self):
        unmeasured = [{"status": gate.UNIT_UNMEASURED}, {"status": gate.UNIT_UNMEASURED}]
        self.assertEqual(gate.unit_stage_status(unmeasured, []), gate.STATUS_UNMEASURED)
        self.assertEqual(gate.unit_stage_status(unmeasured + [{"status": gate.UNIT_KILLED}], []), gate.STATUS_PASS)
        self.assertEqual(gate.unit_stage_status([{"status": gate.UNIT_WAIVED}], []), gate.STATUS_PASS)


def outcome(passed=(), failed=(), errors_outside=0, reported=True):
    return {"reported": reported, "passed": set(passed), "failed": set(failed), "errors_outside": errors_outside}


class JudgeUnitTest(unittest.TestCase):
    """A load error is not a kill: 7 of 8 sweep passes on PR 1111 were files that failed to load."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="gate-judge-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        (self.directory / "platform/app").mkdir(parents=True)
        self.target = self.directory / "platform/app/presenter.rb"
        self.target.write_bytes(b"{ name: name, icon_url: icon_url }\n")
        self.unit = {"path": "platform/app/presenter.rb", "start": 14, "end": 32, "replacement": "", "digest": "d1"}
        self.baseline = {"files": {"platform/spec/a_spec.rb": {"project": "platform", "passed": {"a[1:1]"}},
                                   "connector/spec/b_spec.rb": {"project": "connector", "passed": {"b[1:1]"}}}}
        self.enterContext(mock.patch.object(gate, "GATE_WORKTREE", self.directory))

    def judge(self, removed, covering=("platform/spec/a_spec.rb",)):
        calls = []

        def run(project, files, json_path, environment, log, with_coverage=False, fail_fast=False):
            calls.append((project, list(files), self.target.read_bytes()))
            return removed
        unit_run = gate.UnitRun(baseline=self.baseline, environment={}, directory=self.directory, log=lambda text: None,
                                context={}, cache={})
        with mock.patch.object(gate, "run_unit_specs", run):
            result = gate.judge_unit(self.unit, list(covering), unit_run)
        self.assertEqual(self.target.read_bytes(), b"{ name: name, icon_url: icon_url }\n")
        self.assertTrue(all(text == b"{ name: name,  }\n" for _project, _files, text in calls))
        return result, calls

    def test_a_green_example_turning_red_kills_the_unit(self):
        self.assertEqual(self.judge(outcome(failed={"a[1:1]"}))[0]["status"], gate.UNIT_KILLED)

    def test_a_load_error_is_not_a_kill(self):
        result = self.judge(outcome(errors_outside=1))[0]
        self.assertEqual((result["status"], result["runner_error"]), (gate.UNIT_UNMEASURED, False))

    def test_a_run_that_wrote_no_report_is_a_runner_error_not_a_survivor(self):
        result = self.judge(outcome(reported=False))[0]
        self.assertEqual((result["status"], result["runner_error"]), (gate.UNIT_UNMEASURED, True))

    def test_an_example_that_was_already_red_kills_nothing(self):
        self.assertEqual(self.judge(outcome(passed={"a[1:1]"}, failed={"a[9:9]"}))[0]["status"], gate.UNIT_SURVIVED)

    def test_everything_green_is_a_survivor(self):
        self.assertEqual(self.judge(outcome(passed={"a[1:1]"}))[0]["status"], gate.UNIT_SURVIVED)

    def test_examples_that_stopped_running_are_not_a_survivor(self):
        self.assertEqual(self.judge(outcome())[0]["status"], gate.UNIT_UNMEASURED)

    def test_each_project_runs_its_own_specs(self):
        _result, calls = self.judge(outcome(passed={"a[1:1]", "b[1:1]"}),
                                    covering=("platform/spec/a_spec.rb", "connector/spec/b_spec.rb"))
        self.assertEqual(sorted((project, files) for project, files, _text in calls),
                         [("connector", ["connector/spec/b_spec.rb"]), ("platform", ["platform/spec/a_spec.rb"])])


class CoveringSpecsTest(unittest.TestCase):
    def test_a_ruby_unit_is_covered_from_its_statement_line(self):
        unit = {"path": "platform/app/p.rb", "anchor_line": 5, "start_line": 6, "end_line": 7}
        ran = {"statements": {"platform/app/p.rb": [(5, 5, 2)]}, "functions": {}}
        elsewhere = {"statements": {"platform/app/p.rb": [(5, 5, 0), (40, 40, 1)]}, "functions": {}}
        entries = {"platform/spec/p_spec.rb": {"coverage": ran}, "platform/spec/q_spec.rb": {"coverage": elsewhere}}
        self.assertEqual(gate.covering_specs(unit, entries), ["platform/spec/p_spec.rb"])

    def test_a_script_unit_inside_a_callback_nobody_called_did_not_run(self):
        unit = {"path": "platform/app/javascript/T.tsx", "anchor_line": 150, "start_line": 200, "end_line": 200,
                "start_column": 20, "end_column": 26}
        statements = {"platform/app/javascript/T.tsx": [(150, 234, 1)]}
        called = {"platform/app/javascript/T.tsx": [(140, 0, 240, 1, 1), (199, 0, 201, 3, 1)]}
        never = {"platform/app/javascript/T.tsx": [(140, 0, 240, 1, 1), (199, 0, 201, 3, 0)]}
        entries = {"rendered-and-clicked.test.tsx": {"coverage": {"statements": statements, "functions": called}},
                   "rendered-only.test.tsx": {"coverage": {"statements": statements, "functions": never}}}
        self.assertEqual(gate.covering_specs(unit, entries), ["rendered-and-clicked.test.tsx"])

    def test_two_callbacks_on_one_line_are_told_apart_by_column(self):
        # a.map(x => x.y).filter(z => z || w): the first callback ran, the second never did.
        unit = {"path": "platform/app/javascript/T.tsx", "anchor_line": 7, "start_line": 7, "end_line": 7,
                "start_column": 29, "end_column": 35}
        coverage = {"statements": {"platform/app/javascript/T.tsx": [(7, 7, 1)]},
                    "functions": {"platform/app/javascript/T.tsx": [(7, 6, 7, 14, 1), (7, 24, 7, 35, 0)]}}
        self.assertEqual(gate.covering_specs(unit, {"t.test.tsx": {"coverage": coverage}}), [])


class ProveTest(unittest.TestCase):
    """The waive window opened at the proof's finish, before any unit report existed."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="gate-prove-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.run_directory = self.directory / "runs" / "run1"
        self.run_directory.mkdir(parents=True)
        (self.run_directory / gate.PATCH_FILE_NAME).write_text("")
        gate.write_json(self.run_directory / gate.META_FILE_NAME, {"id": "run1", "base_sha": "abc", "paths": []})
        gate.write_json(self.run_directory / gate.DECLARATION_FILE_NAME, {"proofs": []})
        self.enterContext(mock.patch.object(gate, "GATE_LOCK_FILE", self.directory / "lock"))
        self.enterContext(mock.patch.object(gate, "notify", lambda *arguments: None))
        self.enterContext(mock.patch.object(gate, "run_declared_proofs", lambda *arguments: gate.STATUS_PASS))

    def test_the_proof_is_final_and_the_units_pending_while_the_stage_runs(self):
        seen = {}

        def stage(*arguments, **options):
            seen.update(gate.read_json(self.run_directory / gate.FINDINGS_FILE_NAME))
            return {"status": gate.STATUS_SURVIVED, "units": [], "finished_at": "2999-01-01T00:00:00Z"}
        with mock.patch.object(gate, "unit_stage", stage):
            gate.prove(self.run_directory)
        findings = gate.read_json(self.run_directory / gate.FINDINGS_FILE_NAME)
        self.assertEqual((seen["status"], seen["units"]["status"]), (gate.STATUS_PASS, gate.STATUS_PENDING))
        self.assertEqual(findings["status"], gate.STATUS_PASS)
        self.assertLess(findings["finished_at"], findings["units"]["finished_at"])

    def test_the_waive_window_opens_when_the_unit_stage_finished(self):
        survivor = {"digest": "d1", "status": gate.UNIT_SURVIVED}
        gate.write_json(self.run_directory / gate.FINDINGS_FILE_NAME,
                        {"finished_at": "2026-10-17T10:00:00Z",
                         "units": {"finished_at": "2026-10-17T10:30:00Z", "units": [survivor]}})
        with mock.patch.object(gate, "runs_directory", lambda root: self.directory / "runs"):
            self.assertEqual(gate.first_reported(self.directory, "d1"), "2026-10-17T10:30:00Z")






class UnitStageTest(unittest.TestCase):
    """The unit stage cases the second review reproduced."""

    def test_a_config_file_outside_the_test_tree_is_listed_not_judged(self):
        lines = {"platform/vite.config.ts": [1], "platform/app/javascript/T.tsx": [1]}
        _ruby, script, unread = gate.unit_files(list(lines), lines)
        self.assertEqual(script, ["platform/app/javascript/T.tsx"])
        self.assertEqual([row["path"] for row in unread], ["platform/vite.config.ts"])

    def test_identical_units_in_two_methods_keep_their_digests_when_a_third_is_added(self):
        def unit(start, scope):
            return {"path": "p.rb", "kind": "guard", "original": "return if x.nil?", "replacement": "", "start": start,
                    "scope": scope}
        before = gate.number_units([unit(10, "A#x"), unit(80, "A#y")])
        after = gate.number_units([unit(5, "A#w"), unit(20, "A#x"), unit(90, "A#y")])
        self.assertEqual([each["digest"] for each in before], [each["digest"] for each in after[1:]])

    def run_with(self, outcome, deadline=None):
        directory = Path(tempfile.mkdtemp(prefix="gate-baseline-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        unit_run = gate.UnitRun(baseline={}, environment={}, directory=directory, log=lambda text: None, context={},
                                cache={}, deadline=deadline)
        with mock.patch.object(gate, "run_unit_specs", lambda *arguments, **options: outcome):
            return gate.unit_baseline({"platform": ["platform/spec/a_spec.rb"]}, unit_run)

    def test_a_report_without_coverage_is_a_runner_error(self):
        baseline = self.run_with(gate.run_outcome(True, {"a[1:1]"}, coverage=None))
        self.assertEqual(baseline["runner_errors"], ["platform/spec/a_spec.rb"])

    def test_files_left_by_the_budget_make_units_unmeasured_not_unexecuted(self):
        baseline = self.run_with(gate.run_outcome(True, {"a[1:1]"}, coverage={"statements": {}, "functions": {}}),
                                 deadline=0)
        self.assertEqual(baseline["unrun"], ["platform/spec/a_spec.rb"])
        unit = {"path": "p.rb", "kind": "guard", "start_line": 1, "end_line": 1, "anchor_line": 1, "original": "x",
                "replacement": "", "digest": "d"}
        self.assertEqual(gate.unjudged_record(unit, baseline)["status"], gate.UNIT_UNMEASURED)

    def test_a_cache_value_that_is_not_an_object_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            cache.write_text(json.dumps({"k1": [], "k2": {"at": "2999-01-01T00:00:00Z", "status": "killed"}}))
            with mock.patch.object(gate, "UNIT_CACHE_FILE", cache):
                self.assertEqual(list(gate.read_unit_cache()), ["k2"])

    @unittest.skipUnless(shutil.which("ruby"), "ruby is not installed")
    def test_a_heredoc_statement_is_not_a_unit_and_units_carry_their_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "h.rb"
            path.write_text("class Mailer\n  def send_it\n    log(<<~TEXT)\n      sent\n    TEXT\n"
                            "    notify\n  end\nend\n")
            request = json.dumps({"files": [{"path": str(path), "added_lines": [3, 4, 5, 6]}]})
            done = subprocess.run(["ruby", str(gate.RUBY_UNIT_FINDER)], input=request, capture_output=True, text=True)
            units = json.loads(done.stdout)["units"]
            self.assertEqual([(unit["original"], unit["scope"]) for unit in units], [("notify", "Mailer#send_it")])


class PhaseLoopTest(unittest.TestCase):
    def test_no_line_when_no_phase_is_active(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(gate.phase_protocol_line(Path(directory)))


if __name__ == "__main__":
    unittest.main()
