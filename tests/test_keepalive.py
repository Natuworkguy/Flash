# pylint: disable=C0114,C0115,C0116

import json
import plistlib
import sys
import time
from pathlib import Path

import pytest

from flash import cli, keepalive, notify, sparks, tools, web
from flash.theme import answer_from, capture_tool_output


@pytest.fixture(autouse=True)
def flash_model(monkeypatch):
    """Flash has a model, the one a spark runs on when it names none."""

    monkeypatch.setattr(tools, "MODEL_NAME", "flash-model")


# Before the fixture below swaps it out, for the test of the real one.
REAL_CHECKOUT_SCRIPT = keepalive._checkout_script


@pytest.fixture(autouse=True)
def installed_flash(monkeypatch):
    """Flash as installed (pipx, pip), unless a test says it is a clone:
    the suite itself runs from one."""

    monkeypatch.setattr(keepalive, "_checkout_script", lambda: None)


@pytest.fixture
def ran(monkeypatch):
    """The system commands keepalive runs, each a yes."""

    commands = []
    monkeypatch.setattr(
        keepalive, "_ok", lambda args: commands.append(args) or True
    )
    return commands


@pytest.fixture
def spawned(monkeypatch):
    starts = []
    monkeypatch.setattr(keepalive, "_spawn", lambda: starts.append(True))
    return starts


def _beat(always: bool) -> None:
    sparks.sparks_dir().mkdir(parents=True, exist_ok=True)
    (sparks.sparks_dir() / sparks.HEARTBEAT).write_text(json.dumps({
        "pid": 1, "always": always, "since": time.time(),
        "beat": time.time(),
    }))


def test_the_keeper_is_this_python_running_flash_sparks(monkeypatch):
    monkeypatch.setattr(keepalive, "_host", lambda: "linux")

    assert keepalive.command() == [sys.executable, "-m", "flash", "--sparks"]


def test_windows_starts_it_without_a_console(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(keepalive, "_host", lambda: "windows")
    monkeypatch.setattr(keepalive.sys, "executable", str(python))

    assert keepalive.command()[0] == str(tmp_path / "pythonw.exe")


class TestLinux:
    @pytest.fixture(autouse=True)
    def linux(self, monkeypatch):
        monkeypatch.setattr(keepalive, "_host", lambda: "linux")

    def test_systemd_gets_a_user_service(self, ran, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/odd%dir")

        how = keepalive.turn_on()

        assert "systemd" in how
        unit = keepalive._unit_path().read_text()
        assert "--sparks" in unit and "WorkingDirectory=%h" in unit
        # systemd's specifiers are escaped out of what it is given.
        assert "/odd%%dir" in unit
        assert ["systemctl", "--user", "enable", "--now",
                keepalive.NAME] in ran
        assert keepalive.installed()

        assert keepalive.turn_off()
        assert not keepalive._unit_path().exists()
        assert ["systemctl", "--user", "disable", "--now",
                keepalive.NAME] in ran
        assert not keepalive.installed()

    def test_without_systemd_it_autostarts_and_starts_now(
        self, spawned, monkeypatch
    ):
        monkeypatch.setattr(keepalive, "_systemd", lambda: False)

        how = keepalive.turn_on()

        assert "autostart" in how
        entry = keepalive._autostart_path().read_text()
        assert "Exec=" in entry and "--sparks" in entry
        assert spawned == [True]
        assert keepalive.turn_off()
        assert not keepalive._autostart_path().exists()

    def test_a_service_systemd_will_not_start_is_an_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(keepalive, "_systemd", lambda: True)
        monkeypatch.setattr(
            keepalive, "_ok", lambda args: "enable" not in args
        )

        with pytest.raises(keepalive.KeepAliveError, match="systemctl"):
            keepalive.turn_on()

    def test_off_when_it_was_never_on_says_so(self):
        assert not keepalive.turn_off()


def test_a_clone_keeps_its_own_code_running(monkeypatch, tmp_path):
    # `-m flash` from the home folder would load whatever Flash that
    # Python has installed, not this clone.
    script = tmp_path / "run.py"
    monkeypatch.setattr(keepalive, "_host", lambda: "linux")
    monkeypatch.setattr(keepalive, "_checkout_script", lambda: script)

    assert keepalive.command() == [sys.executable, str(script), "--sparks"]


def test_a_python_with_a_space_in_its_path_is_quoted(monkeypatch):
    monkeypatch.setattr(keepalive, "_host", lambda: "linux")
    monkeypatch.setattr(
        keepalive.sys, "executable", "/home/me/My Tools/python"
    )

    assert keepalive._exec_line() == (
        '"/home/me/My Tools/python" -m flash --sparks'
    )


class TestMacOS:
    @pytest.fixture(autouse=True)
    def macos(self, monkeypatch):
        monkeypatch.setattr(keepalive, "_host", lambda: "macos")
        monkeypatch.setattr(keepalive.os, "getuid", lambda: 501,
                            raising=False)

    def test_launchd_gets_an_agent(self, ran):
        keepalive.turn_on()

        plist = plistlib.loads(keepalive._plist_path().read_bytes())
        assert plist["Label"] == keepalive.LABEL
        assert plist["ProgramArguments"][-2:] == ["flash", "--sparks"]
        assert plist["RunAtLoad"] is True
        assert ["launchctl", "bootstrap", "gui/501",
                str(keepalive._plist_path())] in ran
        assert keepalive.installed()

        assert keepalive.turn_off()
        assert not keepalive._plist_path().exists()


class TestWindows:
    @pytest.fixture(autouse=True)
    def windows(self, monkeypatch):
        monkeypatch.setattr(keepalive, "_host", lambda: "windows")

    def test_task_scheduler_gets_a_logon_task(self, ran):
        keepalive.turn_on()

        made = ran[0]
        assert made[:2] == ["schtasks", "/Create"]
        assert "ONLOGON" in made and keepalive.TASK in made
        assert '"--sparks"' in made[-1]
        assert ["schtasks", "/Run", "/TN", keepalive.TASK] in ran

    def test_a_task_that_will_not_run_is_started_by_hand(
        self, spawned, monkeypatch
    ):
        monkeypatch.setattr(keepalive, "_ok", lambda args: "/Run" not in args)

        keepalive.turn_on()

        assert spawned == [True]


class TestStatus:
    def test_off(self, monkeypatch):
        monkeypatch.setattr(keepalive, "installed", lambda: False)

        assert keepalive.status() == {
            "installed": False, "running": False, "since": None,
            "here": False, "other": "",
        }

    def test_on_and_running(self, monkeypatch):
        monkeypatch.setattr(keepalive, "installed", lambda: True)
        _beat(always=True)

        state = keepalive.status()

        assert state["running"] and not state["here"]

    def test_on_with_an_open_flash_doing_the_work(self, monkeypatch):
        monkeypatch.setattr(keepalive, "installed", lambda: True)
        _beat(always=False)

        state = keepalive.status()

        assert state["here"] and not state["running"]


class TestAnotherFlash:
    """The keeper doing the work is told apart from this Flash."""

    def _beat(self, **fields):
        sparks.sparks_dir().mkdir(parents=True, exist_ok=True)
        (sparks.sparks_dir() / sparks.HEARTBEAT).write_text(json.dumps({
            "pid": 1, "always": True, "since": time.time(),
            "beat": time.time(), **fields,
        }))

    def test_this_flash_is_not_another(self):
        self._beat(**sparks.code_identity())

        assert keepalive.status()["other"] == ""

    def test_one_from_before_keepers_said_is_older(self):
        self._beat()

        assert keepalive.status()["other"] == "an older Flash"

    def test_one_elsewhere_is_named_by_its_folder(self):
        self._beat(code="/opt/pipx/venvs/flash/flash", stamp="x")

        assert keepalive.status()["other"] == (
            "another Flash, at /opt/pipx/venvs/flash/flash"
        )

    def test_this_one_from_before_an_update(self):
        self._beat(code=sparks.code_identity()["code"], stamp="old")

        assert keepalive.status()["other"] == (
            "this Flash as it was before its last update"
        )

    def test_the_keeper_says_which_flash_it_is(self, monkeypatch):
        monkeypatch.setattr(sparks, "TICK_SECONDS", 0.01)
        monkeypatch.setattr(sparks, "CHECK_EVERY_TICKS", 2)

        assert sparks._take_floor()
        try:
            sparks._beat(always=True)
            deadline = time.time() + 3
            while sparks.keeper() is None and time.time() < deadline:
                time.sleep(0.01)
            beat = sparks.keeper()
        finally:
            sparks._give_floor()

        assert beat["code"] == sparks.code_identity()["code"]
        assert beat["stamp"] == sparks.code_identity()["stamp"]

    def test_the_words_say_how_to_fix_it(self):
        from flash import ai

        said = ai._always_words({
            "installed": True, "running": True, "here": False,
            "other": "an older Flash",
        })

        assert said.startswith("Your sparks are being run by an older Flash")
        assert "/sparks takeover" in said


class TestTakeOver:
    OTHER = {"pid": 4242, "always": False, "code": "/opt/old/flash",
             "stamp": "x"}

    @pytest.fixture(autouse=True)
    def quick(self, monkeypatch):
        monkeypatch.setattr(keepalive, "TAKEOVER_SECONDS", 0.2)
        monkeypatch.setattr(keepalive, "TAKEOVER_STEP", 0.01)
        self.done = []
        monkeypatch.setattr(keepalive, "installed", lambda: True)
        monkeypatch.setattr(
            keepalive, "turn_off", lambda: self.done.append("off"),
        )
        monkeypatch.setattr(
            keepalive, "turn_on", lambda: self.done.append("on") or "x",
        )

    def test_nothing_to_do_when_this_flash_runs_them(self, monkeypatch):
        monkeypatch.setattr(sparks, "keeper", lambda: None)

        assert keepalive.take_over()["other"] == ""
        assert self.done == []

    def test_an_open_flash_is_never_stopped(self, monkeypatch):
        monkeypatch.setattr(sparks, "keeper", lambda: dict(self.OTHER))
        monkeypatch.setattr(keepalive, "_headless_keeper", lambda pid: False)
        stopped = []
        monkeypatch.setattr(keepalive, "_stop_process", stopped.append)

        with pytest.raises(keepalive.KeepAliveError, match="Close it"):
            keepalive.take_over()
        assert stopped == [] and self.done == []

    def test_the_system_keeper_is_set_up_again_from_here(self, monkeypatch):
        beats = [dict(self.OTHER, always=True)]
        monkeypatch.setattr(
            sparks, "keeper",
            lambda: beats[0] if "on" not in self.done else None,
        )
        stopped = []
        monkeypatch.setattr(keepalive, "_stop_process", stopped.append)

        state = keepalive.take_over()

        assert self.done == ["off", "on"]
        assert stopped == []
        assert state["other"] == ""

    def test_a_stray_keeper_still_holding_on_is_stopped(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(
            sparks, "keeper",
            lambda: None if stopped else dict(self.OTHER),
        )
        monkeypatch.setattr(sparks, "alive", lambda pid: not stopped)
        monkeypatch.setattr(keepalive, "_headless_keeper", lambda pid: True)
        monkeypatch.setattr(keepalive, "_stop_process", stopped.append)

        keepalive.take_over()

        assert stopped == [4242]
        assert self.done == ["off", "on"]

    def test_one_that_will_not_go_says_so(self, monkeypatch):
        monkeypatch.setattr(sparks, "keeper", lambda: dict(self.OTHER))
        monkeypatch.setattr(sparks, "alive", lambda pid: True)
        monkeypatch.setattr(keepalive, "_headless_keeper", lambda pid: True)
        monkeypatch.setattr(keepalive, "_stop_process", lambda pid: None)

        with pytest.raises(keepalive.KeepAliveError, match="still running"):
            keepalive.take_over()


def test_a_beat_from_a_process_that_is_gone_is_no_keeper(monkeypatch):
    _beat(always=True)
    monkeypatch.setattr(sparks, "alive", lambda pid: False)

    assert sparks.keeper() is None


def test_a_running_process_is_alive_and_a_made_up_one_is_not():
    import os

    assert sparks.alive(os.getpid())
    if os.name != "nt":
        assert not sparks.alive(2 ** 22 + 12345)
    assert not sparks.alive(None) and not sparks.alive(0)


def test_the_real_clone_is_found_by_its_run_py():
    script = REAL_CHECKOUT_SCRIPT()

    assert script is not None and script.name == "run.py"
    assert (script.parent / "flash" / "keepalive.py").is_file()


class TestNotify:
    def test_linux_uses_notify_send(self, monkeypatch):
        calls = []
        monkeypatch.setattr(notify, "_Notification", None)
        monkeypatch.setattr(notify.sys, "platform", "linux")
        monkeypatch.setattr(notify.shutil, "which", lambda name: name)
        monkeypatch.setattr(
            notify.subprocess, "run", lambda args, **kw: calls.append(args)
        )

        notify.notify_spark("Scout", "\n**Two** new issues.\nMore.")

        assert calls == [[
            "notify-send", "--app-name=Flash", "Scout has news",
            "**Two** new issues.",
        ]]

    def test_macos_quotes_what_it_says(self, monkeypatch):
        calls = []
        monkeypatch.setattr(notify, "_Notification", None)
        monkeypatch.setattr(notify.sys, "platform", "darwin")
        monkeypatch.setattr(
            notify.subprocess, "run", lambda args, **kw: calls.append(args)
        )

        notify.notify_spark("Scout", 'Said "hi" \\ bye')

        script = calls[0][2]
        assert 'display notification "Said \\"hi\\" \\\\ bye"' in script

    def test_a_failed_shift_says_so(self, monkeypatch):
        calls = []
        monkeypatch.setattr(notify, "_Notification", None)
        monkeypatch.setattr(notify.sys, "platform", "linux")
        monkeypatch.setattr(notify.shutil, "which", lambda name: name)
        monkeypatch.setattr(
            notify.subprocess, "run", lambda args, **kw: calls.append(args)
        )

        notify.notify_spark("Scout", "This shift failed.", failed=True)

        assert calls[0][2] == "Scout could not finish a shift"

    def test_nothing_to_notify_with_is_fine(self, monkeypatch):
        monkeypatch.setattr(notify, "_Notification", None)
        monkeypatch.setattr(notify.sys, "platform", "linux")
        monkeypatch.setattr(notify.shutil, "which", lambda name: None)

        notify.notify_spark("Scout", "News.")


def test_flash_sparks_is_a_flag():
    assert cli.parse_args(["--sparks"]).sparks
    assert not cli.parse_args([]).sparks


def test_the_page_turns_always_on_on_and_off(monkeypatch):
    calls = []
    monkeypatch.setattr(
        keepalive, "turn_on", lambda: calls.append("on") or "a service"
    )
    monkeypatch.setattr(
        keepalive, "turn_off", lambda: calls.append("off") or True
    )
    session = web.Session()

    on = web.command(session, {"name": "sparks-always", "arg": "on"})
    web.command(session, {"name": "sparks-always", "arg": "off"})

    assert calls == ["on", "off"]
    assert on["how"] == "a service" and "installed" in on["always"]
    assert "always" in web.command(session, {"name": "sparks"})


def test_settings_can_ask_without_changing_anything(monkeypatch):
    calls = []
    monkeypatch.setattr(keepalive, "turn_on", lambda: calls.append("on"))
    monkeypatch.setattr(keepalive, "turn_off", lambda: calls.append("off"))
    session = web.Session()

    asked = web.command(session, {"name": "sparks-always", "arg": ""})

    assert calls == []
    assert asked["always"]["installed"] is False
    with pytest.raises(ValueError, match="on or off"):
        web.command(session, {"name": "sparks-always", "arg": "sideways"})
    assert calls == []


def test_the_page_hears_why_always_on_failed(monkeypatch):
    def refuse():
        raise keepalive.KeepAliveError("launchd said no")

    monkeypatch.setattr(keepalive, "turn_on", refuse)

    with pytest.raises(ValueError, match="launchd said no"):
        web.command(web.Session(), {"name": "sparks-always", "arg": "on"})


def test_a_new_spark_offers_always_on_when_it_is_off():
    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: "y"):
            result = tools.make_spark("Scout", "Watch the issues.")

    assert "/sparks always on" in result


def test_a_new_spark_does_not_offer_what_is_on(monkeypatch):
    monkeypatch.setattr(keepalive, "installed", lambda: True)

    with capture_tool_output(lambda kind, text, style: None):
        with answer_from(lambda question: "y"):
            result = tools.make_spark("Scout", "Watch the issues.")

    assert "always on" not in result


def test_the_keeper_by_hand_works_from_home(monkeypatch, tmp_path):
    from flash import ai

    served = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sparks, "serve", lambda prepare, announce, wanted: served.append(
            (prepare, wanted, Path.cwd())
        ),
    )

    ai._keep_sparks()

    prepare, wanted, where = served[0]
    assert prepare is ai.refresh_config
    assert where == Path.home()
    # Run by hand, nothing takes it away.
    assert wanted is None


def test_the_login_keeper_goes_when_always_on_does(monkeypatch):
    from flash import ai

    served = []
    monkeypatch.setattr(keepalive, "installed", lambda: True)
    monkeypatch.setattr(
        sparks, "serve",
        lambda prepare, announce, wanted: served.append(wanted),
    )

    ai._keep_sparks()

    assert served == [keepalive.installed]


def test_the_keeper_starts_again_on_new_code(monkeypatch):
    from flash import ai

    started = []
    monkeypatch.setattr(sparks, "serve", lambda **kwargs: True)
    monkeypatch.setattr(
        ai.os, "execv", lambda path, args: started.append((path, args)),
    )

    ai._keep_sparks()

    command = keepalive.command()
    assert started == [(command[0], command)]


def test_the_keeper_stopped_by_hand_is_not_started_again(monkeypatch):
    from flash import ai

    started = []
    monkeypatch.setattr(sparks, "serve", lambda **kwargs: False)
    monkeypatch.setattr(ai.os, "execv", lambda *a: started.append(a))

    ai._keep_sparks()

    assert started == []
