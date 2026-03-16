"""
tests/test_config.py

Unit tests for config/settings.py.
Tests MigrationConfig validation, the validate() method, summary(), and load_config().

Run with: uv run pytest tests/test_config.py -v
"""

import pytest
from pydantic import ValidationError

from config.settings import MigrationConfig, load_config


# ──────────────────────────────────────────────
# Instantiation & field defaults
# ──────────────────────────────────────────────

class TestMigrationConfigDefaults:
    def test_valid_config_accepts_api_key(self):
        config = MigrationConfig(openai_api_key="sk-test")
        assert config.openai_api_key == "sk-test"

    def test_default_llm_model_is_gpt4o(self):
        config = MigrationConfig(openai_api_key="sk-test")
        assert config.llm_model == "gpt-4o"

    def test_default_max_retry_loops_is_three(self):
        config = MigrationConfig(openai_api_key="sk-test")
        assert config.max_retry_loops == 3

    def test_default_legacy_path(self):
        config = MigrationConfig(openai_api_key="sk-test")
        assert config.legacy_project_path == "./legacy_sample"

    def test_default_output_path_auto_derives_from_legacy(self):
        # Default legacy_sample contains LegacyInventory.sln → output is auto-derived
        config = MigrationConfig(openai_api_key="sk-test")
        assert config.output_project_path == "./output/LegacyInventory"

    def test_explicit_output_path_is_respected(self):
        config = MigrationConfig(openai_api_key="sk-test", output_project_path="./output/Custom")
        assert config.output_project_path == "./output/Custom"

    def test_missing_api_key_raises_validation_error(self, tmp_path, monkeypatch):
        # Change to a dir with no .env so pydantic-settings cannot find a key
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            MigrationConfig()


# ──────────────────────────────────────────────
# Field-level validators
# ──────────────────────────────────────────────

class TestFieldValidators:
    def test_llm_model_can_be_overridden(self):
        config = MigrationConfig(
            openai_api_key="test", llm_model="gpt-4o-mini")
        assert config.llm_model == "gpt-4o-mini"

    def test_max_retry_loops_zero_raises(self):
        with pytest.raises(ValidationError):
            MigrationConfig(openai_api_key="test", max_retry_loops=0)

    def test_max_retry_loops_negative_raises(self):
        with pytest.raises(ValidationError):
            MigrationConfig(openai_api_key="test", max_retry_loops=-1)

    def test_max_retry_loops_one_is_valid(self):
        config = MigrationConfig(openai_api_key="test", max_retry_loops=1)
        assert config.max_retry_loops == 1

    def test_max_retry_loops_large_number_is_valid(self):
        config = MigrationConfig(openai_api_key="test", max_retry_loops=99)
        assert config.max_retry_loops == 99


# ──────────────────────────────────────────────
# validate() business-level checks
# ──────────────────────────────────────────────

class TestValidateMethod:
    def test_passes_when_all_valid(self, tmp_legacy):
        config = MigrationConfig(
            openai_api_key="sk-test",
            legacy_project_path=str(tmp_legacy),
        )
        config.validate()  # must not raise

    def test_raises_when_legacy_path_missing(self):
        config = MigrationConfig(
            openai_api_key="sk-test",
            legacy_project_path="/nonexistent/path/abc123xyz",
        )
        with pytest.raises(ValueError, match="Legacy project path not found"):
            config.validate()

    def test_raises_when_api_key_is_empty_string(self, tmp_legacy):
        config = MigrationConfig(
            openai_api_key="sk-test",
            legacy_project_path=str(tmp_legacy),
        )
        # Force empty key to exercise the validate() branch
        object.__setattr__(config, "openai_api_key", "")
        with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
            config.validate()

    def test_error_message_lists_all_problems(self):
        config = MigrationConfig(
            openai_api_key="sk-test",
            legacy_project_path="/does/not/exist",
        )
        with pytest.raises(ValueError, match="Legacy project path not found"):
            config.validate()


# ──────────────────────────────────────────────
# summary() — must never leak the API key
# ──────────────────────────────────────────────

class TestSummaryMethod:
    def test_api_key_value_not_in_summary(self):
        config = MigrationConfig(openai_api_key="sk-super-secret-12345")
        assert "sk-super-secret-12345" not in config.summary()

    def test_summary_confirms_key_is_set(self):
        config = MigrationConfig(openai_api_key="sk-test")
        assert "Yes" in config.summary()

    def test_summary_contains_llm_model(self):
        config = MigrationConfig(
            openai_api_key="test",
            llm_model="gpt-4o-mini",
        )
        assert "gpt-4o-mini" in config.summary()

    def test_summary_contains_both_paths(self):
        config = MigrationConfig(
            openai_api_key="test",
            legacy_project_path="./my_legacy",
            output_project_path="./my_output",
        )
        summary = config.summary()
        assert "./my_legacy" in summary
        assert "./my_output" in summary

    def test_summary_contains_retry_count(self):
        config = MigrationConfig(openai_api_key="test", max_retry_loops=5)
        assert "5" in config.summary()


# ──────────────────────────────────────────────
# load_config() factory
# ──────────────────────────────────────────────

class TestLoadConfig:
    def test_override_legacy_path(self, tmp_legacy, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = load_config(legacy_path_override=str(tmp_legacy))
        assert config.legacy_project_path == str(tmp_legacy)

    def test_override_output_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        output = str(tmp_path / "custom_output")
        config = load_config(output_path_override=output)
        assert config.output_project_path == output

    def test_env_var_sets_retry_loops(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("MAX_RETRY_LOOPS", "7")
        config = load_config()
        assert config.max_retry_loops == 7

    def test_override_takes_priority_over_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("LEGACY_PROJECT_PATH", "./env_legacy")
        override = str(tmp_path / "cli_legacy")
        config = load_config(legacy_path_override=override)
        assert config.legacy_project_path == override
