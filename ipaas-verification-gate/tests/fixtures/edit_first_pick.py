"""GIT_SEQUENCE_EDITOR for the tests: turn the first `pick` of the todo list into `edit`."""

import sys
from pathlib import Path

todo = Path(sys.argv[1])
todo.write_text(todo.read_text().replace("pick", "edit", 1))
