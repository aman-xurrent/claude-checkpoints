"""The one decision of the phase loop, kept pure so it can be tested without GitHub or tmux.

State is the local truth for interpretation (which phase, whether waiting, what was consumed).
Facts are what GitHub reports this tick. The result is one Action or None."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

LAST_PHASE = 7
STATUS_WORKING = "working"
STATUS_WAITING = "waiting_approval"
STATUS_ADDRESSING = "addressing_comments"
STATUS_DONE = "done"


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
class Facts:
    head_sha: str
    head_committed_at: str
    is_draft: bool
    closed: bool
    merged: bool
    reviews: tuple = field(default_factory=tuple)
    threads: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class StartPhase:
    phase: int
    approval_id: str


@dataclass(frozen=True)
class AddressComments:
    thread_ids: tuple
    newest_comment_at: str


@dataclass(frozen=True)
class MarkDone:
    reason: str


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


def decide(state, facts, me) -> Optional[object]:
    if state["status"] == STATUS_DONE:
        return None
    if facts.merged or facts.closed:
        return MarkDone("pull request merged" if facts.merged else "pull request closed")
    if state["status"] != STATUS_WAITING:
        return None
    handoff_at = parse_time(state["handoff_at"])
    new_feedback = [thread for thread in blocking_threads(facts)
                    if parse_time(thread.last_comment_at) > handoff_at and thread.last_comment_author != "phased"]
    if new_feedback:
        newest = max(thread.last_comment_at for thread in new_feedback)
        return AddressComments(tuple(thread.id for thread in new_feedback), newest)
    if blocking_threads(facts):
        return None
    review = approving_review(state, facts, me)
    if review is None:
        return None
    if state["phase"] >= LAST_PHASE:
        return MarkDone(f"phase {LAST_PHASE} approved by {me}")
    return StartPhase(state["phase"] + 1, review.id)
