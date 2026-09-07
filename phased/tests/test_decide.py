import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decide import AddressComments, Facts, MarkDone, Review, StartPhase, Thread, decide  # noqa: E402

ME = "aman-kumar"
HEAD = "a" * 40
OLD = "b" * 40


def state(phase=2, status="waiting_approval", handoff_at="2026-09-07T10:00:00Z", consumed=()):
    return {"phase": phase, "status": status, "handoff_at": handoff_at, "consumed_approval_ids": list(consumed)}


def approval(commit=HEAD, at="2026-09-07T11:00:00Z", author=ME, review_id="R1", review_state="APPROVED"):
    return Review(review_id, author, review_state, at, commit)


def facts(reviews=(), threads=(), merged=False, closed=False):
    return Facts(HEAD, "2026-09-07T09:00:00Z", True, closed, merged, tuple(reviews), tuple(threads))


def thread(resolved=False, outdated=False, at="2026-09-07T10:30:00Z", author="tushar", thread_id="T1"):
    return Thread(thread_id, resolved, outdated, at, author)


class DecideTest(unittest.TestCase):
    def test_approval_on_head_after_handoff_starts_the_next_phase(self):
        self.assertEqual(decide(state(), facts([approval()]), ME), StartPhase(3, "R1"))

    def test_approval_on_an_older_commit_does_nothing(self):
        self.assertIsNone(decide(state(), facts([approval(commit=OLD)]), ME))

    def test_approval_before_the_handoff_does_nothing(self):
        self.assertIsNone(decide(state(), facts([approval(at="2026-09-07T09:30:00Z")]), ME))

    def test_consumed_approval_does_nothing(self):
        self.assertIsNone(decide(state(consumed=["R1"]), facts([approval()]), ME))

    def test_teammate_approval_does_nothing(self):
        self.assertIsNone(decide(state(), facts([approval(author="tushar")]), ME))

    def test_changes_requested_does_nothing(self):
        self.assertIsNone(decide(state(), facts([approval(review_state="CHANGES_REQUESTED")]), ME))

    def test_new_comment_on_a_blocking_thread_wins_over_approval(self):
        self.assertEqual(decide(state(), facts([approval()], [thread()]), ME), AddressComments(("T1",), "2026-09-07T10:30:00Z"))

    def test_blocking_thread_without_new_comment_holds(self):
        self.assertIsNone(decide(state(), facts([approval()], [thread(at="2026-09-07T09:00:00Z")]), ME))

    def test_outdated_or_resolved_threads_do_not_block(self):
        threads = [thread(outdated=True), thread(resolved=True, thread_id="T2")]
        self.assertEqual(decide(state(), facts([approval()], threads), ME), StartPhase(3, "R1"))

    def test_phase_seven_approval_finishes(self):
        self.assertEqual(decide(state(phase=7), facts([approval()]), ME), MarkDone("phase 7 approved by aman-kumar"))

    def test_working_and_addressing_wait_for_the_handoff(self):
        self.assertIsNone(decide(state(status="working"), facts([approval()]), ME))
        self.assertIsNone(decide(state(status="addressing_comments"), facts([approval()]), ME))

    def test_done_stays_done(self):
        self.assertIsNone(decide(state(status="done"), facts([approval()]), ME))

    def test_merged_or_closed_finishes(self):
        self.assertEqual(decide(state(), facts(merged=True), ME), MarkDone("pull request merged"))
        self.assertEqual(decide(state(), facts(closed=True), ME), MarkDone("pull request closed"))


if __name__ == "__main__":
    unittest.main()
