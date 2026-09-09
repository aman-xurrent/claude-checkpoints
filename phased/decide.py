"""The one decision of the phase loop, kept pure so it can be tested without GitHub or tmux.

State is the local truth for interpretation (which phase, whether waiting, what was consumed).
Facts are what GitHub reports this tick. The result is one Action or None."""
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

LAST_PHASE = 7
STATUS_WORKING = "working"
STATUS_WAITING = "waiting_approval"
STATUS_ADDRESSING = "addressing_comments"
STATUS_DONE = "done"

# A pull request in one of these statuses has a session doing the work. When that session is gone (the
# machine restarted, tmux was killed, Claude exited), nothing on GitHub can move the loop, because the
# loop only reads GitHub for a pull request that waits for approval. The daemon restarts the session.
RESUME_STATUSES = (STATUS_WORKING, STATUS_ADDRESSING)
RESUME_MAX_ATTEMPTS = 3
RESUME_COOLDOWN_SECONDS = int(os.environ.get("PHASED_RESUME_COOLDOWN_SECONDS", 600))
# Nothing is resumed in the first minutes after the daemon starts: at boot the network is not up yet and
# the user is not at the machine.
RESUME_GRACE_SECONDS = int(os.environ.get("PHASED_RESUME_GRACE_SECONDS", 180))


@dataclass(frozen=True)
class Review:
    id: str
    author: str
    state: str
    submitted_at: str
    commit_sha: str


@dataclass(frozen=True)
class Thread:
    id: str
    is_resolved: bool
    is_outdated: bool
    last_comment_at: str
    last_comment_author: str


@dataclass(frozen=True)
class Comment:
    id: str
    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class Facts:
    head_sha: str
    head_committed_at: str
    is_draft: bool
    closed: bool
    merged: bool
    reviews: tuple = field(default_factory=tuple)
    threads: tuple = field(default_factory=tuple)
    comments: tuple = field(default_factory=tuple)


APPROVAL_WORD = "approved"
# A comment carrying this marker belongs to the other daemon (ghmention). Reading it as feedback would
# make the two answer each other.
MENTION_MARKER = "@claude"


@dataclass(frozen=True)
class StartPhase:
    phase: int
    approval_id: str


@dataclass(frozen=True)
class AddressComments:
    thread_ids: tuple
    comment_ids: tuple
    newest_comment_at: str


@dataclass(frozen=True)
class MarkDone:
    reason: str


@dataclass(frozen=True)
class ResumeSession:
    reason: str
    attempt: int


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def blocking_threads(facts):
    """Unresolved and current. An outdated thread hangs on a line the branch already changed."""
    return [thread for thread in facts.threads if not thread.is_resolved and not thread.is_outdated]


def approving_review(state, facts, me):
    """The user's APPROVED review given on the current head, after the last handoff, not consumed yet."""
    handoff_at = parse_time(state["handoff_at"])
    consumed = set(state.get("consumed_approval_ids", []))
    for review in facts.reviews:
        if review.author != me or review.state != "APPROVED" or review.id in consumed:
            continue
        if review.commit_sha != facts.head_sha:
            continue
        if parse_time(review.submitted_at) <= handoff_at:
            continue
        return review
    return None


def is_approval_text(body):
    """The first line of the comment, letters only, is the word approved. `Approved.` counts;
    `Approved, but rename X` does not, because that is feedback, not a release."""
    first_line = body.strip().splitlines()[0] if body.strip() else ""
    return re.sub(r"[^a-z]", "", first_line.lower()) == APPROVAL_WORD


def approving_comment(state, facts, me):
    """GitHub refuses a review approval from the author of the pull request, so the author approves with a
    conversation comment. It must be newer than the last handoff and than the last commit, and unconsumed."""
    handoff_at = parse_time(state["handoff_at"])
    head_at = parse_time(facts.head_committed_at) if facts.head_committed_at else handoff_at
    consumed = set(state.get("consumed_approval_ids", []))
    for comment in facts.comments:
        if comment.author != me or comment.id in consumed or not is_approval_text(comment.body):
            continue
        created = parse_time(comment.created_at)
        if created <= handoff_at or created <= head_at:
            continue
        return comment
    return None


def feedback_comments(state, facts, me):
    """The approver's conversation comments are feedback unless they are the approval itself. A comment on
    the conversation tab is how a reviewer asks for a change that belongs to no single line, and dropping
    those left the request waiting on an approval the approver never meant to give."""
    handoff_at = parse_time(state["handoff_at"])
    spent = set(state.get("consumed_approval_ids", [])) | set(state.get("seen_comment_ids", []))
    return [comment for comment in facts.comments
            if comment.author == me and comment.id not in spent
            and not is_approval_text(comment.body) and MENTION_MARKER not in comment.body
            and parse_time(comment.created_at) > handoff_at]


def decide(state, facts, me) -> Optional[object]:
    if state["status"] == STATUS_DONE:
        return None
    if facts.merged or facts.closed:
        return MarkDone("pull request merged" if facts.merged else "pull request closed")
    if state["status"] != STATUS_WAITING:
        return None
    handoff_at = parse_time(state["handoff_at"])
    new_threads = [thread for thread in blocking_threads(facts)
                   if parse_time(thread.last_comment_at) > handoff_at and thread.last_comment_author != "phased"]
    new_comments = feedback_comments(state, facts, me)
    if new_threads or new_comments:
        newest = max([thread.last_comment_at for thread in new_threads] + [comment.created_at for comment in new_comments])
        return AddressComments(tuple(thread.id for thread in new_threads), tuple(comment.id for comment in new_comments), newest)
    if blocking_threads(facts):
        return None
    approval = approving_review(state, facts, me) or approving_comment(state, facts, me)
    if approval is None:
        return None
    if state["phase"] >= LAST_PHASE:
        return MarkDone(f"phase {LAST_PHASE} approved by {me}")
    return StartPhase(state["phase"] + 1, approval.id)


def decide_resume(state, *, window_alive, worktree_exists, other_claude_running, now, seconds_since_start):
    """Whether to restart the session of a pull request whose window is gone.

    Every input the caller must measure (tmux, the filesystem, the clock) arrives as an argument, so this
    stays testable. None means leave it alone."""
    if state.get("status") not in RESUME_STATUSES:
        return None
    if window_alive or not worktree_exists or other_claude_running:
        return None
    if seconds_since_start < RESUME_GRACE_SECONDS:
        return None
    attempts = state.get("resume_attempts", 0)
    if attempts >= RESUME_MAX_ATTEMPTS:
        return None
    last_resume = state.get("last_resume_at")
    if last_resume and (now - parse_time(last_resume)).total_seconds() < RESUME_COOLDOWN_SECONDS:
        return None
    return ResumeSession(f"no live Claude window for a pull request in status {state['status']}", attempts + 1)
