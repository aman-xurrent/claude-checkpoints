# A code explanation, worked

The rule is in `~/.claude/CLAUDE.md` under **Code Explanations**. This shows what it means.

The code is real: `~/personal/scripts/phased/tmux.py`, the function that decides which tmux window
holds a pull request's Claude session.

---

## The example

**Quick context.** This picks the tmux window that holds the Claude session for one pull request, so
the daemon can type a prompt into it. It prefers the window it remembers, and falls back to searching
by name when that memory is wrong.

### `window_name_of` — ask tmux what a window is called

```python
def window_name_of(window_id):
    return tmux("display-message", "-p", "-t", window_id, "#{window_name}", check=False).strip()
```

- `tmux("display-message", ...)` = run `tmux display-message` and hand back what it printed.
- `-p` = print the answer to standard output instead of showing it in the tmux status bar.
- `-t window_id` = ask about this one window, for example `@4`.
- `"#{window_name}"` = a tmux format string. tmux replaces it with the window's name. The braces are
  tmux's own syntax, not Python's.
- `check=False` = do not raise when the command fails. A window that has closed makes tmux exit
  non-zero, and here that should read as "no name", not as a crash.
- `.strip()` = drop the trailing newline tmux prints.

### `find_live_window` — the remembered window, but only if it is still the right one

```python
def find_live_window(session, name, preferred_id=None):
    if (preferred_id and window_alive(preferred_id) and pane_is_claude(preferred_id)
            and window_name_of(preferred_id) == name):
        return preferred_id
```

- `preferred_id=None` = the id the daemon wrote down last time, for example `@3`. Optional, because
  the first lookup has nothing remembered.
- `preferred_id and ...` = a guard. An empty or `None` id short-circuits, so the later calls never
  run with a meaningless id.
- `window_alive(preferred_id)` = does a window with that id still exist.
- `pane_is_claude(preferred_id)` = is Claude the program running in it.
- `window_name_of(preferred_id) == name` = **and is it the window for this pull request.** Without
  this, any surviving Claude window passes, because it answers yes to the first two questions.

```python
    if not has_session(session):
        return None
```

- An early return. No tmux session means there is nothing to search, so stop here rather than nest
  the rest of the function inside an `if`.

```python
    for line in tmux("list-windows", "-t", session, "-F", "#{window_id} #{window_name}", check=False).splitlines():
        window_id, _, window_name = line.partition(" ")
        if window_name == name and pane_is_claude(window_id):
            return window_id
    return None
```

- `list-windows -t session` = every window in that tmux session.
- `-F "#{window_id} #{window_name}"` = print each one as `@4 pr1027`, so one line is one window.
- `.splitlines()` = one string per window.
- `line.partition(" ")` = split on the **first** space only, returning three pieces: before,
  the separator, after. `split()` would break a window name that contains a space.
- `window_id, _, window_name = ...` = unpack those three. `_` is the conventional name for a value
  that is deliberately ignored, here the separator itself.
- `return None` at the end = no window of that name runs Claude. The caller decides what to do.

**Idioms a novice would not know.** `#{...}` is tmux's format language, filled in by tmux before the
output is printed. `str.partition` differs from `str.split` by splitting once and always returning
three items, so unpacking never raises. A bare `_` marks an ignored value. `check=False` here is a
deliberate choice to treat a failure as a value rather than an exception. Python's `and` chain
short-circuits, so the cheap checks run before the ones that shell out.

**Net effect.** The daemon finds the right window even when the id it remembered is stale, and it
never types into someone else's session, which is what happened before the name check existed:
`find_live_window("ipaas", "pr1027", "@3")` returned `@3`, a different Claude session in a different
directory.

---

## Why that passes the rule

- It leads with **what and why**, in one line, before any mechanics.
- Each block has a **heading naming what it does**, then the real code, then bullets.
- Every bullet maps a **real fragment** to plain meaning: `- <fragment> = <meaning>`. No bullet
  describes code that is not shown.
- The idioms are named and defined in place: `#{...}`, `partition`, the bare `_`, `check=False`,
  short-circuiting. A capable novice is not left to look any of them up.
- It ends with the **net effect**, and that effect is concrete: the real inputs and the real wrong
  answer they used to produce.

## What fails the rule

- "This function finds the window, checking that it exists and runs Claude, then falls back to
  searching." A summary of the code, not an explanation of it. No fragment is quoted, so nothing can
  be checked against the source.
- Explaining `partition` as "splits the string". That is what `split` does. The reason it is
  `partition` is the difference, and the difference is the explanation.
- Skipping `check=False` because it looks like boilerplate. It is a decision, and it is why a closed
  window reads as empty instead of raising.
