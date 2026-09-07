"""The same window convention ghmention uses: the window for a pull request is named pr<number> in the
repository's tmux session; a live Claude in it is reused with one typed line, otherwise a new window
starts claude with the brief."""
import os
import re
import subprocess

CLAUDE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
CLAUDE_COMMANDS = {"claude", "node"}


def tmux(*arguments, check=True):
    completed = subprocess.run(["tmux", *arguments], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and completed.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def has_session(session):
    return subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode == 0


def ensure_session(session, cwd):
    if not has_session(session):
        tmux("new-session", "-d", "-s", session, "-c", cwd)


def pane_is_claude(window_id):
    command = tmux("display-message", "-p", "-t", window_id, "#{pane_current_command}", check=False)
    return bool(CLAUDE_VERSION.match(command)) or command in CLAUDE_COMMANDS


def window_alive(window_id):
    return bool(window_id) and window_id in tmux("list-windows", "-a", "-F", "#{window_id}", check=False).split()


def find_live_window(session, name, preferred_id=None):
    """A window with a live Claude: the remembered id first, then any window of that name."""
    if preferred_id and window_alive(preferred_id) and pane_is_claude(preferred_id):
        return preferred_id
    if not has_session(session):
        return None
    for line in tmux("list-windows", "-t", session, "-F", "#{window_id} #{window_name}", check=False).splitlines():
        window_id, _, window_name = line.partition(" ")
        if window_name == name and pane_is_claude(window_id):
            return window_id
    return None


def send_line(window_id, line):
    """One line only: send-keys submits on every newline."""
    tmux("send-keys", "-t", window_id, "-l", line.replace("\n", " "))
    tmux("send-keys", "-t", window_id, "Enter")


def new_window(session, name, cwd, launcher):
    return tmux("new-window", "-P", "-F", "#{window_id}", "-t", session, "-n", name, "-c", cwd,
                f"{os.environ.get('SHELL', '/bin/zsh')} -lc {shell_quote(launcher)}")


def rename_window(window_id, name):
    tmux("rename-window", "-t", window_id, name, check=False)


def current_window_id():
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    return tmux("display-message", "-p", "-t", pane, "#{window_id}", check=False) or None


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"
