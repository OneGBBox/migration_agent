"""
config/settings.py

Single source of truth for all configuration.
Built with pydantic-settings — replaces the old hand-rolled dataclass approach.

WHY pydantic-settings is better than os.getenv() + dataclass:
  ✅ Reads .env file automatically — no load_dotenv() call needed anywhere
  ✅ Type coercion built-in — "3" in .env becomes int 3 automatically
  ✅ "true"/"false" in .env becomes bool True/False automatically
  ✅ Crashes immediately on startup if a required field is missing
  ✅ Field-level validation with @field_validator
  ✅ No scattered os.getenv() calls across multiple files
  ✅ Industry standard — used by FastAPI, most modern Python backends

How values are resolved (priority order, highest to lowest):
  1. CLI args passed as overrides to load_config()   ← highest priority
  2. System environment variables  ($env:ANTHROPIC_API_KEY=...)
  3. .env file values
  4. Default values defined on each field             ← lowest priority

Usage in main.py — identical to before, nothing changes there:
  from config.settings import load_config

  config = load_config()           # reads .env + system env automatically
  config.validate()                # fails fast if anything is wrong
  print(config.summary())          # safe summary, never prints API key

  config = load_config(            # CLI args override .env values
      legacy_path_override="./my_app",
      output_path_override="./output/MyApp",
  )
  config.validate()
"""

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationConfig(BaseSettings):
    """
    Pydantic-settings model — every field maps 1:1 to a .env variable.

    Pydantic-settings automatically:
      - Reads values from the .env file specified in model_config
      - Reads values from system environment variables
      - Coerces types  ("3" → int, "true" → bool)
      - Raises ValidationError on startup if a required field is missing
    """

    # ── Required — no default, crashes immediately if not set ──────────
    # anthropic_api_key: str                  # ANTHROPIC_API_KEY (required)
    openai_api_key: str                     # OPENAI_API_KEY (required)
    # ── Optional — sensible defaults if not set in .env ────────────────
    llm_model: str = "gpt-4o"  # LLM_MODEL
    legacy_project_path: str = "./legacy_sample"              # LEGACY_PROJECT_PATH
    output_project_path: str = "./output/MigratedApp"         # OUTPUT_PROJECT_PATH
    max_retry_loops: int = 3                              # MAX_RETRY_LOOPS
    verbose: bool = True                           # VERBOSE

    # ── Pydantic-settings configuration ────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",              # which file to read
        env_file_encoding="utf-8",
        case_sensitive=False,         # OPENAI_API_KEY = openai_api_key
        # ignore unknown vars in .env (don't crash)
        extra="ignore",
    )

    # ── Field-level validators ──────────────────────────────────────────
    # These run automatically when the class is instantiated

    @field_validator("max_retry_loops")
    @classmethod
    def max_retry_loops_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"MAX_RETRY_LOOPS must be >= 1, got: {v}")
        return v

    # ── Business-level validation (checks across multiple fields) ───────

    def validate(self):
        """
        Extra validations that need filesystem checks or cross-field logic.
        Pydantic validators above handle field-level checks automatically.
        Call this explicitly after load_config() in main.py.
        """
        errors = []

        if not self.openai_api_key:
            errors.append(
                "OPENAI_API_KEY is not set.\n"
                "    → Add it to your .env file: OPENAI_API_KEY=sk-ant-...\n"
                "    → Or set it as a system variable: $env:OPENAI_API_KEY='sk-ant-...'\n"
                "    → Get a key at: https://platform.openai.com/"
            )

        if not Path(self.legacy_project_path).exists():
            errors.append(
                f"Legacy project path not found: '{self.legacy_project_path}'\n"
                "    → Update LEGACY_PROJECT_PATH in .env\n"
                "    → Or pass --legacy <path> on the command line"
            )

        if errors:
            raise ValueError(
                "\n\n❌  Configuration errors found:\n\n" +
                "\n\n".join(f"  [{i+1}] {e}" for i, e in enumerate(errors))
            )

    def summary(self) -> str:
        """
        Safe human-readable config summary.
        Never prints the API key — only confirms it is set or not.
        """
        return (
            f"  LLM Model    : {self.llm_model}\n"
            f"  Legacy Path  : {self.legacy_project_path}\n"
            f"  Output Path  : {self.output_project_path}\n"
            f"  Max Retries  : {self.max_retry_loops}\n"
            f"  Verbose      : {self.verbose}\n"
            f"  API Key Set  : {'✅ Yes' if self.openai_api_key else '❌ No'}"
        )


def load_config(
    legacy_path_override: str | None = None,
    output_path_override: str | None = None,
) -> MigrationConfig:
    """
    Creates and returns a MigrationConfig instance.

    Pydantic-settings handles reading .env + system env automatically.
    Optional overrides let CLI args take priority over .env values.

    Args:
        legacy_path_override: If provided, overrides LEGACY_PROJECT_PATH from .env
        output_path_override: If provided, overrides OUTPUT_PROJECT_PATH from .env

    Returns:
        MigrationConfig with all values populated and type-validated.
        Call .validate() on the result before using it.

    Raises:
        pydantic.ValidationError — if ANTHROPIC_API_KEY is missing entirely
                                   or if a field fails its @field_validator
    """
    overrides = {}

    # Only pass overrides if they were actually provided — otherwise
    # let pydantic-settings read from .env / system env as normal
    if legacy_path_override:
        overrides["legacy_project_path"] = legacy_path_override
    if output_path_override:
        overrides["output_project_path"] = output_path_override

    return MigrationConfig(**overrides)
