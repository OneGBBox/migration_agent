import pytest


@pytest.fixture
def tmp_legacy(tmp_path):
    """Temporary directory that mimics a legacy project root."""
    legacy = tmp_path / "legacy_sample"
    legacy.mkdir()
    return legacy
