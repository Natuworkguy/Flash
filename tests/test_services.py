"""Extensions' services: started, started again, and stopped."""

import json
import os
import time

import pytest

from flash import extensions, services

# A service that says it is running, once a tick, until it is stopped; or,
# given "die", stops on its own after a moment.
PROGRAM = """
import pathlib, sys, time
here = pathlib.Path(sys.argv[1])
starts = here / "starts"
count = int(starts.read_text()) if starts.exists() else 0
starts.write_text(str(count + 1))
print("up", flush=True)
for _ in range(400):
    (here / "beat").write_text(str(time.time()))
    if len(sys.argv) > 2:
        time.sleep(0.2); sys.exit(1)
    time.sleep(0.05)
"""


def until(check, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


@pytest.fixture
def home(tmp_path, monkeypatch):
    found = tmp_path / "exts"
    found.mkdir()
    monkeypatch.setattr(extensions, "extensions_dir", lambda: found)
    monkeypatch.setattr(services, "FLASH_DIR", tmp_path / ".flash")
    extensions.reload()
    yield found
    extensions.reload()


def install(home, tmp_path, *extra, name="watcher"):
    folder = home / name
    folder.mkdir()
    (folder / "svc.py").write_text(PROGRAM)
    (folder / extensions.MANIFEST).write_text(
        json.dumps(
            {
                "name": name,
                "description": "d",
                "services": {
                    "name": "eyes",
                    "run": ["python", "./svc.py", str(tmp_path), *extra],
                },
            }
        )
    )
    extensions.reload()
    return folder


def test_a_manifest_names_its_services(tmp_path, home):
    install(home, tmp_path)
    ext = extensions.find("watcher")
    assert [(s.name, s.run[-2]) for s in ext.services] == [
        ("eyes", "./svc.py")
    ]  # nosec B101
    assert any("runs in the background" in line for line in ext.contents())  # nosec B101


def test_a_service_runs_and_stops_with_flash(tmp_path, home):
    install(home, tmp_path)
    keeper = services.Supervisor(check_seconds=0.1)
    keeper.start()
    until(lambda: (tmp_path / "beat").exists())
    assert keeper.names() == ["watcher: eyes"]  # nosec B101
    keeper.stop()
    stopped = (tmp_path / "beat").read_text()
    time.sleep(0.3)
    assert (tmp_path / "beat").read_text() == stopped  # nosec B101 -- no longer beating
    assert services.log_path("watcher", "eyes").read_text().startswith("up")  # nosec B101


def test_one_that_stops_is_started_again(tmp_path, home):
    install(home, tmp_path, "die")
    keeper = services.Supervisor(check_seconds=0.1)
    keeper.start()
    try:
        until(
            lambda: (
                (tmp_path / "starts").exists()
                and int((tmp_path / "starts").read_text()) >= 2
            )
        )
    finally:
        keeper.stop()


def test_removing_the_extension_stops_its_service(tmp_path, home):
    folder = install(home, tmp_path)
    keeper = services.Supervisor(check_seconds=0.1)
    keeper.start()
    try:
        until(lambda: keeper.names() == ["watcher: eyes"])
        import shutil

        shutil.rmtree(folder)
        extensions.reload()
        until(lambda: keeper.names() == [])
    finally:
        keeper.stop()


def test_an_update_starts_it_again_with_the_new_program(tmp_path, home):
    # An update that changes the program, not the command that runs it: the
    # old program must not keep running.
    folder = install(home, tmp_path)
    keeper = services.Supervisor(check_seconds=0.1)
    keeper.start()
    try:
        until(lambda: (tmp_path / "starts").exists())
        time.sleep(0.3)
        assert (tmp_path / "starts").read_text() == "1"  # nosec B101 -- left running
        program = folder / "svc.py"
        program.write_text(PROGRAM + "\n# updated\n")
        later = time.time() + 5
        os.utime(program, (later, later))
        until(lambda: int((tmp_path / "starts").read_text()) >= 2)
    finally:
        keeper.stop()
