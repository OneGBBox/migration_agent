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

from pathlib import Path

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

    project_name = Path(output_path).name

    # ──────────────────────────────────────────────
    # Task 1 — Analyze Legacy Project
    # Agent: Developer (read-only analysis pass)
    # ──────────────────────────────────────────────
    task_analyze = Task(
        description=f"""
Analyze the legacy ASP.NET project located at: {legacy_path}

NOTE: This project may be Web Forms (.aspx/.aspx.cs), ASP.NET MVC (Controllers/), or a mix.
Do NOT assume a folder structure — discover it by reading the actual files.

Follow these steps exactly:
1. Call list_files on "{legacy_path}" to discover every file.
   list_files returns FULL paths (e.g. "legacy_sample/LegacyInventory/Web.config").
   NEVER construct or guess file paths — use ONLY paths returned by list_files.

2. From the list_files output, identify ALL file paths. Then call read_multiple_files
   with the COMPLETE list of paths at once. Do not read files one at a time.
   Use the EXACT paths from list_files — do not shorten, modify, or reconstruct them.

Produce a Migration Analysis Report with these exact sections:

## PROJECT SUMMARY
What the app does. List every entity/feature. Note if it is Web Forms, MVC, or mixed.

## DATABASE
All table names inferred from models or raw SQL found. Connection string from Web.config.
Note if it uses EF6, pure ADO.NET (SqlConnection/SqlCommand), or both.

## ENTITIES AND DATA ACCESS PATTERNS
For each entity (e.g. Product, Category):
- All properties and data types
- Any foreign key relationships between entities
- Whether it uses EF6, pure ADO.NET, or both

## PAGES / CONTROLLERS
For each page or controller found:
- File name and class name
- Every action method / page event (Page_Load, btnSave_Click, etc.)
- HTTP verb if determinable (GET=Page_Load+!IsPostBack, POST=button click events)
- What data it reads or writes

## MIGRATION MAPPING
For each legacy piece, state the exact .NET Core 8 equivalent:
  Web Forms Page       → MVC Controller + Razor View
  Page_Load(!IsPostBack) → [HttpGet] action
  btnSave_Click        → [HttpPost] action with [ValidateAntiForgeryToken]
  Response.Redirect()  → RedirectToAction()
  Request.QueryString  → int id route parameter
  System.Configuration.ConfigurationManager → IConfiguration / appsettings.json
  System.Data.SqlClient (ADO.NET) → EF Core DbContext with async LINQ
  EF6 DbContext        → EF Core 8 DbContext
  Web.config           → appsettings.json
  Global.asax          → Program.cs

## BREAKING CHANGES
List every pattern that does NOT exist in .NET Core 8.
Include file name and line context for each.
        """,
        expected_output="""
A complete Migration Analysis Report with all 6 sections filled in.
Every section must reference actual file names and code found in the legacy project.
The ENTITIES section must list every property and every foreign key relationship.
The PAGES/CONTROLLERS section must list every page event or action.
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

IMPORTANT: Use write_batch_files to write ALL files in ONE call.
Pass a list of objects, each with "path" and "content" fields:
  [{{"path": "./output/App/Program.cs", "content": "..."}}, {{"path": "./output/App/appsettings.json", "content": "..."}}, ...]
Do NOT call write_file separately for each file — one batch call saves API quota.
Output path: {output_path}

═══════════════════════════════════════════════════════
FILES TO GENERATE
Generate a file for EVERY entity discovered in Task 1.
The legacy project has at least: Category and Product (with Category foreign key).
═══════════════════════════════════════════════════════

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
Use connection string name and database from the legacy Web.config.
Replace server with placeholder: Server=localhost;Database=InventoryDB;Trusted_Connection=True;TrustServerCertificate=True;

── {output_path}/Program.cs ──
Must include in this order:
  builder.Services.AddControllersWithViews()
  builder.Services.AddDbContext<AppDbContext>(options => options.UseSqlServer(...))
  app.UseHttpsRedirection()
  app.UseStaticFiles()
  app.UseRouting()
  app.UseAuthorization()
  app.MapControllerRoute(name: "default", pattern: "{{controller=Products}}/{{action=Index}}/{{id?}}")

── {output_path}/Data/AppDbContext.cs ──
EF Core 8 DbContext. Constructor takes DbContextOptions<AppDbContext>.
DbSet for EACH model (Category AND Product).
OnModelCreating: configure Product.Price as decimal(18,2).

── {output_path}/Models/Category.cs ──
public class Category {{
    public int Id {{ get; set; }}
    [Required][StringLength(100)] public string Name {{ get; set; }} = string.Empty;
    [StringLength(500)] public string? Description {{ get; set; }}
    public ICollection<Product> Products {{ get; set; }} = new List<Product>();
}}

── {output_path}/Models/Product.cs ──
public class Product {{
    public int Id {{ get; set; }}
    [Required][StringLength(200)] public string Name {{ get; set; }} = string.Empty;
    [StringLength(1000)] public string? Description {{ get; set; }}
    [Required][Range(0, 999999.99)] public decimal Price {{ get; set; }}
    [Required][Range(0, int.MaxValue)] public int Stock {{ get; set; }}
    public int CategoryId {{ get; set; }}
    public Category? Category {{ get; set; }}
    public DateTime CreatedAt {{ get; set; }}
}}

── {output_path}/Controllers/CategoriesController.cs ──
Constructor injects AppDbContext. All actions are async.
  Index()              GET  → _context.Categories.ToListAsync()
  Create()             GET  → return View()
  Create(model)        POST → validate, Add, SaveChangesAsync, Redirect("Index")
  Edit(id)             GET  → FindAsync(id), NotFound() if null
  Edit(id, model)      POST → update state, SaveChangesAsync, Redirect("Index")
  Delete(id)           GET  → FindAsync(id), NotFound() if null
  DeleteConfirmed(id)  POST → Remove, SaveChangesAsync, Redirect("Index")
All POST actions: [ValidateAntiForgeryToken]. Use NotFound() not HttpNotFound().

── {output_path}/Controllers/ProductsController.cs ──
Constructor injects AppDbContext. All actions are async.
  Index()              GET  → _context.Products.Include(p => p.Category).ToListAsync()
  Create()             GET  → ViewBag.Categories = new SelectList(await _context.Categories.ToListAsync(), "Id", "Name")
                             return View()
  Create(model)        POST → set model.CreatedAt = DateTime.UtcNow, validate, Add, SaveChangesAsync, Redirect("Index")
  Edit(id)             GET  → FindAsync(id), ViewBag.Categories = SelectList, return View
  Edit(id, model)      POST → update state, SaveChangesAsync, Redirect("Index")
  Delete(id)           GET  → Include(p => p.Category).FirstOrDefaultAsync(p => p.Id == id)
  DeleteConfirmed(id)  POST → Remove, SaveChangesAsync, Redirect("Index")
All POST actions: [ValidateAntiForgeryToken]. Use NotFound() not HttpNotFound().
Add: using Microsoft.AspNetCore.Mvc.Rendering; for SelectList.

── {output_path}/Views/Categories/Index.cshtml ──
Bootstrap 5 table. Columns: Name, Description, Edit/Delete links.
Tag helpers: asp-action, asp-controller, asp-route-id.

── {output_path}/Views/Categories/Create.cshtml ──
<form asp-action="Create">. Fields: Name, Description.
Use <input asp-for="Name"> and <span asp-validation-for="Name">.

── {output_path}/Views/Categories/Edit.cshtml ──
Same as Create but pre-filled. Include <input type="hidden" asp-for="Id">.

── {output_path}/Views/Categories/Delete.cshtml ──
Display Category details. Confirm button posts to DeleteConfirmed.

── {output_path}/Views/Products/Index.cshtml ──
Bootstrap 5 table. Columns: Name, Category (from Category.Name), Price, Stock, CreatedAt, Edit/Delete links.

── {output_path}/Views/Products/Create.cshtml ──
<form asp-action="Create">. Fields: Name, Description, Price, Stock.
Category: <select asp-for="CategoryId" asp-items="ViewBag.Categories"> with a blank first option.

── {output_path}/Views/Products/Edit.cshtml ──
Same as Create but pre-filled. Include <input type="hidden" asp-for="Id">.
Category select must pre-select the current CategoryId.

── {output_path}/Views/Products/Delete.cshtml ──
Display Product details including Category name. Confirm button posts to DeleteConfirmed.

── {output_path}/Views/Shared/_Layout.cshtml ──
Bootstrap 5 CDN. Nav bar with links to /Products and /Categories.
@RenderBody(). @await RenderSectionAsync("Scripts", required: false).

── {output_path}/Views/_ViewImports.cshtml ──
@using {project_name}
@using {project_name}.Models
@addTagHelper *, Microsoft.AspNetCore.Mvc.TagHelpers

── {output_path}/Views/_ViewStart.cshtml ──
@{{ Layout = "_Layout"; }}

RULES — no exceptions:
- Zero System.Web, System.Data.SqlClient, or System.Configuration references
- Zero HttpContext.Current, IsPostBack, Page_Load, Response.Redirect
- Zero Web.config references (only appsettings.json)
- All DB calls async: ToListAsync, FindAsync, FirstOrDefaultAsync, SaveChangesAsync
- DbContext injected via constructor only — never new AppDbContext()
- Use Include() for navigation properties in Index and Delete views
        """,
        expected_output="""
Confirmation that every file listed above was written successfully.
Print each file path on its own line, grouped by folder.
Confirm: CategoriesController, ProductsController, all 8 views, _Layout, _ViewImports, _ViewStart.
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
2. Read all Controller files and Model files using read_multiple_files

3. Use write_batch_files to write BOTH test files in one call (list of {{path, content}} objects):

   File 1: {output_path}.Tests/{project_name}.Tests.csproj
   Must reference:
     xunit (2.6.0+), Microsoft.NET.Test.Sdk, xunit.runner.visualstudio,
     Microsoft.EntityFrameworkCore.InMemory, Microsoft.AspNetCore.Mvc (for IActionResult)
   Must ProjectReference: ../{project_name}/{project_name}.csproj

   File 2: {output_path}.Tests/CrudControllerTests.cs
   Use InMemory DB (no real SQL Server needed):
     var options = new DbContextOptionsBuilder<AppDbContext>()
         .UseInMemoryDatabase(Guid.NewGuid().ToString()).Options;
     var context = new AppDbContext(options);

   Write tests for BOTH CategoriesController AND ProductsController.
   For ProductsController tests, seed a Category first (required FK):
     context.Categories.Add(new Category {{ Id = 1, Name = "Test" }});
     context.SaveChanges();

   Tests to write (7 for Categories + 7 for Products = 14 total):
   CategoriesController:
     [Fact] Categories_Index_Returns_All_Items()
     [Fact] Categories_Create_GET_Returns_ViewResult()
     [Fact] Categories_Create_POST_Valid_Saves_And_Redirects()
     [Fact] Categories_Create_POST_Invalid_Returns_View()
     [Fact] Categories_Edit_GET_Returns_Correct_Item()
     [Fact] Categories_Edit_POST_Valid_Updates_And_Redirects()
     [Fact] Categories_Delete_POST_Removes_And_Redirects()

   ProductsController:
     [Fact] Products_Index_Returns_All_Items()
     [Fact] Products_Create_GET_Returns_ViewResult()
     [Fact] Products_Create_POST_Valid_Saves_And_Redirects()
     [Fact] Products_Create_POST_Invalid_Returns_View()
     [Fact] Products_Edit_GET_Returns_Correct_Item()
     [Fact] Products_Edit_POST_Valid_Updates_And_Redirects()
     [Fact] Products_Delete_POST_Removes_And_Redirects()

4. Run dotnet build FIRST to catch compile errors before running tests:
   run_command: dotnet build "{output_path}/{project_name}.csproj"
   If build FAILS: report the exact compiler error. Do NOT proceed to dotnet test.
   If build SUCCEEDS: proceed to step 5.

5. Run: dotnet test "{output_path}.Tests/{project_name}.Tests.csproj"
   Capture the full output.

6. Report PASS/FAIL for each of the 14 tests with exact error if failed
        """,
        expected_output="""
Test report with:
- dotnet build result: PASS or FAIL (with error if failed)
- Each of 14 test names and its result: PASS or FAIL
- For FAIL: exact exception message and the line that failed
- Summary line: "X/14 tests passed"
- Final recommendation: READY FOR REVIEW or NEEDS FIXES
  (If NEEDS FIXES: list exactly what the Developer must change, file by file)
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
