"""Closing what we open.

Docker gives a container 1024 open files by default. Before `Container.close()`
existed, each container left about 20 descriptors behind (Chroma's SQLite file
and four index files per collection), so the suite ran out part-way through
inside the image. These tests fail if that comes back.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.main import create_app

FD_DIR = Path("/proc/self/fd")

pytestmark = pytest.mark.skipif(not FD_DIR.exists(), reason="counts descriptors through /proc (Linux only)")


def open_fds() -> int:
    return len(os.listdir(FD_DIR))


def fresh(settings, tmp_path, n):
    """Settings with their own data folder, as each test in the suite has."""
    return settings.model_copy(update={"data_dir": tmp_path / f"data-{n}"})


def test_a_closed_container_leaves_no_files_open(settings, tmp_path):
    with Container(fresh(settings, tmp_path, 0)) as warm_up:  # first use loads modules and caches
        warm_up.startup()
    before = open_fds()
    for n in range(1, 4):
        with Container(fresh(settings, tmp_path, n)) as container:
            container.startup()
    assert open_fds() <= before + 2


def test_shutting_the_app_down_closes_its_container(settings, tmp_path):
    with TestClient(create_app(fresh(settings, tmp_path, 0))):
        pass
    before = open_fds()
    apps = []
    for n in range(1, 4):
        app = create_app(fresh(settings, tmp_path, n))
        apps.append(app)  # keep a reference, as a running server would
        with TestClient(app):
            pass
    assert open_fds() <= before + 2


def test_close_is_safe_to_call_twice(settings):
    container = Container(settings)
    container.close()
    container.close()
