"""Keep optional core-helper telemetry in each test's private state directory."""

import pytest


@pytest.fixture(autouse=True)
def local_citation_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "citation-state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "citation-state"))
