"""One JSON file per pull request is the loop's truth. Writes go through a temporary file and a rename,
and a stale writer (older revision) is refused, so a reused window can never rewrite fresh state."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_ROOT = Path(os.environ.get("PHASED_STATE_DIR", Path.home() / ".local/state/phased")).expanduser()
STATE_FILE_NAME = "state.json"
HISTORY_FILE_NAME = "history.jsonl"
PAUSE_FILE_NAME = "paused"
SCHEMA = 1


class StaleWrite(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_directory(repo):
    return STATE_ROOT / repo.replace("/", "__")


def pr_directory(repo, pr):
    return repo_directory(repo) / f"pr-{pr}"


def load(repo, pr):
    path = pr_directory(repo, pr) / STATE_FILE_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save(repo, pr, state, event):
    directory = pr_directory(repo, pr)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STATE_FILE_NAME
    on_disk = json.loads(path.read_text()) if path.exists() else None
    if on_disk and on_disk.get("revision", 0) != state.get("revision", 0):
        raise StaleWrite(f"{path}: revision on disk is {on_disk.get('revision')}, ours is {state.get('revision')}")
    state["schema"] = SCHEMA
    state["revision"] = state.get("revision", 0) + 1
    state["updated_at"] = now_iso()
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
    with os.fdopen(handle, "w") as stream:
        json.dump(state, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)
    with open(directory / HISTORY_FILE_NAME, "a") as history:
        history.write(json.dumps({"at": state["updated_at"], "revision": state["revision"], "event": event,
                                  "phase": state.get("phase"), "status": state.get("status")}) + "\n")
    return state


def all_states():
    if not STATE_ROOT.exists():
        return []
    states = []
    for path in sorted(STATE_ROOT.glob(f"*/pr-*/{STATE_FILE_NAME}")):
        states.append(json.loads(path.read_text()))
    return states


def paused():
    return (STATE_ROOT / PAUSE_FILE_NAME).exists()


def set_paused(value):
    path = STATE_ROOT / PAUSE_FILE_NAME
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    if value:
        path.write_text(now_iso() + "\n")
    else:
        path.unlink(missing_ok=True)
