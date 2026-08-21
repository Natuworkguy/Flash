# pylint: disable=C0114,C0115,C0116

from flash.updater import (
    check_for_update,
    fetch_latest_version,
    is_newer,
    is_pipx_install,
)
from flash.version import __version__


def test_is_newer():
    assert is_newer("9.9.9", current="0.1.0")  # nosec B101
    assert not is_newer("0.1.0", current="0.1.0")  # nosec B101
    assert not is_newer("0.0.9", current="0.1.0")  # nosec B101


def test_is_newer_handles_non_semver():
    assert is_newer("weird-tag", current="0.1.0")  # nosec B101
    assert not is_newer(__version__, current=__version__)  # nosec B101


def test_fetch_latest_version_network_failure(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise OSError("no network")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert fetch_latest_version() is None  # nosec B101


def test_check_for_update_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "flash.updater.fetch_latest_version", lambda: __version__
    )
    assert check_for_update() is None  # nosec B101


def test_check_for_update_available(monkeypatch):
    monkeypatch.setattr(
        "flash.updater.fetch_latest_version", lambda: "99.0.0"
    )
    assert check_for_update() == "99.0.0"  # nosec B101


def test_is_pipx_install_when_flash_not_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert not is_pipx_install()  # nosec B101
