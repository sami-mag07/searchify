import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Stand: 24.10.2026


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _own_config_dir(tmp_path, monkeypatch):
    """No test may read or write the real user profile."""
    monkeypatch.setenv("SEARCHIFY_CONFIG_DIR", str(tmp_path / "searchify-config"))
    from searchify import profile

    profile.reset_cache()
    yield
    profile.reset_cache()


@pytest.fixture(autouse=True)
def _no_host_memory(monkeypatch):
    """Tests climb the ladder without a host memory, so one test cannot teach the next;
    the host memory tests switch it on with their own file."""
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY", "0")
