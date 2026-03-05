# Migration Agent

Migrates ASP.NET MVC projects to .NET Core 8 using a multi-agent CrewAI pipeline.

## Setup
1. Install uv: https://docs.astral.sh/uv/
2. `uv sync`
3. Copy `.env.example` to `.env` and fill in your `OPENAI_API_KEY`

## Usage
uv run main.py
uv run main.py --legacy ./legacy_sample --output ./output/MigratedApp
uv run main.py --no-retry

## Testing

Install dev dependencies and run the full test suite:

```bash
uv sync --dev
uv run pytest -v
```

Run a single test file:

```bash
uv run pytest tests/test_tools.py -v
uv run pytest tests/test_config.py -v
uv run pytest tests/test_agents.py -v
uv run pytest tests/test_tasks.py -v
```

## Agents
- Manager – coordinates the pipeline
- Developer – performs the migration
- Tester – writes and runs tests
- Critic – reviews code quality
