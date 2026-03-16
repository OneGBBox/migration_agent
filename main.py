"""
main.py
Entry point for the ASP.NET MVC → .NET Core 8 multi-agent migration system.
Compatible with CrewAI 1.9.3 + Python 3.12.

TOKEN OPTIMISATION — what changed and why:
  ─────────────────────────────────────────────────────────────────────────
  PROBLEM  The original design ran all 5 tasks inside a single Crew with
           context=[...] chaining and memory=True.  By the time the Manager
           task ran it received the full raw output of Tasks 1-4 plus all
           intermediate reasoning steps — easily 50K-80K tokens in one call.

  FIX 1 — One mini-crew per task (main change here)
           Each task now runs as its own Crew(tasks=[single_task]).
           The LLM for that task starts with a FRESH context window; it does
           not inherit the entire conversation history of prior tasks.

  FIX 2 — File-based checkpointing (checkpoint.py)
           Each task output is saved to disk right after it completes.
           If the process crashes or hits a token limit, the next run SKIPS
           already-completed tasks and resumes from where it stopped.
           Use --clear-checkpoints to start completely fresh.

  FIX 3 — Summarised prior context  (see tasks.py)
           Instead of context=[prior_task] (which injects 5-10K raw tokens),
           we inject only the first 2 500 chars of the prior task's output
           into the new task's description string.  Agents have tools to
           re-read any file they need — they don't need the full history.

  FIX 4 — memory=False on all Crews
           CrewAI's shared memory adds tokens on every agent call.  With
           file-based checkpointing we don't need in-memory cross-agent state.

  FIX 5 — Cheaper models for read-only agents  (see agents.py)
           Manager + Critic use gpt-4o-mini; Developer + Tester keep gpt-4o.
  ─────────────────────────────────────────────────────────────────────────

Usage:
  uv run main.py
  uv run main.py --legacy ./legacy_sample --output ./output/MigratedApp
  uv run main.py --no-retry
  uv run main.py --clear-checkpoints      # wipe saved progress and start fresh
"""
import argparse
import sys
import time
from pathlib import Path

from pydantic import ValidationError
from crewai import Crew, Process

from config.settings import load_config
from agents import create_all_agents
from tasks import create_tasks
from rate_limiter import setup_limiter
# ──────────────────────────────────────────────
# Core runner
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# Internal helper — run a single task as its own mini-crew
# ──────────────────────────────────────────────────────────────────────────────

def _run_single_task(task_name: str, task, agent, verbose: bool, checkpoint: CheckpointManager) -> str:
    """
    Run one task in its own Crew with a fresh context window.

    If the task is already checkpointed, skip execution and return the saved
    output immediately.  Otherwise run the mini-crew, save the result, and
    return it.

    Args:
        task_name:  Short identifier used for checkpointing (e.g. "analyze").
        task:       The CrewAI Task object (already built by tasks.py builder).
        agent:      The agent assigned to this task.
        verbose:    Forward verbosity setting from config.
        checkpoint: CheckpointManager instance shared across all tasks.

    Returns:
        The task output as a string.
    """
    if checkpoint.is_done(task_name):
        print(f"\n  ⏭️   [{task_name.upper()}] Checkpoint found — skipping.\n")
        return checkpoint.load(task_name)

    print(f"\n  🚀  [{task_name.upper()}] Starting task...\n")

    mini_crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=verbose,
        memory=False,          # FIX 4: no shared memory — eliminates memory token overhead
    )

    result = mini_crew.kickoff()
    result_str = str(result)

    checkpoint.save(task_name, result_str)
    return result_str


# ──────────────────────────────────────────────────────────────────────────────
# Core runner
# ──────────────────────────────────────────────────────────────────────────────

def run_migration(legacy_path: str, output_path: str, checkpoint: CheckpointManager) -> str:
    """
    Loads config, builds agents, runs all 5 tasks sequentially as mini-crews.
    Returns the final Manager report string.
    """
    config = load_config(
        legacy_path_override=legacy_path,
        output_path_override=output_path,
    )

    cp_dir = config.checkpoint_dir

    print("\n" + "═" * 60)
    print("  ASP.NET MVC  →  .NET Core 8  Migration Agent")
    print("  CrewAI 1.9.3 + Python 3.12")
    print("═" * 60)
    print(config.summary())
    print("─" * 60)
    print(checkpoint.status())
    print("═" * 60 + "\n")

    Path(output_path).mkdir(parents=True, exist_ok=True)

    # ── Rate limiter ─────────────────────────────
    # Patches litellm.completion so every agent call is throttled.
    # Must be called once per run, before any LLM calls are made.
    setup_limiter(rpm_limit=config.llm_rpm, tpm_limit=config.llm_tpm)

    # ── Agents ───────────────────────────────────
    print("⚙️  Creating agents...")
    agents = create_all_agents(
        model=config.llm_model,
        legacy_path=config.legacy_project_path,
        output_path=config.output_project_path,
        max_tokens=config.llm_max_tokens,
    )

    # ── Task 1: Analyze ───────────────────────────────────────────────────
    result_analyze = _run_single_task(
        task_name="analyze",
        task=build_analyze_task(
            agent=agents["developer"],
            legacy_path=legacy_path,
            output_file=f"{cp_dir}/analyze.md",
        ),
        agent=agents["developer"],
        verbose=config.verbose,
        checkpoint=checkpoint,
    )

    # ── Crew ─────────────────────────────────────
    # IMPORTANT: Process.sequential does NOT use manager_agent.
    # The Manager is the agent assigned to task_report (Task 5).
    # It receives all prior task outputs via context=[...] in tasks.py.
    #
    # Process.hierarchical WOULD use manager_agent but requires an LLM
    # with function calling and adds overhead we don't need for this scope.
    print("🚀  Assembling crew (sequential process)...\n")

    crew = Crew(
        agents=[
            agents["manager"],
            agents["developer"],
            agents["tester"],
            agents["critic"],
        ],
        tasks=tasks,
        process=Process.sequential,   # Tasks run one after another in order
        verbose=config.verbose,       # bool from pydantic-settings
        memory=config.use_memory,     # False by default — saves embedding API calls
        max_rpm=config.llm_rpm,       # Rate limit here, NOT on LLM() — CrewAI RPMController
    )

    # ── Task 3: Test ──────────────────────────────────────────────────────
    result_test = _run_single_task(
        task_name="test",
        task=build_test_task(
            agent=agents["tester"],
            output_path=output_path,
            prior_migrate_summary=checkpoint.load_summary("migrate"),
            output_file=f"{cp_dir}/test.md",
        ),
        agent=agents["tester"],
        verbose=config.verbose,
        checkpoint=checkpoint,
    )

    # ── Task 4: Review ────────────────────────────────────────────────────
    result_review = _run_single_task(
        task_name="review",
        task=build_review_task(
            agent=agents["critic"],
            output_path=output_path,
            output_file=f"{cp_dir}/review.md",
        ),
        agent=agents["critic"],
        verbose=config.verbose,
        checkpoint=checkpoint,
    )

    # ── Task 5: Report ────────────────────────────────────────────────────
    result_report = _run_single_task(
        task_name="report",
        task=build_report_task(
            agent=agents["manager"],
            output_path=output_path,
            prior_analyze_summary=checkpoint.load_summary("analyze"),
            prior_migrate_summary=checkpoint.load_summary("migrate"),
            prior_test_summary=checkpoint.load_summary("test"),
            prior_review_summary=checkpoint.load_summary("review"),
            output_file=f"{cp_dir}/report.md",
        ),
        agent=agents["manager"],
        verbose=config.verbose,
        checkpoint=checkpoint,
    )

    # ── Save final report ─────────────────────────────────────────────────
    report_path = Path(output_path) / "MIGRATION_REPORT.md"
    report_path.write_text(result_report, encoding="utf-8")

    print("\n" + "═" * 60)
    print("  FINAL MIGRATION REPORT")
    print("═" * 60)
    print(result_report)
    print("═" * 60)
    print(f"\n📄  Report saved → {report_path}\n")

    return result_report


# ──────────────────────────────────────────────────────────────────────────────
# Retry loop
# Manager says INCOMPLETE → re-run up to N times
# ──────────────────────────────────────────────────────────────────────────────

def run_with_retry(legacy_path: str, output_path: str, checkpoint: CheckpointManager) -> str:
    config = load_config(
        legacy_path_override=legacy_path,
        output_path_override=output_path,
    )

    for attempt in range(1, config.max_retry_loops + 1):
        print(f"\n{'─' * 60}")
        print(f"  ATTEMPT {attempt} of {config.max_retry_loops}")
        print(f"{'─' * 60}")

        result = run_migration(legacy_path, output_path, checkpoint)

        if "STATUS: COMPLETE" in result.upper():
            print(f"\n✅  Migration COMPLETE on attempt {attempt}!")
            return result

        if attempt < config.max_retry_loops:
            print(
                f"\n⚠️   Migration INCOMPLETE — waiting 60s before retry "
                f"(attempt {attempt + 1}) to let OpenAI rate-limit window reset...")
            time.sleep(60)
            print("     Retrying now. The Developer will use the fix list from this report.\n")
        else:
            print(f"\n❌  Still INCOMPLETE after {config.max_retry_loops} attempts.")
            print("    Review MIGRATION_REPORT.md for remaining issues.")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── Load config early to use as CLI defaults ──────────────────────────
    try:
        config = load_config()
    except ValidationError as e:
        print("\n❌  Failed to load configuration:")
        for error in e.errors():
            field = " → ".join(str(x) for x in error["loc"])
            print(f"   • {field}: {error['msg']}")
        print("\n   Make sure OPENAI_API_KEY is set in your .env file")
        print("   or as a system variable: $env:OPENAI_API_KEY='sk-proj-...'")
        print("   Get a key at: https://console.openai.com/\n")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Multi-agent ASP.NET MVC → .NET Core 8 migration tool (CrewAI 1.9.3)"
    )
    parser.add_argument(
        "--legacy",
        default=config.legacy_project_path,
        help=f"Path to legacy ASP.NET MVC project (default: {config.legacy_project_path})",
    )
    parser.add_argument(
        "--output",
        default=config.output_project_path,
        help=f"Output path for migrated .NET Core 8 project (default: {config.output_project_path})",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Run once only — skip the retry loop",
    )
    parser.add_argument(
        "--clear-checkpoints",
        action="store_true",
        help="Wipe all saved task checkpoints and start the migration from scratch",
    )

    args, _ = parser.parse_known_args()

    # ── Validate once here — before any agent work starts ─────────────────
    try:
        load_config(
            legacy_path_override=args.legacy,
            output_path_override=args.output,
        ).validate()
    except (ValidationError, ValueError) as e:
        print(f"\n{e}\n")
        sys.exit(1)

    # ── Set up checkpoint manager ─────────────────────────────────────────
    final_config = load_config(
        legacy_path_override=args.legacy,
        output_path_override=args.output,
    )
    checkpoint = CheckpointManager(checkpoint_dir=final_config.checkpoint_dir)

    if args.clear_checkpoints:
        checkpoint.clear()

    # ── Run ───────────────────────────────────────────────────────────────
    if args.no_retry:
        run_migration(args.legacy, args.output, checkpoint)
    else:
        run_with_retry(args.legacy, args.output, checkpoint)
