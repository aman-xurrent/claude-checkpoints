#!/usr/bin/env python3
"""phased: the phase loop daemon for phased development.

A pull request under the loop is registered by the phase 1 handoff. Every poll, one GraphQL call per
repository reads the registered pull requests. The user's comment `Approved` (or an APPROVED review) newer
than the last handoff and than the last commit, with no unresolved current review thread, starts the next
phase in the Claude tmux
window of that pull request (reused when alive, else a new window). New review comments send the session
back to address them first. The local state file is the truth; GitHub is the event feed.

Subcommands
  run [--once] [--dry-run]   The loop (the LaunchAgent runs this).
  handoff --pr N --phase P   Called by the session at the end of a phase, from the PR worktree.
                             Registers the pull request on first use.
  status                     Every registered pull request and its phase.
  adopt --pr N --phase P [--worktree PATH] [--repo OWNER/NAME]   Rebuild a lost state file.
  pause | resume             Stop or restart acting (polling continues).
  logs [-f]                  The log.
  init                       Write ~/.config/phased/config.json when missing.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github  # noqa: E402
import state as state_store  # noqa: E402
import tmux  # noqa: E402
from decide import (LAST_PHASE, STATUS_ADDRESSING, STATUS_DONE, STATUS_WAITING, STATUS_WORKING,  # noqa: E402
                    AddressComments, MarkDone, StartPhase, decide)

CONFIG_PATH = Path(os.environ.get("PHASED_CONFIG", Path.home() / ".config/phased/config.json")).expanduser()
LOG_PATH = state_store.STATE_ROOT / "phased.log"
GHMENTION_TRIGGERS = Path.home() / ".cache/ghmention/triggers"
GHMENTION_TERMINAL_STAGES = {"pushed", "withheld", "blocked", "aborted", "spawn-failed"}
GHMENTION_STALE_AFTER = timedelta(hours=3)
DEFAULT_CONFIG = {
    "me": "",
    "gh_host": "git.4me.com",
    "poll_seconds": 60,
    "labels": True,
    "notify": True,
    "repos": {"4me/ipaas": {"tmux": "ipaas", "path": str(Path.home() / "work/ipaas")}},
}
SKILLS_DIRECTORY = ".claude/skills"


# ---------------------------------------------------------------- helpers

def now_iso():
    return state_store.now_iso()


def log(text):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as handle:
        handle.write(f"{now_iso()} {text}\n")


def say(text):
    print(text)
    log(text)


def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit(f"phased: no config at {CONFIG_PATH}; run phased init")
    config = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
    if not config.get("me"):
        raise SystemExit(f"phased: config.me (your GitHub login) is empty in {CONFIG_PATH}")
    return config


def run(command, cwd=None, allow_failure=False):
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if completed.returncode != 0 and not allow_failure:
        raise RuntimeError(f"{' '.join(command)} failed: {completed.stderr.strip()[:300]}")
    return completed.stdout.strip()


def notify(config, title, message):
    if not config.get("notify"):
        return
    subprocess.run(["terminal-notifier", "-title", title, "-message", message], capture_output=True)


def repo_of(root):
    remote = run(["git", "-C", str(root), "remote", "get-url", "origin"])
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise RuntimeError(f"cannot read owner/name from remote {remote}")
    return match.group(1)


def request_number(branch):
    match = re.match(r"(?:requests|problems)/(\d+)", branch)
    return match.group(1) if match else None


def ghmention_busy(repo, pr):
    """A live @claude run on the same pull request owns its window; wait for it."""
    for trigger in GHMENTION_TRIGGERS.glob("*/trigger.json"):
        try:
            data = json.loads(trigger.read_text())
        except (OSError, ValueError):
            continue
        if data.get("repo") != repo or str(data.get("pr")) != str(pr):
            continue
        if data.get("stage") in GHMENTION_TERMINAL_STAGES:
            continue
        started = data.get("started_at")
        if started and datetime.fromisoformat(started.replace("Z", "+00:00")) < datetime.now(timezone.utc) - GHMENTION_STALE_AFTER:
            continue
        return trigger.parent.name
    return None


# ------------------------------------------------------------------ briefs

def write_brief(repo, pr, name, text):
    directory = state_store.pr_directory(repo, pr) / f"phase-{name}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "brief.md"
    path.write_text(text)
    return path


def start_phase_brief(config, current, raw, action):
    phase = action.phase
    return (
        f"# Phase {phase} of {LAST_PHASE} for {current['repo']} PR #{current['pr']}\n\n"
        f"{raw['title']}\n{raw['url']}\n\n"
        f"Branch `{current['branch']}`, request {current.get('request') or 'unknown'}, worktree `{current['worktree']}`.\n"
        f"Phase {phase - 1} was approved by {config['me']} on `{raw['commits']['nodes'][0]['commit']['oid'][:10]}`.\n\n"
        f"Start phase {phase}: read `{SKILLS_DIRECTORY}/phase-{phase}/SKILL.md` in the worktree and carry it out to its\n"
        f"handoff (`{SKILLS_DIRECTORY}/phase/ceremony.md`). Work in `{current['worktree']}` only. The handoff ends with\n"
        f"`phased handoff --pr {current['pr']} --phase {phase}`. Do not start phase {phase + 1}.\n"
    )


def comments_brief(current, raw, action):
    lines = [f"# New review feedback on {current['repo']} PR #{current['pr']} (phase {current['phase']})\n", raw["url"], ""]
    wanted_comments = set(action.comment_ids)
    for node in raw["comments"]["nodes"]:
        if node["id"] not in wanted_comments:
            continue
        lines.append(f"## Conversation comment by {(node.get('author') or {}).get('login', '?')} at {node['createdAt']}")
        lines.append(node.get("body", "").strip())
        lines.append("")
    wanted = set(action.thread_ids)
    for node in raw["reviewThreads"]["nodes"]:
        if node["id"] not in wanted:
            continue
        comments = node["comments"]["nodes"]
        first = comments[0] if comments else {}
        lines.append(f"## Thread {node['id']}  {first.get('path', '?')}:{first.get('line', '?')}")
        for comment in comments:
            lines.append(f"- {(comment.get('author') or {}).get('login', '?')} at {comment.get('createdAt', '')}: {comment.get('body', '').strip()}")
            if comment.get("url"):
                lines.append(f"  {comment['url']}")
        lines.append("")
    lines.append(f"Address every item above in `{current['worktree']}`. Reply in each review thread and resolve it;\n"
                 f"answer a conversation comment with a conversation comment saying what you changed. Then run\n"
                 f"`.claude/bin/agent_task_finalize --phase {current['phase']}`, commit, push, then\n"
                 f"`phased handoff --pr {current['pr']} --phase {current['phase']}`. See `{SKILLS_DIRECTORY}/phase-comments/SKILL.md`.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ acting

def deliver(config, current, line, prompt_for_new_window, dry_run):
    """Type one line into the live Claude window of this pull request, or open a new window with claude."""
    repo_config = config["repos"].get(current["repo"], {})
    session = repo_config.get("tmux") or current["repo"].split("/")[-1]
    name = f"pr{current['pr']}"
    window = tmux.find_live_window(session, name, current.get("window_id"))
    if dry_run:
        say(f"dry-run: would {'type into ' + window if window else 'open a new window in ' + session} for PR #{current['pr']}: {line[:120]}")
        return window or "dry-run"
    if window:
        tmux.send_line(window, line)
        log(f"typed into {window} ({session}:{name}) for PR #{current['pr']}")
        return window
    worktree = Path(current["worktree"])
    if not worktree.is_dir():
        raise RuntimeError(f"worktree {worktree} is gone; run phased adopt --pr {current['pr']} --phase {current['phase']} --worktree <path>")
    tmux.ensure_session(session, str(worktree))
    launcher = state_store.pr_directory(current["repo"], current["pr"]) / "launch.sh"
    delimiter = "PHASED_PROMPT_EOF"
    if delimiter in prompt_for_new_window:
        raise RuntimeError("the prompt contains the heredoc delimiter")
    launcher.write_text("#!/usr/bin/env bash\n# generated by phased\n"
                        f"cd {tmux.shell_quote(str(worktree))}\n"
                        f"exec claude -- \"$(cat <<'{delimiter}'\n{prompt_for_new_window}\n{delimiter}\n)\"\n")
    launcher.chmod(0o755)
    window = tmux.new_window(session, name, str(worktree), str(launcher))
    log(f"opened window {window} ({session}:{name}) for PR #{current['pr']}")
    return window


def apply_action(config, current, raw, action, dry_run):
    repo, pr = current["repo"], current["pr"]
    busy = ghmention_busy(repo, pr)
    if busy and not isinstance(action, MarkDone):
        log(f"PR #{pr}: ghmention trigger {busy} is live on this pull request; waiting")
        return
    if isinstance(action, StartPhase):
        brief = write_brief(repo, pr, str(action.phase), start_phase_brief(config, current, raw, action))
        line = (f"Phase {action.phase - 1} of PR #{pr} is approved. Start phase {action.phase}: read @{brief} and carry out "
                f"{SKILLS_DIRECTORY}/phase-{action.phase}/SKILL.md to its handoff.")
        window = deliver(config, current, line, line, dry_run)
        if dry_run:
            return
        current.update({"phase": action.phase, "status": STATUS_WORKING, "window_id": window,
                        "consumed_approval_ids": current.get("consumed_approval_ids", []) + [action.approval_id],
                        "phase_started_at": now_iso()})
        state_store.save(repo, pr, current, f"start phase {action.phase}")
        if config.get("labels"):
            github.remove_label(config["gh_host"], repo, pr, f"phase:{action.phase - 1}")
            github.set_label(config["gh_host"], repo, pr, f"phase:{action.phase}")
        notify(config, f"{repo} #{pr}", f"phase {action.phase} started")
        say(f"PR #{pr}: phase {action.phase} started in window {window}")
    elif isinstance(action, AddressComments):
        brief = write_brief(repo, pr, f"{current['phase']}-comments-{action.newest_comment_at.replace(':', '')}", comments_brief(current, raw, action))
        count = len(action.thread_ids) + len(action.comment_ids)
        line = (f"New review feedback on PR #{pr} (phase {current['phase']}): read @{brief}, carry out "
                f"{SKILLS_DIRECTORY}/phase-comments/SKILL.md, then phased handoff --pr {pr} --phase {current['phase']}.")
        window = deliver(config, current, line, line, dry_run)
        if dry_run:
            return
        current.update({"status": STATUS_ADDRESSING, "window_id": window, "comments_seen_at": action.newest_comment_at,
                        "seen_comment_ids": current.get("seen_comment_ids", []) + list(action.comment_ids)})
        state_store.save(repo, pr, current, f"address {len(action.thread_ids)} thread(s) and {len(action.comment_ids)} comment(s)")
        notify(config, f"{repo} #{pr}", f"{count} piece(s) of feedback sent to the session")
        say(f"PR #{pr}: {len(action.thread_ids)} thread(s) and {len(action.comment_ids)} comment(s) sent to window {window}")
    elif isinstance(action, MarkDone):
        if dry_run:
            say(f"dry-run: would mark PR #{pr} done ({action.reason})")
            return
        current.update({"status": STATUS_DONE, "done_reason": action.reason})
        state_store.save(repo, pr, current, f"done: {action.reason}")
        if config.get("labels"):
            github.remove_label(config["gh_host"], repo, pr, f"phase:{current['phase']}")
            github.set_label(config["gh_host"], repo, pr, "phase:done")
        notify(config, f"{repo} #{pr}", f"phase loop done: {action.reason}")
        say(f"PR #{pr}: done ({action.reason})")


# -------------------------------------------------------------------- loop

def tick(config, dry_run=False):
    states = [each for each in state_store.all_states() if each.get("status") != STATUS_DONE]
    if not states:
        return
    if state_store.paused():
        log("paused; not acting")
        return
    by_repo = {}
    for each in states:
        by_repo.setdefault(each["repo"], []).append(each)
    for repo, entries in by_repo.items():
        try:
            fetched, rate = github.fetch(config["gh_host"], repo, [each["pr"] for each in entries])
        except RuntimeError as error:
            log(f"{repo}: fetch failed: {error}")
            continue
        log(f"{repo}: polled {len(entries)} pull request(s), rate limit cost {rate.get('cost')} remaining {rate.get('remaining')}")
        for current in entries:
            if current["pr"] not in fetched:
                log(f"{repo} #{current['pr']}: not returned by GitHub")
                continue
            facts, raw = fetched[current["pr"]]
            action = decide(current, facts, config["me"])
            if action is None:
                continue
            try:
                apply_action(config, current, raw, action, dry_run)
            except (RuntimeError, state_store.StaleWrite) as error:
                log(f"{repo} #{current['pr']}: {type(action).__name__} failed: {error}")


def run_loop(arguments):
    config = load_config()
    once = "--once" in arguments
    dry_run = "--dry-run" in arguments
    log(f"phased start (once={once} dry_run={dry_run} pid={os.getpid()})")
    while True:
        try:
            tick(config, dry_run)
        except Exception as error:  # the loop must survive anything a tick raises
            log(f"tick failed: {type(error).__name__}: {error}")
        if once:
            return
        time.sleep(int(config.get("poll_seconds", 60)))


# ---------------------------------------------------------------- commands

def argument_value(arguments, flag, default=None):
    if flag in arguments:
        return arguments[arguments.index(flag) + 1]
    return default


def handoff(arguments):
    config = load_config()
    pr = int(argument_value(arguments, "--pr") or 0)
    phase = int(argument_value(arguments, "--phase") or 0)
    if not pr or phase not in range(1, LAST_PHASE + 1):
        raise SystemExit("usage: phased handoff --pr N --phase P (run from the PR worktree)")
    root = Path(run(["git", "rev-parse", "--show-toplevel"]))
    repo = argument_value(arguments, "--repo") or repo_of(root)
    branch = run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
    head = run(["git", "-C", str(root), "rev-parse", "HEAD"])
    current = state_store.load(repo, pr) or {"repo": repo, "pr": pr, "consumed_approval_ids": [], "registered_at": now_iso()}
    window = tmux.current_window_id() or current.get("window_id")
    if window:
        tmux.rename_window(window, f"pr{pr}")
    current.update({"branch": branch, "request": request_number(branch), "worktree": str(root), "phase": phase,
                    "status": STATUS_WAITING, "phase_head": head, "handoff_at": now_iso(), "window_id": window})
    state_store.save(repo, pr, current, f"handoff phase {phase}")
    if config.get("labels"):
        github.set_label(config["gh_host"], repo, pr, f"phase:{phase}")
    say(f"{repo} #{pr}: phase {phase} handed off at {head[:10]}; waiting for {config['me']}'s comment `Approved` newer than that head"
        + (f" (window {window})" if window else " (not inside tmux: a new window will open for the next phase)"))


def adopt(arguments):
    config = load_config()
    pr = int(argument_value(arguments, "--pr") or 0)
    phase = int(argument_value(arguments, "--phase") or 0)
    worktree = Path(argument_value(arguments, "--worktree") or os.getcwd()).resolve()
    if not pr or phase not in range(1, LAST_PHASE + 1):
        raise SystemExit("usage: phased adopt --pr N --phase P [--worktree PATH] [--repo OWNER/NAME]")
    repo = argument_value(arguments, "--repo") or repo_of(worktree)
    branch = run(["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"])
    head = run(["git", "-C", str(worktree), "rev-parse", "HEAD"])
    current = state_store.load(repo, pr) or {"repo": repo, "pr": pr, "consumed_approval_ids": [], "registered_at": now_iso()}
    current.update({"branch": branch, "request": request_number(branch), "worktree": str(worktree), "phase": phase,
                    "status": STATUS_WAITING, "phase_head": head, "handoff_at": now_iso(), "adopted": True})
    state_store.save(repo, pr, current, f"adopt at phase {phase}")
    say(f"{repo} #{pr}: adopted at phase {phase}, waiting for approval on {head[:10]}")


def status():
    states = state_store.all_states()
    if not states:
        print(f"no registered pull requests under {state_store.STATE_ROOT}")
        return
    print(f"{'repo':12} {'pr':>6} {'phase':>5} {'status':20} {'handoff':20} window   branch")
    for each in states:
        print(f"{each['repo']:12} {each['pr']:>6} {each.get('phase', '?'):>5} {each.get('status', '?'):20} "
              f"{(each.get('handoff_at') or '')[:19]:20} {each.get('window_id') or '-':8} {each.get('branch', '')}")
    if state_store.paused():
        print("PAUSED")


def logs(arguments):
    if not LOG_PATH.exists():
        print("no log yet")
        return
    if "-f" in arguments:
        os.execvp("tail", ["tail", "-f", str(LOG_PATH)])
    print("\n".join(LOG_PATH.read_text().splitlines()[-40:]))


def init():
    if CONFIG_PATH.exists():
        print(f"config exists: {CONFIG_PATH}")
        return
    config = dict(DEFAULT_CONFIG)
    login = run(["gh", "api", "user", "--jq", ".login"], allow_failure=True) if os.environ.get("GH_HOST") else ""
    config["me"] = login or run(["env", f"GH_HOST={config['gh_host']}", "gh", "api", "user", "--jq", ".login"], allow_failure=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    print(f"wrote {CONFIG_PATH} (me={config['me'] or 'FILL IN'})")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(2)
    command, arguments = argv[1], argv[2:]
    dispatch = {
        "run": lambda: run_loop(arguments),
        "handoff": lambda: handoff(arguments),
        "adopt": lambda: adopt(arguments),
        "status": lambda: status(),
        "pause": lambda: (state_store.set_paused(True), say("paused")),
        "resume": lambda: (state_store.set_paused(False), say("resumed")),
        "logs": lambda: logs(arguments),
        "init": lambda: init(),
    }
    if command not in dispatch:
        print(f"unknown subcommand {command}\n{__doc__}")
        sys.exit(2)
    dispatch[command]()


if __name__ == "__main__":
    main(sys.argv)
