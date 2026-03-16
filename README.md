# MigrationAgenticCrew.NET

An **Agentic AI system** that autonomously migrates legacy **ASP.NET Web Forms** projects
to **.NET Core 8 MVC** — end-to-end, without human intervention during the run.

Built with **CrewAI 1.9.3**, **OpenAI GPT-4o**, **Python 3.12**, managed by **uv**.
Ships with a **FastAPI web UI** for real-time visibility into every agent decision.

---

## Architectural Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TWO RUN MODES                                      │
│                                                                             │
│   CLI Mode                         Web UI Mode                              │
│   ────────                         ───────────                              │
│   uv run main.py                   uv run uvicorn api.index:app --reload    │
│         │                                    │                              │
│         │                         ┌──────────▼──────────┐                  │
│         │                         │   FastAPI (api/)     │                  │
│         │                         │  GET  /              │ ◄── Browser      │
│         │                         │  POST /api/migrate   │                  │
│         │                         │  GET  /api/stream/id │ ──► SSE stream   │
│         │                         │  GET  /api/status/id │                  │
│         │                         └──────────┬──────────┘                  │
│         │                                    │ ThreadPoolExecutor           │
│         └────────────────────────────────────┘                              │
│                                   │                                         │
│                    ┌──────────────▼──────────────────────┐                 │
│                    │         main.py  (core runner)       │                 │
│                    │  load_config() → validate() →        │                 │
│                    │  setup_limiter() → crew.kickoff()    │                 │
│                    └──────────────┬──────────────────────┘                 │
│                                   │                                         │
│                    ┌──────────────▼──────────────────────┐                 │
│                    │       Rate Limiter (rate_limiter.py) │                 │
│                    │  Patches litellm.completion globally │                 │
│                    │  Sliding-window RPM + TPM throttle   │                 │
│                    └──────────────┬──────────────────────┘                 │
│                                   │                                         │
│          ┌────────────────────────▼────────────────────────────────┐       │
│          │                 CrewAI Sequential Pipeline               │       │
│          │                                                          │       │
│          │  Task 1: Analyze        Task 2: Migrate                  │       │
│          │  ┌──────────────┐       ┌──────────────┐                │       │
│          │  │  Developer   │──────►│  Developer   │                │       │
│          │  │   Agent      │       │   Agent      │                │       │
│          │  │ list_files   │       │ write_batch  │                │       │
│          │  │ read_multi   │       │ run_command  │                │       │
│          │  └──────────────┘       └──────┬───────┘                │       │
│          │                                │                         │       │
│          │  Task 3: Test           Task 4: Review                   │       │
│          │  ┌──────────────┐       ┌──────────────┐                │       │
│          │  │   Tester     │──────►│    Critic    │                │       │
│          │  │   Agent      │       │    Agent     │                │       │
│          │  │ write_batch  │       │  read_file   │                │       │
│          │  │ run_command  │       │  read_multi  │                │       │
│          │  └──────────────┘       └──────┬───────┘                │       │
│          │                                │                         │       │
│          │  Task 5: Report                │                         │       │
│          │  ┌──────────────┐             │                         │       │
│          │  │   Manager    │◄────────────┘                         │       │
│          │  │   Agent      │  aggregates all task outputs           │       │
│          │  │  (no tools)  │  verdict: COMPLETE / INCOMPLETE        │       │
│          │  └──────────────┘                                        │       │
│          └────────────────────────────────────────────────────────┬┘       │
│                                                                    │        │
│          ┌─────────────────────────────────────────────────────────▼──┐    │
│          │                    OpenAI GPT-4o API                        │    │
│          │          (via LiteLLM — rate-limited per sliding window)    │    │
│          └────────────────────────────────────────────────────────────┘    │
│                                                                             │
│          File I/O                                                           │
│          ┌──────────────────┐      ┌──────────────────────────────────┐    │
│          │  legacy_sample/  │      │  output/{ProjectName}/           │    │
│          │  LegacyInventory │ read │  ├── {ProjectName}.csproj        │    │
│          │  ├── *.aspx      │─────►│  ├── Program.cs                  │    │
│          │  ├── *.aspx.cs   │      │  ├── appsettings.json            │    │
│          │  ├── Web.config  │      │  ├── Data/AppDbContext.cs        │    │
│          │  └── packages... │      │  ├── Models/*.cs                  │    │
│          └──────────────────┘      │  ├── Controllers/*.cs             │    │
│                                    │  ├── Views/**/*.cshtml            │    │
│                                    │  ├── {ProjectName}.Tests/         │    │
│                                    │  └── MIGRATION_REPORT.md          │    │
│                                    └──────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## SSE Log Streaming (Web UI)

```
CrewAI thread                     Queue              SSE stream         Browser
──────────────────────────────────────────────────────────────────────────────
sys.stdout.write("Thought: ...")  → queue.put() → /api/stream/{id} → EventSource
logging.emit("Working Agent...")  → queue.put() → SSE event        → processLogLine()
print("dotnet build SUCCESS")     → queue.put() → SSE event        → terminal UI
                                                                          │
                                                                 ┌────────▼────────┐
                                                                 │  Pipeline steps  │
                                                                 │  🔍 → ⚙️ → 🧪  │
                                                                 │  → 🔎 → 📋      │
                                                                 │  (light up live) │
                                                                 │                  │
                                                                 │  Color-coded log  │
                                                                 │  Thought:  blue   │
                                                                 │  Action:   purple │
                                                                 │  Observe:  green  │
                                                                 │  Answer:   yellow │
                                                                 │  ERROR:    red    │
                                                                 └──────────────────┘
```

---

## Retry Loop

```
┌─────────────────────────────────────────────────────────────┐
│  run_with_retry()   (up to MAX_RETRY_LOOPS attempts)        │
│                                                             │
│  Attempt 1 ──► crew.kickoff() ──► Manager verdict          │
│                                        │                    │
│                              COMPLETE ─┤─► save report ✅  │
│                                        │                    │
│                           INCOMPLETE ──┤─► wait 60s        │
│                                        │   (window reset)  │
│  Attempt 2 ──► crew.kickoff() ◄────────┘                   │
│    (Developer gets fix list from previous report)           │
└─────────────────────────────────────────────────────────────┘
```

---

## Agent + Tool Matrix

```
                     read_file  read_multi  list_files  write_file  write_batch  run_command
                     ─────────  ──────────  ──────────  ──────────  ───────────  ───────────
Developer Agent         ✓           ✓           ✓           ✓            ✓             ✓
Tester Agent            ✓           ✓           ✓           ✓            ✓             ✓
Critic Agent            ✓           ✓           ✓           ✗            ✗             ✗
Manager Agent           ✗           ✗           ✗           ✗            ✗             ✗
```

---

## How It Works

Four specialized AI agents collaborate in a five-task sequential pipeline.
Each agent receives the outputs of all previous tasks as context.

| Task | Agent | What It Does |
|---|---|---|
| **1. Analyze** | Developer | Reads every `.aspx`, `.aspx.cs`, `Web.config`, `Global.asax` in the legacy project. Discovers all pages, entities, data-access patterns, and navigation structure dynamically — no hardcoded assumptions. |
| **2. Migrate** | Developer | Generates a complete .NET Core 8 MVC solution: controllers, Razor views, EF Core DbContext, models, `Program.cs`, `appsettings.json`, `.csproj`, `.sln`. Runs `dotnet restore` + `dotnet build` to verify. |
| **3. Test** | Tester | Writes an xUnit test project with 7 tests per discovered controller. Runs `dotnet test`. Reports each test pass/fail individually. |
| **4. Review** | Critic | Scores the migration 0–100 against a rubric: architecture, EF Core correctness, Razor syntax, test coverage, build status. Produces a detailed gap list. |
| **5. Report** | Manager | Aggregates all four task outputs. Issues `STATUS: COMPLETE` or `STATUS: INCOMPLETE` with a specific fix list for the next retry. |

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
  {ProjectName}.sln                 ← .NET solution file (main + test projects)
  {ProjectName}/
    {ProjectName}.csproj            ← net8.0, all EF Core + MVC packages
    appsettings.json                ← connection string from Web.config
    Program.cs                      ← middleware pipeline, EF Core DI
    Data/
      AppDbContext.cs               ← EF Core DbContext, one DbSet<T> per entity
    Models/
      {Entity}.cs                   ← one model per entity, data annotations
    Controllers/
      {Entity}sController.cs        ← full async CRUD, injected DbContext
    Views/
      {Entity}s/
        Index.cshtml                ← Bootstrap 5 table
        Create.cshtml               ← form with tag helpers
        Edit.cshtml                 ← pre-filled form
        Delete.cshtml               ← confirm page
      Shared/
        _Layout.cshtml              ← Bootstrap 5 nav layout
      _ViewImports.cshtml
      _ViewStart.cshtml
  {ProjectName}.Tests/
    {ProjectName}.Tests.csproj      ← xUnit + EF Core InMemory
    CrudControllerTests.cs          ← 7 tests per controller (in-memory DB)
  MIGRATION_REPORT.md               ← final Manager verdict + fix list
```

The output folder name is **auto-derived** from the legacy `.sln` filename.
`LegacyInventory.sln` → `./output/LegacyInventory`. No configuration needed.

---

## Requirements

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/) package manager
- .NET 8 SDK (`dotnet` CLI used by agents to build + test)
- OpenAI API key (GPT-4o recommended)

---

## Setup

```bash
# 1. Install uv
# Windows (PowerShell):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install all dependencies (Python + FastAPI + uvicorn)
uv sync

# 3. Configure environment
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# Edit .env and set OPENAI_API_KEY
```

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | Your OpenAI API key |
| `LEGACY_PROJECT_PATH` | `./legacy_sample` | Path to the legacy ASP.NET Web Forms project |
| `OUTPUT_PROJECT_PATH` | *(auto-derived)* | Leave unset — auto-derived from `.sln` name |
| `LLM_MODEL` | `gpt-4o` | LiteLLM model string |
| `LLM_RPM` | `60` | Max requests per minute (match your OpenAI tier) |
| `LLM_TPM` | `30000` | Max tokens per minute (Tier 1 = 30K, Tier 2 = 450K) |
| `LLM_MAX_TOKENS` | `8192` | Max tokens per LLM response |
| `USE_MEMORY` | `false` | Enable CrewAI shared memory (requires embedding API calls) |
| `MAX_RETRY_LOOPS` | `3` | Max retry attempts if Manager returns INCOMPLETE |
| `VERBOSE` | `true` | Show agent reasoning in console |

---

## Usage

### CLI Mode
```bash
# Run with defaults from .env
uv run main.py

# Override the legacy project path
uv run main.py --legacy ./my_legacy_project

# Override both paths
uv run main.py --legacy ./my_legacy_project --output ./output/MyApp

# Single run — skip the retry loop
uv run main.py --no-retry
```

### Web UI Mode (local)
```bash
uv run uvicorn api.index:app --reload
# → open http://localhost:8000
```

The web UI provides:
- Real-time **log streaming** (SSE) — every agent Thought / Action / Observation appears live
- **Pipeline progress bar** — steps light up as each agent activates
- **Color-coded terminal** — Thought (blue), Action (purple), Observation (green), Error (red)
- **Agent status bar** — shows current agent name + elapsed time
- **Migration Report** displayed inline when the job completes

### Web API Endpoints
| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Landing page + run UI |
| `POST` | `/api/migrate` | Start a job → returns `job_id` (202) |
| `GET` | `/api/stream/{job_id}` | **SSE stream** — real-time log lines |
| `GET` | `/api/status/{job_id}` | Poll status + final result |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/health` | Health check |
| `GET` | `/api/info` | Project metadata (JSON) |

---

## Rate Limiting

A custom sliding-window rate limiter (`rate_limiter.py`) patches `litellm.completion`
globally so every agent call is automatically throttled before reaching OpenAI:

- Tracks **RPM** and **TPM** in a 62-second sliding window (deque-based, thread-safe)
- Calculates minimum wait time when either limit is approaching
- Prints `[RateLimiter] Pausing X.Xs ...` when throttling kicks in
- Configured via `LLM_RPM` and `LLM_TPM` in `.env`

```
OpenAI gpt-4o Tiers:
  Tier 1 → LLM_RPM=500   LLM_TPM=30000
  Tier 2 → LLM_RPM=5000  LLM_TPM=450000
  Tier 3 → LLM_RPM=5000  LLM_TPM=800000
```

Between retries the system also waits **60 seconds** to let the OpenAI
rate-limit window fully reset before the next attempt.

---

## Custom Tools (`migration_tools.py`)

Six purpose-built `BaseTool` subclasses power the agents:

| Tool | Description |
|---|---|
| `read_file` | Read a single file by exact path |
| `read_multiple_files` | Read multiple files in one call (list of paths) |
| `list_files` | Recursively list a directory — returns **full paths** (never relative) |
| `write_file` | Write a single file |
| `write_batch_files` | Write ALL generated project files in one API call |
| `run_command` | Run `dotnet` CLI commands (allowlisted prefixes only) |

**`write_batch_files`** reduces API calls from 13+ per project down to 1–2 by writing
every file in a single tool invocation. Accepts JSON string, list, or keyword arg
— handles all formats CrewAI 1.9.3 may pass.

**`list_files`** returns full absolute-style paths (via `Path.as_posix()`) so agents
never need to guess or construct paths.

**`RunCommandTool`** allows only: `dotnet`, `cat`, `ls`, `find`, `echo`.

---

## NuGet Packages

The generated `.csproj` includes all required packages automatically:

```xml
<PackageReference Include="Microsoft.EntityFrameworkCore"                  Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer"        Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.Tools"            Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.Design"           Version="8.0.0" />
<PackageReference Include="Microsoft.EntityFrameworkCore.InMemory"        Version="8.0.0" />
<PackageReference Include="Microsoft.AspNetCore.Mvc.Razor.RuntimeCompilation" Version="8.0.0" />
```

After writing all files the agent runs `dotnet restore` → `dotnet build` to confirm
all packages install and every `using` statement compiles correctly.

---

## Testing the Agent Itself

**111 Python unit tests** cover every component:

```bash
# Full suite
uv run pytest tests/ -v

# Individual files
uv run pytest tests/test_tools.py -v         # all 6 tools
uv run pytest tests/test_agents.py -v        # agent creation, LLM config, tool assignment
uv run pytest tests/test_tasks.py -v         # task count, context chaining, agent assignment
uv run pytest tests/test_config.py -v        # .env loading, auto-derive, MigratedApp override
uv run pytest tests/test_rate_limiter.py -v  # RPM/TPM sliding window, litellm patch
```

| Test File | What It Covers |
|---|---|
| `test_tools.py` | All 6 tools: read, write, batch-write, list, multi-read, run_command |
| `test_agents.py` | Agent creation, `max_tokens=8192`, tool assignment per agent |
| `test_tasks.py` | Task count (5), context chaining, agent assigned to each task |
| `test_config.py` | `.env` loading, auto-derive output path, `MigratedApp` always overridden |
| `test_rate_limiter.py` | RPM throttle, TPM throttle, window expiry, litellm monkey-patch |

---

## Project Structure

```
migration_agent/
├── api/
│   └── index.py             # FastAPI app — web UI, SSE streaming, job queue
├── config/
│   └── settings.py          # pydantic-settings MigrationConfig, auto-derive logic
├── tests/
│   ├── test_agents.py
│   ├── test_config.py
│   ├── test_rate_limiter.py
│   ├── test_tasks.py
│   └── test_tools.py
├── legacy_sample/           # Sample legacy Web Forms project (LegacyInventory)
├── output/                  # Generated .NET Core 8 projects (git-ignored)
├── main.py                  # CLI entry point, retry loop, saves MIGRATION_REPORT.md
├── agents.py                # 4 CrewAI agents with tools and LLM config
├── tasks.py                 # 5 tasks: analyze → migrate → test → review → report
├── migration_tools.py       # 6 custom BaseTool subclasses
├── rate_limiter.py          # Sliding-window RPM + TPM limiter, patches litellm
├── vercel.json              # Vercel routing config (web UI showcase)
├── pyproject.toml           # PEP 621 project definition + dependency pins
└── uv.lock                  # Deterministic lock file
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Package manager | uv (Astral) — replaces pip + venv + pip-tools |
| AI framework | CrewAI 1.9.3 (sequential process) |
| LLM | OpenAI GPT-4o via LiteLLM |
| Web framework | FastAPI + uvicorn |
| Real-time streaming | Server-Sent Events (SSE) |
| Config | pydantic-settings |
| Testing | pytest 8+ (111 tests) |
| Target platform | .NET 8 SDK (`dotnet` CLI) |

---

## Deploying the Web UI

### Local
```bash
uv run uvicorn api.index:app --reload --port 8000
```

### Vercel (showcase only)
```bash
vercel --prod
```
> **Note:** Vercel functions time out at 10–60 seconds. The agent pipeline runs 5–20 minutes.
> Vercel is suitable as a **demo/showcase** page. To run the full pipeline, deploy on
> **Railway**, **Render**, or **Fly.io** where long-running processes are supported.

### Railway / Render / Fly.io (full pipeline)
Set these environment variables in your hosting dashboard:
```
OPENAI_API_KEY=sk-...
LEGACY_PROJECT_PATH=./legacy_sample
LLM_MODEL=gpt-4o
LLM_RPM=60
LLM_TPM=30000
```
Then run:
```bash
uvicorn api.index:app --host 0.0.0.0 --port $PORT
```

---

## What Needs Manual Review After Migration

The Manager's report always flags these for human sign-off:

- **Connection strings** — server/credentials are placeholders; replace before deploying
- **EF Core migrations** — run `dotnet ef migrations add InitialCreate` + `dotnet ef database update`
- **Windows Authentication** — not automatically migrated; requires manual setup
- **Session state** — converted to `TempData`; verify behaviour is equivalent
- **Third-party packages** — items from `packages.config` with no .NET Core equivalent
- **Custom HTTP modules** — removed during migration; check if logic is still needed
