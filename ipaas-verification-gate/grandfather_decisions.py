"""Keep the decisions recorded before the owner-words store went live. install.sh runs it on every install.

The first run on a machine takes the list: it writes the moment the store went live on this machine, and the
digest of every decision recorded before it, read the way excused_by and waiver_for read them, so they keep
holding. Later runs keep that list as it was taken, because a record dated before the moment and written after
it would otherwise excuse itself by its own date, and they name any such record. Every run makes the files that
quote the owner readable by the owner only.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parent / "gate.py"
spec = importlib.util.spec_from_file_location("gate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

roots = gate.repository_worktrees(gate.MAIN_REPOSITORY)
records = [record for root in roots if root.exists() for record in gate.read_deviations(root)]
records += [gate.read_json(path) for path in sorted(gate.WAIVERS_DIRECTORY.glob("*.json"))]
records = [record for record in records if isinstance(record, dict)]

if gate.GRANDFATHERED_DECISIONS_FILE.exists():
    epoch = gate.owner_words_epoch()
    listed = set(gate.grandfathered_decisions())
    unlisted = [record.get("id") or record.get("digest") for record in records
                if gate.recorded_before(record, epoch) and gate.decision_digest(record) not in listed]
    print(f"epoch {epoch}: the list taken then holds {len(listed)} decision(s) and stays as it was")
    if unlisted:
        print(f"WARNING: {len(unlisted)} decision(s) dated before {epoch} were not on the list when it was taken, so "
              f"they must quote the owner: {unlisted}")
elif gate.OWNER_WORDS_EPOCH_FILE.exists():
    # The epoch is set but the list is gone: deleting the list and taking a new one would relist every record
    # dated before now, excusing decisions the store never checked. Do not take a new list; tell the owner.
    sys.exit(f"the grandfather list {gate.GRANDFATHERED_DECISIONS_FILE} is missing but the epoch "
             f"{gate.OWNER_WORDS_EPOCH_FILE} is set. Restore the list from a backup, or clear both to take a new "
             "one, which makes every decision recorded before now quote the owner again.")
else:
    epoch = gate.now_iso()
    gate.write_atomically(gate.OWNER_WORDS_EPOCH_FILE, epoch + "\n", mode=gate.PRIVATE_FILE_MODE)
    digests = sorted({gate.decision_digest(record) for record in records if gate.recorded_before(record, epoch)})
    gate.write_json(gate.GRANDFATHERED_DECISIONS_FILE, digests, mode=gate.PRIVATE_FILE_MODE)
    print(f"epoch {epoch}: {len(digests)} earlier decision(s) listed")
    print(json.dumps({"grandfathered": str(gate.GRANDFATHERED_DECISIONS_FILE), "digests": digests}, indent=2))

private = [path for path in (gate.SKIPS_LOG, *gate.TRACE_DIRECTORY.glob("*.jsonl"),
                              *gate.OWNER_WORDS_DIRECTORY.glob("*.jsonl")) if path.exists()]
for path in private:
    os.chmod(path, gate.PRIVATE_FILE_MODE)
print(f"{len(private)} file(s) made readable by the owner only")
