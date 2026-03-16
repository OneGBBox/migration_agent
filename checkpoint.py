"""
checkpoint.py

File-based checkpoint manager — saves each task's output to disk so the
migration can resume from the last completed task instead of restarting
from scratch on token exhaustion or process crashes.

How it works:
  - After each task completes, its output is written to  ./output/.checkpoints/<task>.md
  - On the next run the orchestrator checks for that file first
  - If the file exists → skip the task and load the saved output
  - If the file is missing → run the task and save on success

Usage:
  cp = CheckpointManager()
  if cp.is_done("analyze"):
      result = cp.load("analyze")
  else:
      result = run_task(...)
      cp.save("analyze", result)

  cp.clear()   # wipe all checkpoints and start fresh
  cp.status()  # human-readable overview
"""

import json
from datetime import datetime
from pathlib import Path

# Canonical execution order — used by status() and first_incomplete()
TASK_ORDER = ["analyze", "migrate", "test", "review", "report"]

# How many characters of a prior task's output to inject into the next
# task's description.  ~2500 chars ≈ 600 tokens — enough for key facts
# without blowing up the new task's context window.
MAX_SUMMARY_CHARS = 2500


class CheckpointManager:
    """Lightweight file-based checkpoint store for the migration pipeline."""

    def __init__(self, checkpoint_dir: str = "./output/.checkpoints"):
        self.dir = Path(checkpoint_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────

    def is_done(self, task_name: str) -> bool:
        """Return True if this task already has a saved output on disk."""
        return self._output_path(task_name).exists()

    def load(self, task_name: str) -> str:
        """Load and return the saved output for a completed task."""
        return self._output_path(task_name).read_text(encoding="utf-8")

    def load_summary(self, task_name: str) -> str:
        """
        Load a truncated version of a task's output — safe to inject
        into the next task's description without blowing up its context.
        """
        full = self.load(task_name)
        if len(full) <= MAX_SUMMARY_CHARS:
            return full
        return (
            full[:MAX_SUMMARY_CHARS]
            + f"\n\n... [truncated to {MAX_SUMMARY_CHARS} chars — full output in "
            + str(self._output_path(task_name))
            + "]"
        )

    def save(self, task_name: str, output: str) -> None:
        """Persist a task's output and write a companion metadata file."""
        self._output_path(task_name).write_text(output, encoding="utf-8")
        meta = {
            "task": task_name,
            "completed_at": datetime.now().isoformat(),
            "chars": len(output),
        }
        self._meta_path(task_name).write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        print(f"  💾  Checkpoint saved → {self._output_path(task_name)}")

    def clear(self) -> None:
        """Delete all checkpoint files so the next run starts from Task 1."""
        removed = 0
        for f in self.dir.glob("*.md"):
            f.unlink()
            removed += 1
        for f in self.dir.glob("*.json"):
            f.unlink()
        print(f"  🗑️   Cleared {removed} checkpoint(s) — next run starts from Task 1.")

    def status(self) -> str:
        """Return a human-readable overview of completed / pending tasks."""
        lines = ["  Checkpoint status:"]
        for name in TASK_ORDER:
            if self.is_done(name):
                meta_file = self._meta_path(name)
                when = ""
                if meta_file.exists():
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    when = f"  (completed {meta.get('completed_at', '')[:19]})"
                lines.append(f"    ✅  {name}{when}")
            else:
                lines.append(f"    ⬜  {name}  (pending)")
        return "\n".join(lines)

    def first_incomplete(self) -> str | None:
        """Return the name of the first task that has NOT been checkpointed."""
        for name in TASK_ORDER:
            if not self.is_done(name):
                return name
        return None  # all tasks done

    # ── Private helpers ────────────────────────────────────────────────────

    def _output_path(self, task_name: str) -> Path:
        return self.dir / f"{task_name}.md"

    def _meta_path(self, task_name: str) -> Path:
        return self.dir / f"{task_name}.meta.json"
