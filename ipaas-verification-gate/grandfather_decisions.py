"""Keep the decisions recorded before the owner-words store went live. install.sh runs it; it is idempotent.

Lists every decision recorded before OWNER_WORDS_EPOCH, read the way excused_by and waiver_for read them, so
they keep holding once the store goes live. Then checks each one holds, and makes the files that quote the
owner readable by the owner only.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

GATE = Path(__file__).resolve().parent / "gate.py"
spec = importlib.util.spec_from_file_location("gate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

listing = subprocess.run(["git", "-C", str(gate.MAIN_REPOSITORY), "worktree", "list", "--porcelain"],
                         capture_output=True, text=True, check=True).stdout
roots = [Path(line.split(" ", 1)[1]) for line in listing.splitlines() if line.startswith("worktree ")]
records = [record for root in roots if root.exists() for record in gate.read_deviations(root)]
records += [gate.read_json(path) for path in sorted(gate.WAIVERS_DIRECTORY.glob("*.json"))]
earlier = [record for record in records if record and (record.get("recorded_at") or "") < gate.OWNER_WORDS_EPOCH]
digests = sorted({gate.decision_digest(record) for record in earlier})
gate.write_json(gate.GRANDFATHERED_DECISIONS_FILE, digests, mode=gate.PRIVATE_FILE_MODE)
gate.PROCESS_MEMO.clear()
holding = [record.get("id") or record.get("digest") for record in earlier if gate.owner_decision_holds(record)]
print(f"epoch {gate.OWNER_WORDS_EPOCH}: {len(earlier)} earlier decision(s) listed, {len(holding)} hold: {holding}")
assert len(holding) == len(earlier), "an earlier decision stopped holding"

private = [gate.SKIPS_LOG, *gate.TRACE_DIRECTORY.glob("*.jsonl"), *gate.OWNER_WORDS_DIRECTORY.glob("*.jsonl")]
for path in private:
    if path.exists():
        os.chmod(path, gate.PRIVATE_FILE_MODE)
print(f"{len(private)} file(s) made readable by the owner only")
print(json.dumps({"grandfathered": str(gate.GRANDFATHERED_DECISIONS_FILE), "digests": digests}, indent=2))
