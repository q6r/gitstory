"""Shared fixtures: a hermetic git environment, the synthetic repository and its metrics."""

from __future__ import annotations

import copy
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from builders import commit, history
from make_repo import NOW, SyntheticRepo, build_synthetic_repo, make_real_repo

from gitstory.collect import collect_history
from gitstory.metrics import compute_metrics
from gitstory.model import Filters

PORT_RANGE = range(17300, 17400)  # tests only ever listen on 127.0.0.1, in this block


@pytest.fixture(scope="session", autouse=True)
def _hermetic_environment() -> Iterator[None]:
    """Keep the developer's own git configuration and clocks out of every test."""
    patch = pytest.MonkeyPatch()
    patch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    patch.setenv("GIT_TERMINAL_PROMPT", "0")
    for name in os.environ:
        if name.startswith("GIT_") and name not in (
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_TERMINAL_PROMPT",
        ):
            patch.delenv(name)
    patch.delenv("SOURCE_DATE_EPOCH", raising=False)
    patch.delenv("NO_COLOR", raising=False)
    yield
    patch.undo()


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> SyntheticRepo:
    """The deterministic synthetic repository (built once per test session)."""
    return build_synthetic_repo(tmp_path_factory.mktemp("synthetic") / "story-repo")


@pytest.fixture(scope="session")
def tiny(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A six-commit repository: enough for the commands, quick to build and to read."""
    days = ["2024-03-01", "2024-03-02", "2024-03-04", "2024-03-05", "2024-03-08", "2024-03-09"]
    return make_real_repo(
        tmp_path_factory.mktemp("tiny") / "tiny-repo", [f"{day}T10:00:00+00:00" for day in days]
    )


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in repository: ``gitstory.cli`` reads ten made-up commits instead of running git.

    For the tests of the command line itself (output files, options, error handling), where the
    real history reader is not the point and every git process costs time. The returned path is
    only a name; nothing exists there.
    """
    from gitstory import cli

    commits = [
        commit(f"2024-03-{day:02d}T10:00:00+00:00", files=[("src/app.py", day, 1)])
        for day in range(1, 11)
    ]

    def read(_path: object, filters: Filters | None = None, **_options: object) -> object:
        return history(list(commits), filters=filters or Filters(), name="demo")

    monkeypatch.setattr(cli, "collect_history", read)
    return Path("a-repository-that-does-not-exist")


@pytest.fixture(scope="session")
def _session_metrics(synthetic: SyntheticRepo) -> dict[str, Any]:
    return compute_metrics(collect_history(synthetic.path), now=NOW)


@pytest.fixture
def metrics(_session_metrics: dict[str, Any]) -> dict[str, Any]:
    """The metrics of the synthetic repository at the fixed clock (a private copy per test)."""
    return copy.deepcopy(_session_metrics)


@pytest.fixture
def free_port() -> int:
    """A currently free TCP port in 17300-17399 on 127.0.0.1."""
    start = (os.getpid() * 37) % len(PORT_RANGE)
    for offset in range(len(PORT_RANGE)):
        port = PORT_RANGE[(start + offset) % len(PORT_RANGE)]
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    pytest.fail("no free port in 17300-17399")


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty temporary directory that is also the current directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path
