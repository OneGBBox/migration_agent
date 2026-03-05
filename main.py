"""
main.py
Entry point for the ASP.NET MVC → .NET Core 8 multi-agent migration system.
Compatible with CrewAI 1.9.3 + Python 3.12.

All configuration is loaded from config/settings.py which uses
pydantic-settings to read values from .env or system environment variables.

Flow:
  1. load_config()     → reads .env via pydantic-settings → MigrationConfig
  2. config.validate() → fails fast if API key missing or legacy path wrong
  3. run_with_retry()  → passes config values to agents + tasks

Usage:
  uv run main.py
  uv run main.py --legacy ./legacy_sample --output ./output/MigratedApp
  uv run main.py --no-retry
"""
import argparse
import sys
from pathlib import Path
# ← needed to catch pydantic-settings errors
from pydantic import ValidationError
from crewai import Crew, Process

from config.settings import load_config       # ← single source of truth
from agents import create_all_agents
from tasks import create_tasks
# ──────────────────────────────────────────────
# Core runner
# ──────────────────────────────────────────────


def run_migration(legacy_path: str, output_path: str) -> str:
    """
    Loads config, builds agents + tasks, assembles the Crew, kicks it off.
    Config is NOT re-validated here — validation happens once in the CLI
    block before this function is ever called.
    Returns the final report string.
    """
    config = load_config(
        legacy_path_override=legacy_path,
        output_path_override=output_path,
    )

    print("\n" + "═" * 60)
    print("  ASP.NET MVC  →  .NET Core 8  Migration Agent")
    print("  CrewAI 1.9.3 + Python 3.12")
    print("═" * 60)
    print(config.summary())  # safe summary that never prints the API key
    print("═" * 60 + "\n")

    Path(config.output_project_path).mkdir(parents=True, exist_ok=True)

    # ── Agents ───────────────────────────────────
    print("⚙️  Creating agents...")
    agents = create_all_agents(
        model=config.llm_model,
        legacy_path=config.legacy_project_path,
        output_path=config.output_project_path,
    )

    # ── Tasks ────────────────────────────────────
    print("📋  Creating tasks...")
    tasks = create_tasks(
        agents=agents,
        legacy_path=config.legacy_project_path,
        output_path=config.output_project_path,
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
        memory=True,                  # Agents share memory across tasks
    )

    # ── Kick off ─────────────────────────────────
    print("🏃  Running migration...\n")
    result = crew.kickoff()
    result_str = str(result)

    # ── Save report ──────────────────────────────
    report_path = Path(config.output_project_path) / "MIGRATION_REPORT.md"
    report_path.write_text(result_str, encoding="utf-8")

    print("\n" + "═" * 60)
    print("  FINAL MIGRATION REPORT")
    print("═" * 60)
    print(result_str)
    print("═" * 60)
    print(f"\n📄  Report saved → {report_path}\n")

    return result_str


# ──────────────────────────────────────────────
# Retry loop
# Manager says INCOMPLETE → re-run up to N times
# ──────────────────────────────────────────────

def run_with_retry(legacy_path: str, output_path: str) -> str:
    config = load_config(
        legacy_path_override=legacy_path,
        output_path_override=output_path,
    )

    for attempt in range(1, config.max_retry_loops + 1):
        print(f"\n{'─' * 60}")
        print(f"  ATTEMPT {attempt} of {config.max_retry_loops}")
        print(f"{'─' * 60}")

        result = run_migration(legacy_path, output_path)

        if "STATUS: COMPLETE" in result.upper():
            print(f"\n✅  Migration COMPLETE on attempt {attempt}!")
            return result

        if attempt < config.max_retry_loops:
            print(
                f"\n⚠️   Migration INCOMPLETE — retrying (attempt {attempt + 1})...")
            print("     The Developer will use the fix list from this report.\n")
        else:
            print(
                f"\n❌  Still INCOMPLETE after {config.max_retry_loops} attempts.")
            print("    Review MIGRATION_REPORT.md for remaining issues.")

    return result


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":

    # ── Load config early to use as CLI defaults ──
    # Wrapped in try/except because pydantic-settings raises ValidationError
    # (not ValueError) if OPENAI_API_KEY is missing entirely.
    # We catch it here and print a clean message instead of a pydantic traceback.

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
        default=config.legacy_project_path,  # from .env via pydantic-settings
        help=f"Path to legacy ASP.NET MVC project (default: {config.legacy_project_path})",
    )
    parser.add_argument(
        "--output",
        default=config.output_project_path,  # from .env via pydantic-settings
        help=f"Output path for migrated .NET Core 8 project (default: {config.output_project_path})",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Run once only — skip the retry loop",
    )

    args, _ = parser.parse_known_args()

    # ── Validate once here — before any agent work starts ────────────────
    # This is the single validation point. run_migration() does NOT
    # call validate() again — no double validation on retries.
    try:
        load_config(
            legacy_path_override=args.legacy,
            output_path_override=args.output,
        ).validate()
    except (ValidationError, ValueError) as e:
        print(f"\n{e}\n")
        sys.exit(1)

    # ── Run ──────────────────────────────────────────────────────────────
    if args.no_retry:
        run_migration(args.legacy, args.output)
    else:
        run_with_retry(args.legacy, args.output)
