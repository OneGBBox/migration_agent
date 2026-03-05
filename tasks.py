"""
tasks/tasks.py

5 CrewAI Tasks wired in execution order.

No API changes needed between 0.28.8 and 1.9.3 for Task itself —
the Task constructor is stable. context=[] chaining still works identically.

Task order:
  1. task_analyze   → Developer reads legacy project
  2. task_migrate   → Developer writes .NET Core 8 project
  3. task_test      → Tester writes + runs xUnit tests
  4. task_review    → Critic scores the code
  5. task_report    → Manager produces final COMPLETE / INCOMPLETE verdict
"""

from crewai import Task


def create_tasks(agents: dict, legacy_path: str, output_path: str) -> list[Task]:
    """
    Builds and returns all 5 tasks in execution order.
    Each task receives prior task outputs via context=[...].
    """

    manager = agents["manager"]
    developer = agents["developer"]
    tester = agents["tester"]
    critic = agents["critic"]

    project_name = output_path.rstrip("/").split("/")[-1]

    # ──────────────────────────────────────────────
    # Task 1 — Analyze Legacy Project
    # Agent: Developer (read-only analysis pass)
    # ──────────────────────────────────────────────
    task_analyze = Task(
        description=f"""
Analyze the legacy ASP.NET MVC project located at: {legacy_path}

Follow these steps exactly:
1. Use list_files on {legacy_path} to see all files
2. Read Web.config — extract connection strings and app settings
3. Read every .cs file in Controllers/ folder
4. Read every .cs file in Models/ folder
5. Read every .cshtml or .aspx file in Views/
6. Read App_Start/RouteConfig.cs or Global.asax for routing config

Produce a Migration Analysis Report with these exact sections:

## PROJECT SUMMARY
What the app does. Number of controllers, models, views.

## DATABASE
Table names found. Connection string format from Web.config.

## CONTROLLERS
List every controller, every action method, its HTTP verb (GET/POST), and its return type.

## VIEWS
List every view file and which model it binds to.

## MIGRATION MAPPING
For each legacy piece, state the exact .NET Core 8 equivalent:
e.g. RouteConfig.cs → Program.cs MapControllerRoute
     Web.config connectionStrings → appsettings.json ConnectionStrings
     Html.BeginForm() → <form asp-action="">
     EF6 DbContext → EF Core DbContext
     System.Web.Mvc → Microsoft.AspNetCore.Mvc

## BREAKING CHANGES
List every pattern found in the legacy code that does NOT exist in .NET Core 8.
Include file name and line context for each one.
        """,
        expected_output="""
A complete Migration Analysis Report with all 6 sections filled in:
PROJECT SUMMARY, DATABASE, CONTROLLERS, VIEWS, MIGRATION MAPPING, BREAKING CHANGES.
Each section must reference actual files and code found in the legacy project.
        """,
        agent=developer,
    )

    # ──────────────────────────────────────────────
    # Task 2 — Generate .NET Core 8 Project
    # Agent: Developer (code generation pass)
    # ──────────────────────────────────────────────
    task_migrate = Task(
        description=f"""
Using the Migration Analysis Report from Task 1, generate a complete .NET Core 8 MVC CRUD application.
Write every file using the write_file tool. Output path: {output_path}

FILES TO GENERATE (write each one):

── {output_path}/{project_name}.csproj ──
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="8.0.0" />
    <PackageReference Include="Microsoft.EntityFrameworkCore.Tools" Version="8.0.0" />
  </ItemGroup>
</Project>

── {output_path}/appsettings.json ──
Use the connection string from the legacy Web.config.
Replace actual credentials with placeholder: Server=localhost;Database=ProductsDb;...

── {output_path}/Program.cs ──
Must include in this order:
  builder.Services.AddControllersWithViews()
  builder.Services.AddDbContext<AppDbContext>(options => options.UseSqlServer(...))
  app.UseHttpsRedirection()
  app.UseStaticFiles()
  app.UseRouting()
  app.UseAuthorization()
  app.MapControllerRoute(name: "default", pattern: "{{controller=Home}}/{{action=Index}}/{{id?}}")

── {output_path}/Data/AppDbContext.cs ──
EF Core 8 DbContext. Constructor takes DbContextOptions<AppDbContext>.
DbSet for each model found in legacy project.

── {output_path}/Models/<ModelName>.cs ──
Clean POCO. Data annotations: [Required], [StringLength], [Range] where appropriate.
No System.Data or System.Data.Entity imports.

── {output_path}/Controllers/<Name>Controller.cs ──
Constructor injection for AppDbContext.
Actions (all async):
  Index()        GET  → return View(await _context.Items.ToListAsync())
  Create()       GET  → return View()
  Create(model)  POST → validate, add, SaveChangesAsync, RedirectToAction("Index")
  Edit(id)       GET  → find by id, return View
  Edit(model)    POST → update entry state, SaveChangesAsync, RedirectToAction("Index")
  Delete(id)     GET  → find by id, return View
  DeleteConfirmed(id) POST → remove, SaveChangesAsync, RedirectToAction("Index")
All POST actions must have [ValidateAntiForgeryToken].
Return NotFound() instead of HttpNotFound(). Return BadRequest() instead of HttpStatusCodeResult.

── {output_path}/Views/<Name>/Index.cshtml ──
Bootstrap 5 table. Use tag helpers: asp-action, asp-controller, asp-route-id.
No Html.ActionLink(). No Html.DisplayFor() — use @item.PropertyName directly.

── {output_path}/Views/<Name>/Create.cshtml ──
<form asp-action="Create"> tag helper. Use <input asp-for=""> and <span asp-validation-for="">.
No Html.BeginForm(). No Html.EditorFor().

── {output_path}/Views/<Name>/Edit.cshtml ──
Same pattern as Create but pre-filled. Include hidden <input asp-for="Id">.

── {output_path}/Views/<Name>/Delete.cshtml ──
Display item details. Confirm delete with <form asp-action="DeleteConfirmed">.

── {output_path}/Views/Shared/_Layout.cshtml ──
Bootstrap 5 CDN. Simple nav bar. @RenderBody(). @await RenderSectionAsync("Scripts", required: false)

── {output_path}/Views/_ViewImports.cshtml ──
@using {project_name}
@using {project_name}.Models
@addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers

── {output_path}/Views/_ViewStart.cshtml ──
@{{ Layout = "_Layout"; }}

RULES — no exceptions:
- Zero System.Web references
- Zero HttpContext.Current
- Zero Web.config (only appsettings.json)
- All DB calls must be async (ToListAsync, FindAsync, SaveChangesAsync)
- DbContext injected via constructor only — never new'd up
        """,
        expected_output="""
Confirmation that every file listed above was written successfully.
Print each file path on its own line.
Briefly state what each file contains.
        """,
        agent=developer,
        context=[task_analyze],
    )

    # ──────────────────────────────────────────────
    # Task 3 — Write and Run xUnit Tests
    # Agent: Tester
    # ──────────────────────────────────────────────
    task_test = Task(
        description=f"""
Write and run xUnit tests for the migrated .NET Core 8 app at: {output_path}

Steps:
1. list_files on {output_path} to understand the project structure
2. Read the Controller and Model files
3. Write the test project file:
   {output_path}.Tests/{project_name}.Tests.csproj

   Must reference:
     xunit (2.6.0+)
     Microsoft.NET.Test.Sdk
     xunit.runner.visualstudio
     Microsoft.EntityFrameworkCore.InMemory
   Must ProjectReference the main app.

4. Write {output_path}.Tests/CrudControllerTests.cs

   Setup pattern (use InMemory so no real SQL Server needed):
     var options = new DbContextOptionsBuilder<AppDbContext>()
         .UseInMemoryDatabase(databaseName: Guid.NewGuid().ToString())
         .Options;
     var context = new AppDbContext(options);
     var controller = new <Name>Controller(context);

   Tests to write (7 total):
     [Fact] Index_Returns_ViewResult_With_All_Items()
     [Fact] Create_GET_Returns_Empty_ViewResult()
     [Fact] Create_POST_Valid_Model_Saves_And_Redirects()
     [Fact] Create_POST_Invalid_Model_Returns_View_With_Errors()
     [Fact] Edit_GET_Returns_ViewResult_With_Correct_Item()
     [Fact] Edit_POST_Valid_Model_Updates_And_Redirects()
     [Fact] Delete_POST_Removes_Item_And_Redirects()

5. Run: dotnet test "{output_path}.Tests/" and capture the full output

6. Report PASS/FAIL for each test with exact error if failed
        """,
        expected_output="""
Test report with:
- Each test name and its result: PASS or FAIL
- For FAIL: exact exception message and the line that failed
- Summary line: "X/7 tests passed"
- Final recommendation: READY FOR REVIEW or NEEDS FIXES
  (If NEEDS FIXES: list exactly what the Developer must change)
        """,
        agent=tester,
        context=[task_migrate],
    )

    # ──────────────────────────────────────────────
    # Task 4 — Code Review
    # Agent: Critic
    # ──────────────────────────────────────────────
    task_review = Task(
        description=f"""
Review every .cs and .cshtml file in the migrated project at: {output_path}

Use list_files to find all files, then read each one.

Score checklist — deduct points for each violation found:

Program.cs (-10 HIGH each):
  - Missing AddControllersWithViews()
  - Missing AddDbContext<AppDbContext>()
  - UseAuthorization() called before UseRouting()
  - No default MapControllerRoute

Controllers (-10 HIGH each):
  - Any using System.Web found
  - HttpContext.Current used anywhere
  - DbContext instantiated with new (not injected)
  - Synchronous DB calls (SaveChanges, Find, ToList without Async)
  - Missing [ValidateAntiForgeryToken] on POST actions
  - No RedirectToAction after successful POST

Models (-5 MEDIUM each):
  - Missing [Required] on non-nullable string properties
  - Missing data annotations entirely
  - Any EF6 or System.Data.Entity references

Views (-5 MEDIUM each):
  - Html.BeginForm() found (should be <form asp-action="">)
  - Html.EditorFor() or Html.TextBoxFor() found (should be <input asp-for="">)
  - Html.ActionLink() found (should be <a asp-action="">)
  - Missing @addTagHelper in _ViewImports.cshtml

appsettings.json (-10 HIGH each):
  - Connection string missing entirely
  - Real password hardcoded (not a placeholder)

.csproj (-5 MEDIUM each):
  - Not targeting net8.0
  - Missing EF Core package references

Scoring:
  Start at 100. Apply deductions.
  HIGH issue   = -10 points
  MEDIUM issue = -5 points
  LOW issue    = -2 points
        """,
        expected_output="""
Structured code review report:
- SCORE: X/100
- HIGH SEVERITY ISSUES (list with file + description)
- MEDIUM SEVERITY ISSUES (list with file + description)
- LOW SEVERITY ISSUES (list with file + description)
- VERDICT: APPROVED (score >= 80) or NEEDS REVISION (score < 80)
- If NEEDS REVISION: ordered fix list for the Developer (most critical first)
        """,
        agent=critic,
        context=[task_migrate],
    )

    # ──────────────────────────────────────────────
    # Task 5 — Manager Final Report
    # Agent: Manager
    # ──────────────────────────────────────────────
    task_report = Task(
        description="""
You are the Migration Project Manager. Read all four reports:
  - Task 1: Migration Analysis (from Developer)
  - Task 2: Files Generated list (from Developer)
  - Task 3: Test Results (from Tester)
  - Task 4: Code Review Score (from Critic)

Apply these pass criteria:
  ✅ Code Review Score >= 80/100
  ✅ At least 6/7 tests PASS
  ✅ Core files present: Program.cs, appsettings.json, Controller, Model, Views, DbContext, .csproj

If ALL criteria met → STATUS: COMPLETE
If ANY criteria not met → STATUS: INCOMPLETE

For INCOMPLETE: list exactly what the Developer must fix, ordered by priority.

Always include a final section: WHAT NEEDS MANUAL HUMAN REVIEW
(Production connection strings, Windows Auth, deployment config,
 any custom HTTP modules, any Session usage patterns)
        """,
        expected_output="""
Final Migration Report:
- STATUS: COMPLETE or INCOMPLETE
- MIGRATION SCORE: X% overall
- FILES MIGRATED: full list
- ISSUES REMAINING: list (empty if COMPLETE)
- WHAT NEEDS MANUAL HUMAN REVIEW: always present regardless of status
        """,
        agent=manager,
        context=[task_analyze, task_migrate, task_test, task_review],
    )

    return [
        task_analyze,
        task_migrate,
        task_test,
        task_review,
        task_report,
    ]
