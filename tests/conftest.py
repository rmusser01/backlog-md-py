import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "runtime-state"))
