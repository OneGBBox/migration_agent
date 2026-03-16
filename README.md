# ASP.NET Web Forms → .NET Core 8 Migration Agent

An **Agentic AI system** that autonomously migrates legacy ASP.NET Web Forms projects
to .NET Core 8 MVC — end-to-end, without human intervention during the run.

Built with **CrewAI 1.9.3**, **OpenAI GPT-4o**, and **Python 3.12**, managed by **uv**.

---

## How It Works

Four specialized AI agents collaborate in a five-task sequential pipeline:

```
Task 1: Analyze    →  Task 2: Migrate  →  Task 3: Test  →  Task 4: Review  →  Task 5: Report
   Developer            Developer           Tester            Critic             Manager
```

| Agent | Role |
|---|---|
| **Developer** | Discovers legacy project structure, generates complete .NET Core 8 project |
| **Tester** | Writes xUnit tests, runs `dotnet test`, reports pass/fail per test |
| **Critic** | Reviews code quality against a scoring rubric (0–100) |
| **Manager** | Aggregates all reports, produces COMPLETE / INCOMPLETE verdict |

The system retries automatically if the Manager marks the migration INCOMPLETE,
giving the Developer the fix list from the previous report.

---

## What Gets Migrated

| Legacy (Web Forms) | Generated (.NET Core 8 MVC) |
|---|---|
| `.aspx` / `.aspx.cs` page | Controller class + Razor Views |
| `Page_Load(!IsPostBack)` | `[HttpGet]` action |
| `btnSave_Click` / `btnUpdate_Click` | `[HttpPost]` action + `[ValidateAntiForgeryToken]` |
| `Response.Redirect()` | `return RedirectToAction()` |
| `Request.QueryString["id"]` | `int id` route parameter |
| `SqlConnection` / `SqlCommand` (ADO.NET) | EF Core 8 `DbContext` with async LINQ |
| `EF6 DbContext` | EF Core 8 `DbContext` |
| `Web.config` connection strings | `appsettings.json` |
| `Global.asax` `Application_Start` | `Program.cs` middleware pipeline |
| `packages.config` NuGet deps | `.csproj` `<PackageReference>` entries |

---

## Output Structure

```
./output/
  {ProjectName}.sln               ← .NET solution file (main + test projects)
  {ProjectName}/
    {ProjectName}.csproj          ← net8.0, EF Core packages
    appsettings.json              ← connection string from Web.config
    Program.cs                    ← middleware pipeline
    Data/
      AppDbContext.cs             ← EF Core DbContext, one DbSet<T> per entity
    Models/
      {Entity}.cs                 ← one model per entity, with data annotations
    Controllers/
      {Entity}sController.cs      ← full CRUD, async, injected DbContext
    Views/
      {Entity}s/
        Index.cshtml              ← Bootstrap 5 table
        Create.cshtml             ← form with tag helpers
        Edit.cshtml               ← pre-filled form
        Delete.cshtml             ← confirm page
      Shared/
        _Layout.cshtml            ← Bootstrap 5 nav layout
      _ViewImports.cshtml
      _ViewStart.cshtml
  {ProjectName}.Tests/
    {ProjectName}.Tests.csproj    ← xUnit + EF Core InMemory
    CrudControllerTests.cs        ← 7 tests per controller (in-memory DB)
  MIGRATION_REPORT.md             ← final Manager verdict
```

The output folder name is **auto-derived** from the legacy project's `.sln` filename —
no manual configuration needed.

---

## Requirements

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/) package manager
- .NET 8 SDK (for `dotnet build` / `dotnet test` inside the agent)
- OpenAI API key (GPT-4o recommended)

---

## Setup

```bash
# 1. Install uv (if not already installed)
# Windows (PowerShell):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies
uv sync

# 3. Configure environment
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# Then edit .env and set your OPENAI_API_KEY
```

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | Your OpenAI API key |
| `LEGACY_PROJECT_PATH` | `./legacy_sample` | Path to the legacy ASP.NET Web Forms project |
| `OUTPUT_PROJECT_PATH` | *(auto-derived)* | Output path — leave unset to auto-derive from `.sln` name |
| `LLM_MODEL` | `gpt-4o` | LiteLLM model string |
| `LLM_RPM` | `60` | Max requests per minute (match your OpenAI tier) |
| `LLM_TPM` | `30000` | Max tokens per minute (Tier 1 = 30K, Tier 2 = 450K) |
| `LLM_MAX_TOKENS` | `8192` | Max tokens per LLM response |
| `USE_MEMORY` | `false` | Enable CrewAI shared memory (costs extra embedding tokens) |
| `MAX_RETRY_LOOPS` | `3` | Max retry attempts if Manager returns INCOMPLETE |
| `VERBOSE` | `true` | Show agent reasoning in console |

**Note:** `OUTPUT_PROJECT_PATH` is intentionally left unset. The system auto-derives
it from the legacy project's `.sln` filename (e.g., `LegacyInventory.sln` → `./output/LegacyInventory`).
Even if `./output/MigratedApp` was previously set in your environment, it is always overridden.

---

## Usage

```bash
# Run with defaults from .env
uv run main.py

# Override the legacy project path at runtime
uv run main.py --legacy ./my_legacy_project

# Override both paths
uv run main.py --legacy ./my_legacy_project --output ./output/MyApp

# Single run — skip the retry loop
uv run main.py --no-retry
```

---

## Rate Limiting

A custom sliding-window rate limiter (`rate_limiter.py`) patches `litellm.completion`
globally, so every agent call is automatically throttled before it reaches OpenAI:

- Tracks **RPM** (requests/min) and **TPM** (tokens/min) in a 60-second sliding window
- Calculates the minimum wait needed when a limit is approaching
- Prints `[RateLimiter] Pausing X.Xs ...` when throttling
- Configured via `LLM_RPM` and `LLM_TPM` in `.env`

```
OpenAI Tiers (gpt-4o):
  Tier 1 → LLM_RPM=500  LLM_TPM=30000
  Tier 2 → LLM_RPM=5000 LLM_TPM=450000
  Tier 3 → LLM_RPM=5000 LLM_TPM=800000
```

Between retry attempts the system also waits 60 seconds to let the OpenAI
rate-limit window fully reset before the next attempt.

---

## Custom Tools (`migration_tools.py`)

Six purpose-built `BaseTool` subclasses power the agents:

| Tool | Agents | Description |
|---|---|---|
| `read_file` | Developer, Tester, Critic | Read a single file |
| `read_multiple_files` | Developer, Tester, Critic | Read multiple files in one call |
| `list_files` | Developer, Tester, Critic | Recursively list a directory (returns full paths) |
| `write_file` | Developer, Tester | Write a single file |
| `write_batch_files` | Developer, Tester | Write ALL project files in one API call |
| `run_command` | Developer, Tester | Run `dotnet` CLI commands (allowlisted only) |

**Security:** `RunCommandTool` only permits commands starting with:
`dotnet`, `cat`, `ls`, `find`, `echo` — all others are blocked.

**`write_batch_files`** accepts a list of `{path, content}` objects and writes
all generated files in a single tool call, reducing API iterations from 13+ down to 1–2.

---

## NuGet Packages

The generated `.csproj` includes all required packages automatically:

```xml
<PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.Tools" Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.Design" Version="8.0.0" />
<PackageReference Include="Microsoft.AspNetCore.Mvc.Razor.RuntimeCompilation" Version="8.0.0" />
```

After writing files, the agent runs `dotnet restore` then `dotnet build` to confirm
all packages install and all `using` statements compile correctly.

---

## Testing the Agent Itself

The project has **111 Python unit tests** covering all components:

```bash
# Run the full test suite
uv run pytest tests/ -v

# Run individual test files
uv run pytest tests/test_tools.py -v       # migration tools
uv run pytest tests/test_agents.py -v      # agent creation + LLM config
uv run pytest tests/test_tasks.py -v       # task wiring + context chaining
uv run pytest tests/test_config.py -v      # settings + auto-derive logic
uv run pytest tests/test_rate_limiter.py -v # RPM/TPM sliding window
```

| Test File | What It Covers |
|---|---|
| `test_tools.py` | All 6 tools: read, write, batch-write, list, multi-read, command |
| `test_agents.py` | Agent creation, LLM config (`max_tokens=8192`), tool assignment |
| `test_tasks.py` | Task count (5), context chaining, agent assignment per task |
| `test_config.py` | `.env` loading, auto-derive output path, `MigratedApp` override |
| `test_rate_limiter.py` | RPM throttle, TPM throttle, window expiry, litellm patch |

---

## Project Structure

```
migration_agent/
├── main.py                  # CLI entry point, retry loop, saves MIGRATION_REPORT.md
├── agents.py                # 4 CrewAI agents with tools and LLM config
├── tasks.py                 # 5 tasks: analyze → migrate → test → review → report
├── migration_tools.py       # 6 custom BaseTool subclasses
├── rate_limiter.py          # Sliding-window RPM + TPM limiter, patches litellm
├── config/
│   └── settings.py          # pydantic-settings MigrationConfig, auto-derive logic
├── legacy_sample/           # Sample legacy Web Forms project (LegacyInventory)
├── output/                  # Generated .NET Core 8 projects (git-ignored)
├── tests/                   # 111 pytest unit tests
├── pyproject.toml           # PEP 621 project definition, dependency pins
└── uv.lock                  # Deterministic lock file (3182 lines)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Package manager | uv 0.9+ (Astral) — replaces pip + venv + pip-tools |
| AI framework | CrewAI 1.9.3 |
| LLM | OpenAI GPT-4o via LiteLLM |
| Config | pydantic-settings (PEP 681) |
| Testing | pytest 8+ |
| Target platform | .NET 8 SDK (`dotnet` CLI) |

---

## What Needs Manual Review After Migration

The Manager's report always flags these for human sign-off:

- **Connection strings** — server/credentials are placeholders; replace before deploying
- **EF Core migrations** — run `dotnet ef migrations add InitialCreate` + `dotnet ef database update`
- **Windows Authentication** — not automatically migrated; requires manual setup
- **Session state** — converted to `TempData`; verify behaviour is equivalent
- **Third-party packages** — items from `packages.config` with no .NET Core equivalent
- **Custom HTTP modules** — removed during migration; check if their logic is still needed
