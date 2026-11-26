"""One chat of a fleet (fleet.py), as a Flash of its own in the background.

    python -m flash.fleet_worker SPEC.json

SPEC says the task, the model and the worktree. It works there, with the
same loop and tools as a sub-agent, and keeps writing where it is up to
to the status file SPEC names, which the fleet's view reads. It never
asks anything: the user said yes to the fleet as a whole, and it works
only in its own private worktree, in the OS sandbox when the user
started it in Ask first mode and the sandbox is there.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

EVERY = 0.4


def _write(status: Path, said: dict) -> None:
    temp = status.with_suffix(".tmp")
    temp.write_text(json.dumps(said), encoding="utf-8")
    os.replace(temp, status)


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    status = Path(spec["status"])
    os.chdir(spec["path"])

    from . import agent, ai, sandbox, worktrees
    from . import tools as flash_tools

    ai.refresh_config()
    ai.Config.model = spec["model"]
    flash_tools.MODEL_NAME = spec["model"]
    # Yes was said once, for the whole fleet.
    flash_tools.NO_COMMAND_CONFIRMATION = True
    if spec.get("sandbox") and sandbox.available():
        sandbox.enabled = lambda: True  # type: ignore[assignment]

    task = "\n\n".join([spec["task"], worktrees.describe(spec["path"]), spec.get("prompt", "")])
    entry = agent.SubAgent(id=f"fleet{os.getpid()}", task=task)

    def stop(*_args) -> None:
        entry.stop_asked = True
        flash_tools.stop_commands(f"agent:{entry.id}")

    signal.signal(signal.SIGTERM, stop)
    worker = threading.Thread(target=agent._run, args=(entry,), daemon=True)
    worker.start()

    def said(state: str) -> dict:
        snap = agent._snapshot(entry)
        return {"state": state, "activity": snap.activity, "rounds": snap.rounds,
                "steps": [s.label for s in snap.steps[-6:]], "result": snap.result,
                "finished": snap.finished}

    while worker.is_alive():
        _write(status, said(agent.RUNNING))
        worker.join(EVERY)
    state = {agent.DONE: "done"}.get(entry.status, "stopped" if entry.stop_asked else "error")
    _write(status, said(state))
    return 0 if state == "done" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
