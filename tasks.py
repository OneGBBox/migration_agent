"""
tasks.py

Five CrewAI Tasks — one per pipeline stage.

Two usage modes:
  1. Mini-crew (main.py new architecture): call build_*_task() functions directly.
     Each runs in its own Crew with a fresh context window.
     Prior task summaries are injected into the description string.

  2. Sequential crew (backward-compat / tests): call create_tasks().
     Returns all 5 Task objects; context chaining done at construction.

No entity names, controller names, or file names are hardcoded.
The agent discovers everything from the actual legacy project at runtime.
"""

from pathlib import Path

from crewai import Task


# ── Internal helper ───────────────────────────────────────────────────────────

def _solution_paths(output_path: str) -> tuple[str, str, str]:
    """
    Derive (project_name, solution_dir, solution_path) from the output path.

    str(Path("./output/X").parent) strips "./" → "output", so we use
    as_posix() and re-add "./" when the parent is a relative path.
    """
    _p = Path(output_path)
    project_name = _p.name
    _parent = _p.parent
    solution_dir = _parent.as_posix()
    if not solution_dir.startswith("/") and not solution_dir.startswith("./"):
        solution_dir = "./" + solution_dir
    solution_path = f"{solution_dir}/{project_name}.sln"
    return project_name, solution_dir, solution_path


# ── Task 1 — Analyze ─────────────────────────────────────────────────────────

def build_analyze_task(
    agent,
    legacy_path: str,
    output_file: str | None = None,
    context: list | None = None,
) -> Task:
    """Discover the legacy Web Forms project structure dynamically."""
    kwargs = {}
    if output_file:
        kwargs["output_file"] = output_file
    if context:
        kwargs["context"] = context

    return Task(
        description=f"""
Analyze the legacy ASP.NET Web Forms project located at: {legacy_path}

Do NOT assume any folder structure or file names — discover everything by reading
the actual files in the project directory.

═══ STEP 1: Discover all files ═══
Call list_files on "{legacy_path}".
list_files returns FULL paths (e.g. "legacy_sample/MyApp/Web.config").
NEVER construct or guess file paths — use ONLY the exact full paths returned by list_files.

═══ STEP 2: Read configuration files ═══
From the list_files output, identify and read (using read_multiple_files):
  - Web.config
  - *.csproj
  - packages.config  (if present)
  - Global.asax      (if present)
  - Global.asax.cs   (if present)
Only include paths that actually appear in the list_files output.

═══ STEP 3: Read all data/model classes ═══
From the list_files output, find all .cs files that likely define entities or models
(look in folders named Models/, Data/, Entities/, Domain/, or any .cs file that
defines a class with properties). Read them all at once using read_multiple_files.

═══ STEP 4: Read all Web Forms pages ═══
From the list_files output, find all .aspx and .aspx.cs files.
Read them all at once using read_multiple_files.

═══ STEP 5: Read any remaining .cs files ═══
Read any other .cs files not yet covered (helpers, utilities, repositories, etc.)
that may contain business logic or data access code.

═══ PRODUCE A MIGRATION ANALYSIS REPORT ═══

## 1. PROJECT SUMMARY
Describe what the application does.
List every feature and entity/concept it manages.
State the project type (Web Forms, and whether it also has any MVC or API controllers).

## 2. DATABASE AND CONNECTION
- Connection string found in Web.config (database name, server if present)
- Database access pattern: EF6 DbContext, raw ADO.NET (SqlConnection/SqlCommand), or both
- All table names inferred from model class names, SQL strings, or ORM configuration

## 3. ENTITIES
For EACH entity/model class found (or inferred from SQL):
- Class name
- Every property: name, C# type, nullability
- Foreign key relationships to other entities (e.g., Product has CategoryId → Category)
- Validation rules found (required fields, string length limits, numeric ranges, etc.)
- Whether it uses EF6 navigation properties or raw SQL joins

## 4. WEB FORMS PAGES
For EACH .aspx page found:
- File name (e.g., Products.aspx)
- Code-behind class name (e.g., Products)
- Every Page_Load, button click, or other event handler:
    * Handler name
    * HTTP verb mapping: Page_Load with !IsPostBack → GET, button click events → POST
    * What entity it reads or writes
    * Any query string parameters used (e.g., Request.QueryString["id"])
    * Any Response.Redirect targets
- Proposed .NET Core 8 controller name (pluralized entity + "Controller",
  e.g., Products.aspx → ProductsController)
- Proposed controller actions derived from the handlers above

## 5. MIGRATION MAPPING TABLE
For each legacy pattern found, state the exact .NET Core 8 equivalent:

  LEGACY PATTERN                        → .NET CORE 8 EQUIVALENT
  ─────────────────────────────────────────────────────────────
  System.Web.UI.Page (code-behind)      → Controller inheriting Controller
  Page_Load(!IsPostBack)                → [HttpGet] Index() / Details(int id)
  btnSave_Click / btnSubmit_Click       → [HttpPost] Create(Model m) + [ValidateAntiForgeryToken]
  btnUpdate_Click / btnEdit_Click       → [HttpPost] Edit(int id, Model m) + [ValidateAntiForgeryToken]
  btnDelete_Click                       → [HttpPost] DeleteConfirmed(int id) + [ValidateAntiForgeryToken]
  Response.Redirect("Page.aspx")       → return RedirectToAction("Index")
  Request.QueryString["id"]            → int id route/query parameter
  SqlConnection + SqlCommand (ADO.NET) → EF Core DbContext + async LINQ
  EF6 DbContext                         → EF Core 8 DbContext
  System.Configuration.ConfigurationManager → IConfiguration + appsettings.json
  Web.config <connectionStrings>        → appsettings.json ConnectionStrings section
  Global.asax Application_Start        → Program.cs builder/app configuration
  Session["key"]                        → TempData["key"] or injected service
  Server controls (TextBox, GridView)   → Razor HTML + Bootstrap 5 + tag helpers

## 6. BREAKING CHANGES
List every pattern in the legacy code that does NOT exist in .NET Core 8.
For each: file name, handler/method name, and what must change.
        """,
        expected_output=(
            "A complete Migration Analysis Report with all 6 sections filled in: "
            "PROJECT SUMMARY, DATABASE, ENTITIES, WEB FORMS PAGES, "
            "MIGRATION MAPPING, BREAKING CHANGES. "
            "Every section must reference actual files and code found in the legacy project."
        ),
        agent=agent,
        **kwargs,
    )


# ── Task 2 — Migrate ─────────────────────────────────────────────────────────

def build_migrate_task(
    agent,
    legacy_path: str,
    output_path: str,
    prior_analyze_summary: str = "",
    output_file: str | None = None,
    context: list | None = None,
) -> Task:
    """Generate the complete .NET Core 8 MVC project."""
    project_name, solution_dir, solution_path = _solution_paths(output_path)
    kwargs = {}
    if output_file:
        kwargs["output_file"] = output_file
    if context:
        kwargs["context"] = context

    prior_context = (
        f"\n═══ SUMMARY FROM TASK 1 (ANALYZE) ═══\n{prior_analyze_summary}\n"
        if prior_analyze_summary
        else ""
    )

    return Task(
        description=f"""
Using the Migration Analysis Report from Task 1, generate a complete .NET Core 8 MVC
application that is functionally equivalent to the legacy Web Forms project.
{prior_context}
Output directory: {output_path}
Project name: {project_name}
Solution file: {solution_path}

═══════════════════════════════════════════════════════════════════════
WHAT FILES TO GENERATE
Determine the exact file list from Task 1's report — do NOT hardcode names.
The set of files is driven entirely by what was discovered in the legacy project.
═══════════════════════════════════════════════════════════════════════

── {output_path}/{project_name}.csproj ──
ALWAYS use exactly this template (same for every project):
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0" />
    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="8.0.0" />
    <PackageReference Include="Microsoft.EntityFrameworkCore.Tools" Version="8.0.0" />
    <PackageReference Include="Microsoft.EntityFrameworkCore.Design" Version="8.0.0" />
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Razor.RuntimeCompilation" Version="8.0.0" />
  </ItemGroup>
</Project>

── {output_path}/appsettings.json ──
Copy the connection string from Web.config. Replace server/credentials with placeholders:
  Server=localhost;Database=<DbName>;Trusted_Connection=True;TrustServerCertificate=True;
Use the exact database name from the legacy connection string.
Structure:
{{
  "ConnectionStrings": {{
    "DefaultConnection": "Server=localhost;Database=<DbName>;Trusted_Connection=True;TrustServerCertificate=True;"
  }},
  "Logging": {{ "LogLevel": {{ "Default": "Information", "Microsoft.AspNetCore": "Warning" }} }},
  "AllowedHosts": "*"
}}

── {output_path}/Program.cs ──
Required using statements:
  using Microsoft.EntityFrameworkCore;
  using {project_name}.Data;
Standard middleware pipeline (always in this order):
  var builder = WebApplication.CreateBuilder(args);
  builder.Services.AddControllersWithViews();
  builder.Services.AddDbContext<AppDbContext>(options =>
      options.UseSqlServer(builder.Configuration.GetConnectionString("DefaultConnection")));
  var app = builder.Build();
  app.UseHttpsRedirection();
  app.UseStaticFiles();
  app.UseRouting();
  app.UseAuthorization();
  app.MapControllerRoute(name: "default", pattern: "{{controller=<PrimaryController>}}/{{action=Index}}/{{id?}}");
  app.Run();
Replace <PrimaryController> with the name of the most important controller (the first
one listed in Task 1's PAGES section, without the "Controller" suffix).

── {output_path}/Data/AppDbContext.cs ──
Required using statements:
  using Microsoft.EntityFrameworkCore;
  using {project_name}.Models;
Generate:
  public class AppDbContext : DbContext
  {{
      public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) {{}}
      // One DbSet<T> for EACH entity discovered in Task 1
      public DbSet<EntityName> EntityNames {{ get; set; }}
      // ... repeat for every entity
      protected override void OnModelCreating(ModelBuilder modelBuilder)
      {{
          // For decimal money/price properties: configure precision
          // e.g.: modelBuilder.Entity<Product>().Property(p => p.Price).HasColumnType("decimal(18,2)");
      }}
  }}

── {output_path}/Models/{{EntityName}}.cs  [one file per entity] ──
Required using statements:
  using System.ComponentModel.DataAnnotations;
  using System.ComponentModel.DataAnnotations.Schema;  // only if needed for [ForeignKey] etc.
For EACH entity discovered in Task 1:
  - public int Id {{ get; set; }}  (primary key)
  - Every property with same C# type as legacy
  - [Required] on every non-nullable string property
  - [StringLength(n)] if a max length was found or can be inferred
  - [Range(min, max)] on numeric properties where a range makes sense
  - Foreign key integer property (e.g., public int CategoryId {{ get; set; }})
  - Navigation property (e.g., public Category? Category {{ get; set; }})
  - Navigation collection on parent side (e.g., public ICollection<Child> Children {{ get; set; }} = new List<Child>();)

── {output_path}/Controllers/{{EntityName}}sController.cs  [one per entity] ──
Required using statements:
  using Microsoft.AspNetCore.Mvc;
  using Microsoft.EntityFrameworkCore;
  using {project_name}.Data;
  using {project_name}.Models;
  // Also add: using Microsoft.AspNetCore.Mvc.Rendering;  if entity has FK dropdowns

Derive controller name from Task 1's MIGRATION MAPPING TABLE.
For each legacy .aspx page, generate a controller with these actions:

  GET  Index()            → return all records: await _context.Entities.ToListAsync()
  GET  Details(int id)    → FindAsync(id), return NotFound() if null
  GET  Create()           → populate ViewBag dropdown if FK; return View()
  POST Create(Model m)    → [ValidateAntiForgeryToken], ModelState.IsValid check,
                            _context.Add(m), await _context.SaveChangesAsync(),
                            return RedirectToAction(nameof(Index))
  GET  Edit(int id)       → FindAsync(id), NotFound() if null, populate ViewBag if FK
  POST Edit(int id, Model m) → [ValidateAntiForgeryToken], ModelState.IsValid check,
                            _context.Update(m), await _context.SaveChangesAsync(),
                            return RedirectToAction(nameof(Index))
  GET  Delete(int id)     → FindAsync(id) or FirstOrDefaultAsync with Include for FK display
  POST DeleteConfirmed(int id) → [ValidateAntiForgeryToken], FindAsync(id), Remove,
                                 SaveChangesAsync, return RedirectToAction(nameof(Index))

All DB calls MUST be async. Inject AppDbContext via constructor only.

── {output_path}/Views/{{EntityName}}s/  [four views per entity] ──
  Index.cshtml:    Bootstrap 5 table, one column per property, action links Edit|Details|Delete
  Create.cshtml:   <form asp-action="Create">, <input asp-for="...">, <select asp-for="..."> for FK
  Edit.cshtml:     Same as Create with <input type="hidden" asp-for="Id">
  Delete.cshtml:   Display properties, confirm form with asp-action="DeleteConfirmed"

── {output_path}/Views/Shared/_Layout.cshtml ──
Bootstrap 5 CDN. Nav bar: app title left, one nav link per controller right.

── {output_path}/Views/_ViewImports.cshtml ──
@using {project_name}
@using {project_name}.Models
@addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers

── {output_path}/Views/_ViewStart.cshtml ──
@{{ Layout = "_Layout"; }}

═══════════════════════════════════════════════════════════════════════
HARD RULES — zero exceptions
═══════════════════════════════════════════════════════════════════════
- ZERO references to: System.Web, System.Data.SqlClient, System.Configuration,
  System.Web.UI, HttpContext.Current, IsPostBack, Page_Load, Response.Redirect,
  Request.QueryString, Web.config
- All DB access async — no synchronous SaveChanges/Find/ToList
- DbContext injected via constructor only
- All POST actions must have [ValidateAntiForgeryToken]
- All POST actions must return RedirectToAction on success
- Views must use tag helpers (asp-*), NOT Html.BeginForm / Html.TextBoxFor / Html.ActionLink

═══════════════════════════════════════════════════════════════════════
HOW TO WRITE FILES
═══════════════════════════════════════════════════════════════════════
Use write_batch_files to write ALL generated files in ONE single call.

═══════════════════════════════════════════════════════════════════════
AFTER WRITING FILES — run in this order
═══════════════════════════════════════════════════════════════════════
Step A — Restore NuGet packages:
  run_command: dotnet restore "{output_path}/{project_name}.csproj"

Step B — Build to confirm all using statements compile:
  run_command: dotnet build "{output_path}/{project_name}.csproj"
  Report exact compiler error with file name and line number if FAIL.

Step C — Create solution file and add main project:
  run_command: dotnet new sln --name "{project_name}" --output "{solution_dir}" --force
  run_command: dotnet sln "{solution_path}" add "{output_path}/{project_name}.csproj"
        """,
        expected_output=(
            "Confirmation that every file was written. "
            "List each file path with a one-line description. "
            "NuGet restore result: SUCCESS or FAIL. "
            "Build result: SUCCESS or FAIL (with compiler error if failed). "
            f"Solution file created at: {solution_path}"
        ),
        agent=agent,
        **kwargs,
    )


# ── Task 3 — Test ────────────────────────────────────────────────────────────

def build_test_task(
    agent,
    output_path: str,
    prior_migrate_summary: str = "",
    output_file: str | None = None,
    context: list | None = None,
) -> Task:
    """Write and run xUnit tests for all generated controllers."""
    project_name, solution_dir, solution_path = _solution_paths(output_path)
    kwargs = {}
    if output_file:
        kwargs["output_file"] = output_file
    if context:
        kwargs["context"] = context

    prior_context = (
        f"\n═══ SUMMARY FROM TASK 2 (MIGRATE) ═══\n{prior_migrate_summary}\n"
        if prior_migrate_summary
        else ""
    )

    return Task(
        description=f"""
Write xUnit tests for the migrated .NET Core 8 project at: {output_path}
Then run them and report results.
{prior_context}
═══ STEP 1: Discover what was generated ═══
Call list_files on "{output_path}" to get every file path.
Call read_multiple_files on all Controller files and Model files found.
Use ONLY exact paths from list_files.

═══ STEP 2: Write test files (one write_batch_files call) ═══

File 1 — {output_path}.Tests/{project_name}.Tests.csproj:
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <IsPackable>false</IsPackable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />
    <PackageReference Include="xunit" Version="2.6.1" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.5.3">
      <IncludeAssets>runtime; build; native; contentfiles; analyzers; buildtransitive</IncludeAssets>
      <PrivateAssets>all</PrivateAssets>
    </PackageReference>
    <PackageReference Include="Microsoft.EntityFrameworkCore.InMemory" Version="8.0.0" />
    <PackageReference Include="coverlet.collector" Version="6.0.0">
      <IncludeAssets>runtime; build; native; contentfiles; analyzers; buildtransitive</IncludeAssets>
      <PrivateAssets>all</PrivateAssets>
    </PackageReference>
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="../{project_name}/{project_name}.csproj" />
  </ItemGroup>
</Project>

File 2 — {output_path}.Tests/CrudControllerTests.cs:
Required using statements at top:
  using Microsoft.EntityFrameworkCore;
  using Microsoft.AspNetCore.Mvc;
  using {project_name}.Controllers;
  using {project_name}.Data;
  using {project_name}.Models;
  using Xunit;

Helper to create an in-memory DbContext (use for all tests):
  private static AppDbContext CreateContext() =>
      new AppDbContext(new DbContextOptionsBuilder<AppDbContext>()
          .UseInMemoryDatabase(Guid.NewGuid().ToString())
          .Options);

For EACH controller discovered in Step 1, write these 7 tests:
  [Fact] {{EntityName}}_Index_Returns_All_Items()
  [Fact] {{EntityName}}_Create_GET_Returns_ViewResult()
  [Fact] {{EntityName}}_Create_POST_Valid_Saves_And_Redirects()
  [Fact] {{EntityName}}_Create_POST_Invalid_Returns_View()
  [Fact] {{EntityName}}_Edit_GET_Returns_Correct_Item()
  [Fact] {{EntityName}}_Edit_POST_Valid_Updates_And_Redirects()
  [Fact] {{EntityName}}_Delete_POST_Removes_And_Redirects()

For any entity with a required FK, seed the parent before testing the child.

═══ STEP 3: Install packages and add test project to solution ═══
Step 3a: run_command: dotnet restore "{output_path}.Tests/{project_name}.Tests.csproj"
Step 3b: run_command: dotnet sln "{solution_path}" add "{output_path}.Tests/{project_name}.Tests.csproj"
Step 3c: run_command: dotnet build "{output_path}.Tests/{project_name}.Tests.csproj"

═══ STEP 4: Run tests ═══
  run_command: dotnet test "{output_path}.Tests/{project_name}.Tests.csproj" --logger "console;verbosity=detailed"

═══ STEP 5: Report ═══
For each test: PASS or FAIL. For FAIL: exact exception message and failing line.
Summary: "X / Y tests passed"
Final recommendation: READY FOR REVIEW or NEEDS FIXES
        """,
        expected_output=(
            "Test report listing each test name with PASS or FAIL. "
            "For FAIL: exact exception message and line that failed. "
            "Summary line: 'X/Y tests passed'. "
            "Final line: READY FOR REVIEW or NEEDS FIXES "
            "(if NEEDS FIXES: bullet list of what the Developer must change)."
        ),
        agent=agent,
        **kwargs,
    )


# ── Task 4 — Review ──────────────────────────────────────────────────────────

def build_review_task(
    agent,
    output_path: str,
    output_file: str | None = None,
    context: list | None = None,
) -> Task:
    """Score the migrated code 0–100 against a quality rubric."""
    kwargs = {}
    if output_file:
        kwargs["output_file"] = output_file
    if context:
        kwargs["context"] = context

    return Task(
        description=f"""
Review the entire migrated .NET Core 8 project at: {output_path}

Use list_files to find all files. Read ALL .cs and .cshtml files using read_multiple_files.

Score checklist (start at 100, deduct for each violation):

Program.cs — HIGH (-10 each):
  - Missing AddControllersWithViews()
  - Missing AddDbContext<AppDbContext>()
  - UseAuthorization() called before UseRouting()
  - No default MapControllerRoute

Controllers — HIGH (-10 each):
  - Any System.Web or System.Data.SqlClient using found
  - HttpContext.Current used anywhere
  - DbContext instantiated with new (not injected via constructor)
  - Synchronous DB calls (SaveChanges, Find, ToList without Async suffix)
  - Missing [ValidateAntiForgeryToken] on any POST action
  - POST action does not return RedirectToAction on success

Models — MEDIUM (-5 each):
  - Non-nullable string property missing [Required]
  - No data annotations at all on any model
  - Any EF6 / System.Data.Entity reference

Views — MEDIUM (-5 each):
  - Html.BeginForm() found (should be <form asp-action="">)
  - Html.EditorFor() or Html.TextBoxFor() found (should be <input asp-for="">)
  - Html.ActionLink() found (should be <a asp-action="">)
  - Missing @addTagHelper directive in _ViewImports.cshtml

appsettings.json — HIGH (-10 each):
  - Connection string section missing entirely
  - Real password or secret hardcoded (not a placeholder)

.csproj — MEDIUM (-5 each):
  - Not targeting net8.0
  - Missing Microsoft.EntityFrameworkCore.SqlServer reference
  - Missing Microsoft.EntityFrameworkCore.Tools reference

LOW (-2 each):
  - Inconsistent async/await usage
  - Missing null checks on nullable navigation properties in views
  - Controller action not returning IActionResult

For each issue: report the file name and the specific line/pattern that is wrong.
        """,
        expected_output=(
            "Structured code review: "
            "SCORE: X/100. "
            "HIGH SEVERITY ISSUES (file + description). "
            "MEDIUM SEVERITY ISSUES (file + description). "
            "LOW SEVERITY ISSUES (file + description). "
            "VERDICT: APPROVED (score >= 80) or NEEDS REVISION (score < 80). "
            "If NEEDS REVISION: ordered fix list for the Developer."
        ),
        agent=agent,
        **kwargs,
    )


# ── Task 5 — Report ──────────────────────────────────────────────────────────

def build_report_task(
    agent,
    output_path: str,
    prior_analyze_summary: str = "",
    prior_migrate_summary: str = "",
    prior_test_summary: str = "",
    prior_review_summary: str = "",
    output_file: str | None = None,
    context: list | None = None,
) -> Task:
    """Synthesize all prior reports and issue COMPLETE / INCOMPLETE verdict."""
    kwargs = {}
    if output_file:
        kwargs["output_file"] = output_file
    if context:
        kwargs["context"] = context

    summaries = ""
    if prior_analyze_summary:
        summaries += f"\n═══ ANALYZE SUMMARY ═══\n{prior_analyze_summary}\n"
    if prior_migrate_summary:
        summaries += f"\n═══ MIGRATE SUMMARY ═══\n{prior_migrate_summary}\n"
    if prior_test_summary:
        summaries += f"\n═══ TEST SUMMARY ═══\n{prior_test_summary}\n"
    if prior_review_summary:
        summaries += f"\n═══ REVIEW SUMMARY ═══\n{prior_review_summary}\n"

    return Task(
        description=f"""
You are the Migration Project Manager. Synthesize the four task reports below
and issue a final verdict.
{summaries}
PASS criteria (ALL must be met for STATUS: COMPLETE):
  ✅ dotnet build in Task 2 succeeded
  ✅ Code Review Score >= 80/100
  ✅ At least 85% of xUnit tests PASS (round down: e.g., 6/7 is 85.7% → pass)
  ✅ Core files present: Program.cs, appsettings.json, AppDbContext.cs, .csproj,
     at least one Controller, at least one Model, at least one set of Views

If ALL criteria met → STATUS: COMPLETE
If ANY criterion not met → STATUS: INCOMPLETE

For INCOMPLETE: list every fix required, ordered by priority (build errors first,
then test failures, then review issues).

Always include the section: WHAT NEEDS MANUAL HUMAN REVIEW
  - Production connection strings (server, credentials)
  - Windows Authentication or Active Directory setup
  - Session state usage converted to TempData (verify behaviour is equivalent)
  - Custom HTTP modules that were removed (check if functionality is needed)
  - EF Core migrations must be run before first deployment
  - Third-party libraries from packages.config with no .NET Core equivalent
        """,
        expected_output=(
            "Final Migration Report with: "
            "STATUS: COMPLETE or INCOMPLETE. "
            "MIGRATION SCORE: X% overall. "
            "ENTITIES MIGRATED: list. "
            "CONTROLLERS GENERATED: list. "
            "BUILD RESULT: PASS or FAIL. "
            "TEST RESULTS: X / Y passed. "
            "ISSUES REMAINING (empty if COMPLETE). "
            "WHAT NEEDS MANUAL HUMAN REVIEW (always present)."
        ),
        agent=agent,
        **kwargs,
    )


# ── Backward-compatible wrapper (used by tests + old sequential crew) ─────────

def create_tasks(agents: dict, legacy_path: str, output_path: str) -> list[Task]:
    """
    Builds and returns all 5 tasks in execution order.
    Used by the sequential Crew approach and by the test suite.
    For the mini-crew approach, call build_*_task() directly.
    """
    task_analyze = build_analyze_task(
        agent=agents["developer"],
        legacy_path=legacy_path,
    )
    task_migrate = build_migrate_task(
        agent=agents["developer"],
        legacy_path=legacy_path,
        output_path=output_path,
        context=[task_analyze],
    )
    task_test = build_test_task(
        agent=agents["tester"],
        output_path=output_path,
        context=[task_migrate],
    )
    task_review = build_review_task(
        agent=agents["critic"],
        output_path=output_path,
        context=[task_migrate],
    )
    task_report = build_report_task(
        agent=agents["manager"],
        output_path=output_path,
        context=[task_analyze, task_migrate, task_test, task_review],
    )

    return [task_analyze, task_migrate, task_test, task_review, task_report]
