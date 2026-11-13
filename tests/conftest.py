"""Shared fixtures."""

import pytest

from app import store


@pytest.fixture(autouse=True)
def exercises_in_a_temporary_folder(tmp_path, monkeypatch):
    """Live exercises started by a test are saved under tmp_path, never in the project."""
    monkeypatch.setattr(store, "FOLDER", tmp_path / "exercises")
