"""Keep standalone-viewer citation state inside each test's temporary directory."""

import pytest


@pytest.fixture(autouse=True)
def local_viewer_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "viewer-state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "viewer-state"))
