import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "runtime-state"))


@pytest.fixture
def ipv6_loopback_available():
    """Skip live IPv6 tests only when the OS cannot bind IPv6 loopback."""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError as exc:
        pytest.skip(f"IPv6 loopback unavailable: {exc}")
