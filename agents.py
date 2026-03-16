"""
agents/agents.py

All 4 CrewAI agents for the migration pipeline.

KEY CHANGES from 0.28.8 → 1.9.3:
  1. LLM:     from crewai import LLM  (NOT langchain_openai.ChatOpenAI)
  2. verbose: True/False only  (NOT 0/1/2)
  3. manager_agent is ONLY for Process.hierarchical — removed from here,
     handled in main.py with Process.sequential

TOKEN OPTIMISATION CHANGES:
  - Manager + Critic now use `fast_model` (gpt-4o-mini by default)
    These agents only read + summarise — they don't generate code.
  - Developer + Tester keep the main model (gpt-4o) for code quality.
  - max_iter reduced on all agents to cap runaway token loops:
      Manager   5 → 3
      Developer 10 → 5
      Tester     8 → 4
      Critic     6 → 3
"""

from crewai import Agent, LLM             # ← LLM is now from crewai directly

from migration_tools import (
    get_developer_tools,
    get_tester_tools,
    get_critic_tools,
)


def create_llm(
    model: str = "gpt-4o",
    temperature: float = 0.1,
    max_tokens: int = 8192,
) -> LLM:
    """
    CrewAI 1.x uses its own LLM wrapper backed by LiteLLM.
    Low temperature = more deterministic code output.
    max_tokens: raise from GPT-4o's default 4096 to 16384 so write_batch_files
                (19 files ≈ 5000+ tokens of JSON) is never truncated mid-response.
    NOTE: rpm/rate-limiting belongs on Crew(max_rpm=...), NOT on LLM().
          Passing rpm to LLM causes 'Completions.create() unexpected keyword argument rpm'.
    """
    return LLM(model=model, temperature=temperature, max_tokens=max_tokens)


def create_all_agents(
    model: str = "gpt-4o",
    fast_model: str = "gpt-4o-mini",
    legacy_path: str = "./legacy_sample",
    output_path: str = "./output/LegacyInventory",
    max_tokens: int = 8192,
) -> dict:
    """
    Factory — creates and returns all 4 agents as a dict.
    Call once from main.py.

    Args:
        model:       Full model for code-heavy agents (Developer, Tester).
        fast_model:  Cheaper/faster model for read-only agents (Manager, Critic).
                     gpt-4o-mini uses the same API key and is ~15x cheaper per token.
    """
    llm = create_llm(model, max_tokens=max_tokens)

    # ──────────────────────────────────────────────
    # Agent 1: Manager
    # Orchestrates the crew — reads reports, makes
    # the final COMPLETE / INCOMPLETE decision.
    # NO tools. Does zero actual coding work.
    # Uses fast_model — only reads summaries.
    # ──────────────────────────────────────────────
    manager = Agent(
        role="Migration Project Manager",
        goal=(
            "Coordinate the full migration of a legacy ASP.NET MVC application to .NET Core 8. "
            "Collect reports from Developer, Tester, and Critic and decide when the migration "
            "meets the 90% quality threshold. "
            "Flag issues clearly if the Tester reports failures or the Critic scores below 80."
        ),
        backstory=(
            "You are a senior technical project manager with 15 years of experience "
            "overseeing .NET migration projects. "
            "You never write code — your job is to read reports and make the final quality call. "
            "You accept a migration as complete only when the Tester passes at least 6/7 tests "
            "AND the Critic scores 80 or above."
        ),
        llm=llm_fast,              # gpt-4o-mini — no code generation needed
        tools=[],                  # Manager has no tools — coordinates only
        allow_delegation=False,
        verbose=True,
        max_iter=3,                # was 5 — reduced to cap token loops
    )

    # ──────────────────────────────────────────────
    # Agent 2: Developer / Analyzer
    # Phase 1 — Reads and maps the legacy project
    # Phase 2 — Writes the full .NET Core 8 project
    # Uses main model — code generation is complex.
    # ──────────────────────────────────────────────
    developer = Agent(
        role="Senior .NET Migration Developer",
        goal=(
            f"Analyze the legacy ASP.NET MVC project at '{legacy_path}', "
            f"then generate a complete working .NET Core 8 MVC CRUD application at '{output_path}'. "
            "Migrate all controllers, views, models, EF Core DbContext, "
            "appsettings.json, and Program.cs. "
            "Fix any issues reported by the Tester or Critic."
        ),
        backstory=(
            "You are a .NET expert who has migrated hundreds of legacy ASP.NET apps to .NET Core. "
            "Key migration rules you always follow:\n"
            "- System.Web → Microsoft.AspNetCore.Mvc\n"
            "- HttpContext.Current → IHttpContextAccessor\n"
            "- Web.config → appsettings.json + IConfiguration\n"
            "- RouteConfig.cs → Program.cs MapControllerRoute\n"
            "- EF6 DbContext → EF Core DbContext with OnModelCreating\n"
            "- Html.BeginForm() → <form asp-action=''> tag helpers\n"
            "You always use async/await, constructor-injected DbContext, "
            "and never hardcode connection strings."
        ),
        llm=llm_main,              # gpt-4o — highest quality for code generation
        tools=get_developer_tools(),
        allow_delegation=False,
        verbose=True,
        max_iter=20,
    )

    # ──────────────────────────────────────────────
    # Agent 3: Tester
    # Writes xUnit tests, runs dotnet test,
    # reports PASS/FAIL for every CRUD operation.
    # Uses main model — test code generation.
    # ──────────────────────────────────────────────
    tester = Agent(
        role="QA Engineer — .NET Core Migration Tester",
        goal=(
            f"Write xUnit tests for the migrated .NET Core 8 app at '{output_path}'. "
            "Run the tests with dotnet test. "
            "Verify Create, Read, Update, Delete all work correctly. "
            "Report a clear PASS/FAIL list. If tests fail, explain exactly what broke."
        ),
        backstory=(
            "You are a QA automation engineer specialising in .NET Core testing. "
            "You write clean xUnit tests following AAA (Arrange / Act / Assert). "
            "You use Microsoft.EntityFrameworkCore.InMemory so tests run without a "
            "real SQL Server. "
            "Your report always lists: test name, PASS or FAIL, "
            "and the exact error message if it failed."
        ),
        llm=llm_main,              # gpt-4o — test code needs same quality as source code
        tools=get_tester_tools(),
        allow_delegation=False,
        verbose=True,
        max_iter=12,
    )

    # ──────────────────────────────────────────────
    # Agent 4: Critic / Code Reviewer
    # Reviews code quality, scores 0–100.
    # Uses fast_model — read-only structured review.
    # ──────────────────────────────────────────────
    critic = Agent(
        role="Senior .NET Code Reviewer",
        goal=(
            f"Review all migrated .NET Core 8 code at '{output_path}'. "
            "Check for deprecated APIs, bad DI wiring, wrong middleware order, "
            "hardcoded secrets, missing error handling, and bad Razor syntax. "
            "Score the migration 0–100. A score of 80+ means the migration is acceptable."
        ),
        backstory=(
            "You are a principal .NET engineer who reviews code for Fortune 500 clients. "
            "Key issues you check:\n"
            "- app.UseRouting() missing before app.UseAuthorization()\n"
            "- DbContext registered with wrong lifetime (should be Scoped)\n"
            "- Synchronous EF calls (SaveChanges vs SaveChangesAsync)\n"
            "- Html.* helpers left in Razor views instead of tag helpers\n"
            "- Connection strings left in Web.config instead of appsettings.json\n"
            "Your reviews are structured, scored numerically, and fully actionable."
        ),
        llm=llm_fast,              # gpt-4o-mini — reading and scoring, no code generation
        tools=get_critic_tools(),
        allow_delegation=False,
        verbose=True,
        max_iter=10,
    )

    return {
        "manager":   manager,
        "developer": developer,
        "tester":    tester,
        "critic":    critic,
    }
