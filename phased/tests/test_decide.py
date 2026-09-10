import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone  # noqa: E402
from decide import (RESUME_COOLDOWN_SECONDS, RESUME_GRACE_SECONDS, RESUME_MAX_ATTEMPTS, SESSION_MARKER,  # noqa: E402
                    AddressComments, Comment, DeliverFeedback, Facts, MarkDone, Review, StartPhase,
                    Thread, decide, decide_resume, is_approval_text)

ME = "aman-kumar"
HEAD = "a" * 40
OLD = "b" * 40


def state(phase=2, status="waiting_approval", handoff_at="2026-09-07T10:00:00Z", consumed=()):
    return {"phase": phase, "status": status, "handoff_at": handoff_at, "consumed_approval_ids": list(consumed)}


def approval(commit=HEAD, at="2026-09-07T11:00:00Z", author=ME, review_id="R1", review_state="APPROVED", body=""):
    return Review(review_id, author, review_state, at, commit, body)


def facts(reviews=(), threads=(), merged=False, closed=False, comments=(), head_at="2026-09-07T09:00:00Z"):
    return Facts(HEAD, head_at, True, closed, merged, tuple(reviews), tuple(threads), tuple(comments))


def comment(body="Approved.", at="2026-09-07T11:00:00Z", author=ME, comment_id="C1"):
    return Comment(comment_id, author, body, at)


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
        self.assertEqual(decide(state(), facts([approval()], [thread()]), ME), AddressComments(("T1",), (), "2026-09-07T10:30:00Z"))

    def test_a_conversation_comment_that_is_not_an_approval_is_feedback(self):
        feedback = comment(body="RunbookPresenter should take a param and only then process last_job.", comment_id="C9")
        self.assertEqual(decide(state(), facts(comments=[feedback]), ME), AddressComments((), ("C9",), "2026-09-07T11:00:00Z"))

    def test_feedback_and_a_thread_come_together(self):
        feedback = comment(body="also rename the prop", comment_id="C9")
        action = decide(state(), facts(comments=[feedback], threads=[thread()]), ME)
        self.assertEqual(action, AddressComments(("T1",), ("C9",), "2026-09-07T11:00:00Z"))

    def test_a_seen_feedback_comment_does_not_fire_again(self):
        feedback = comment(body="rename the prop", comment_id="C9")
        current = {**state(), "seen_comment_ids": ["C9"]}
        self.assertIsNone(decide(current, facts(comments=[feedback]), ME))

    def test_a_ghmention_comment_is_not_feedback(self):
        mention = comment(body="@claude please explain this", comment_id="C9")
        self.assertIsNone(decide(state(), facts(comments=[mention]), ME))

    def test_a_teammate_comment_is_not_feedback(self):
        self.assertIsNone(decide(state(), facts(comments=[comment(body="looks odd", author="tushar")]), ME))

    def test_feedback_older_than_the_handoff_does_nothing(self):
        self.assertIsNone(decide(state(), facts(comments=[comment(body="old note", at="2026-09-07T09:30:00Z")]), ME))

    def test_feedback_wins_over_an_approval_in_the_same_batch(self):
        action = decide(state(), facts([approval()], comments=[comment(), comment(body="one more thing", comment_id="C9")]), ME)
        self.assertEqual(action, AddressComments((), ("C9",), "2026-09-07T11:00:00Z"))

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

    def test_approved_comment_by_me_after_handoff_and_head_starts_the_next_phase(self):
        self.assertEqual(decide(state(), facts(comments=[comment()]), ME), StartPhase(3, "C1"))

    def test_approved_comment_older_than_the_last_commit_does_nothing(self):
        self.assertIsNone(decide(state(), facts(comments=[comment(at="2026-09-07T10:30:00Z")], head_at="2026-09-07T10:45:00Z"), ME))

    def test_approved_comment_before_the_handoff_does_nothing(self):
        self.assertIsNone(decide(state(), facts(comments=[comment(at="2026-09-07T09:30:00Z")]), ME))

    def test_consumed_approved_comment_does_nothing(self):
        self.assertIsNone(decide(state(consumed=["C1"]), facts(comments=[comment()]), ME))

    def test_teammate_approved_comment_does_nothing(self):
        self.assertIsNone(decide(state(), facts(comments=[comment(author="tushar")]), ME))

    def test_only_the_word_approved_counts(self):
        self.assertTrue(is_approval_text("Approved."))
        self.assertTrue(is_approval_text("  approved!\nsecond line with a note"))
        self.assertTrue(is_approval_text("APPROVED"))
        self.assertFalse(is_approval_text("Approved, but rename the field first"))
        self.assertFalse(is_approval_text("LGTM"))
        self.assertFalse(is_approval_text("not approved"))
        self.assertFalse(is_approval_text(""))

    def test_approved_comment_on_phase_seven_finishes(self):
        self.assertEqual(decide(state(phase=7), facts(comments=[comment()]), ME), MarkDone("phase 7 approved by aman-kumar"))

    def test_merged_or_closed_finishes(self):
        self.assertEqual(decide(state(), facts(merged=True), ME), MarkDone("pull request merged"))
        self.assertEqual(decide(state(), facts(closed=True), ME), MarkDone("pull request closed"))


NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def stranded(status="working", attempts=0, last_resume_at=None):
    return {"phase": 2, "status": status, "handoff_at": "2026-09-07T10:00:00Z",
            "resume_attempts": attempts, "last_resume_at": last_resume_at}


def resume_for(current, window_alive=False, worktree_exists=True, other_claude_running=False,
               seconds_since_start=RESUME_GRACE_SECONDS + 1):
    return decide_resume(current, window_alive=window_alive, worktree_exists=worktree_exists,
                         other_claude_running=other_claude_running, now=NOW,
                         seconds_since_start=seconds_since_start)


class MidPhaseFeedbackTest(unittest.TestCase):
    """Feedback posted while a phase is still open. It used to be dropped, and dropped for good, because
    the next handoff moved the mark past it."""

    def working(self, status="working", seen_at=None, seen_ids=()):
        current = state(status=status)
        if seen_at:
            current["comments_seen_at"] = seen_at
        current["seen_comment_ids"] = list(seen_ids)
        return current

    def test_a_comment_while_working_reaches_the_session(self):
        action = decide(self.working(), facts(comments=[comment(body="stop, the icons are wrong")]), ME)
        self.assertEqual(action, DeliverFeedback((), ("C1",), "2026-09-07T11:00:00Z", ()))

    def test_a_comment_while_addressing_comments_reaches_the_session(self):
        action = decide(self.working(status="addressing_comments"),
                        facts(comments=[comment(body="and this one too")]), ME)
        self.assertIsInstance(action, DeliverFeedback)

    def test_the_same_comment_does_not_reach_it_twice(self):
        current = self.working(seen_ids=["C1"])
        self.assertIsNone(decide(current, facts(comments=[comment(body="stop")]), ME))

    def test_a_reposted_comment_is_new_because_its_id_is_new(self):
        current = self.working(seen_ids=["C1"], seen_at="2026-09-07T11:00:00Z")
        repost = comment(body="stop", comment_id="C2", at="2026-09-07T11:02:00Z")
        self.assertEqual(decide(current, facts(comments=[repost]), ME).comment_ids, ("C2",))

    def test_delivery_never_carries_a_thread(self):
        action = decide(self.working(), facts(comments=[comment(body="x")], threads=[thread()]), ME)
        self.assertEqual(action.thread_ids, ())

    def test_an_approval_while_working_is_not_feedback(self):
        self.assertIsNone(decide(self.working(), facts(comments=[comment()]), ME))

    def test_a_session_comment_is_never_read_as_feedback(self):
        body = f"\U0001f916 **{SESSION_MARKER}** \u00b7 posted with @aman-kumar's token\n\nphase 7 done"
        self.assertIsNone(decide(self.working(), facts(comments=[comment(body=body)]), ME))
        self.assertIsNone(decide(state(), facts(comments=[comment(body=body)]), ME))

    def test_the_floor_follows_what_was_delivered_not_the_handoff(self):
        # delivered at 11:00, then the phase handed off at 12:00: a comment from 11:30 must still fire
        current = state(handoff_at="2026-09-07T12:00:00Z")
        current["comments_seen_at"] = "2026-09-07T11:00:00Z"
        late = comment(body="you missed this", comment_id="C9", at="2026-09-07T11:30:00Z")
        self.assertEqual(decide(current, facts(comments=[late]), ME).comment_ids, ("C9",))

    def test_a_comment_below_the_floor_stays_quiet(self):
        current = state()
        current["comments_seen_at"] = "2026-09-07T11:00:00Z"
        old = comment(body="already handled", comment_id="C8", at="2026-09-07T10:30:00Z")
        self.assertIsNone(decide(current, facts(comments=[old]), ME))


class ReviewBodyTest(unittest.TestCase):
    """GitHub files a review by the author of the pull request as COMMENTED, so the approval is the word
    in the body. Those bodies are also where an objection that belongs to no line lives."""

    def test_commented_review_saying_approved_starts_the_next_phase(self):
        review = approval(review_state="COMMENTED", body="Approved")
        self.assertEqual(decide(state(), facts(reviews=[review]), ME), StartPhase(3, "R1"))

    def test_commented_review_with_a_note_is_feedback_not_an_approval(self):
        review = approval(review_state="COMMENTED", body="Approved, but rename the field first")
        action = decide(state(), facts(reviews=[review]), ME)
        self.assertEqual(action, AddressComments((), (), "2026-09-07T11:00:00Z", ("R1",)))

    def test_a_plain_review_body_is_feedback(self):
        review = approval(review_state="COMMENTED", body="the presenter should take a keyword")
        self.assertEqual(decide(state(), facts(reviews=[review]), ME).review_ids, ("R1",))

    def test_changes_requested_body_is_feedback(self):
        review = approval(review_state="CHANGES_REQUESTED", body="split this in two")
        self.assertEqual(decide(state(), facts(reviews=[review]), ME).review_ids, ("R1",))

    def test_changes_requested_never_approves_whatever_the_body_says(self):
        review = approval(review_state="CHANGES_REQUESTED", body="Approved")
        self.assertIsNone(decide(state(), facts(reviews=[review]), ME))

    def test_a_review_on_an_older_commit_does_not_approve(self):
        review = approval(commit=OLD, review_state="COMMENTED", body="Approved")
        self.assertIsNone(decide(state(), facts(reviews=[review]), ME))

    def test_a_review_before_the_handoff_is_ignored(self):
        review = approval(at="2026-09-07T09:00:00Z", review_state="COMMENTED", body="Approved")
        self.assertIsNone(decide(state(), facts(reviews=[review]), ME))

    def test_a_seen_review_body_does_not_fire_twice(self):
        review = approval(review_state="COMMENTED", body="the presenter should take a keyword")
        current = state()
        current["seen_comment_ids"] = ["R1"]
        self.assertIsNone(decide(current, facts(reviews=[review]), ME))

    def test_a_teammate_review_body_is_not_read_as_feedback_for_the_author(self):
        review = approval(author="tushar", review_state="COMMENTED", body="please rename this")
        self.assertIsNone(decide(state(), facts(reviews=[review]), ME))

    def test_an_empty_review_body_is_nothing(self):
        review = approval(review_state="COMMENTED", body="   ")
        self.assertIsNone(decide(state(), facts(reviews=[review]), ME))

    def test_a_ghmention_review_body_is_skipped(self):
        review = approval(review_state="COMMENTED", body="@claude have a look at this")
        self.assertIsNone(decide(state(), facts(reviews=[review]), ME))


class DecideResumeTest(unittest.TestCase):
    def test_dead_window_while_working_resumes(self):
        action = resume_for(stranded())
        self.assertIsNotNone(action)
        self.assertEqual(action.attempt, 1)

    def test_dead_window_while_addressing_comments_resumes(self):
        self.assertIsNotNone(resume_for(stranded(status="addressing_comments")))

    def test_live_window_is_left_alone(self):
        self.assertIsNone(resume_for(stranded(), window_alive=True))

    def test_waiting_for_approval_is_left_alone(self):
        self.assertIsNone(resume_for(stranded(status="waiting_approval")))

    def test_done_is_left_alone(self):
        self.assertIsNone(resume_for(stranded(status="done")))

    def test_missing_worktree_is_left_alone(self):
        self.assertIsNone(resume_for(stranded(), worktree_exists=False))

    def test_another_claude_in_the_worktree_blocks_the_resume(self):
        self.assertIsNone(resume_for(stranded(), other_claude_running=True))

    def test_nothing_resumes_inside_the_grace_period(self):
        self.assertIsNone(resume_for(stranded(), seconds_since_start=RESUME_GRACE_SECONDS - 1))

    def test_attempts_stop_at_the_cap(self):
        self.assertIsNone(resume_for(stranded(attempts=RESUME_MAX_ATTEMPTS)))
        self.assertEqual(resume_for(stranded(attempts=RESUME_MAX_ATTEMPTS - 1)).attempt, RESUME_MAX_ATTEMPTS)

    def test_cooldown_holds_a_second_attempt_back(self):
        just_now = "2026-09-09T11:59:00Z"
        self.assertIsNone(resume_for(stranded(attempts=1, last_resume_at=just_now)))

    def test_after_the_cooldown_the_next_attempt_runs(self):
        long_ago = "2026-09-09T11:00:00Z"
        self.assertGreater((NOW - datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc)).total_seconds(), RESUME_COOLDOWN_SECONDS)
        self.assertEqual(resume_for(stranded(attempts=1, last_resume_at=long_ago)).attempt, 2)


if __name__ == "__main__":
    unittest.main()
