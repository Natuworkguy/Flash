"""The xcode tool: what it reads from Apple's tools, and what it asks
them to do, with a stand-in for the tools (no Mac needed)."""

import json

import pytest

from flash import xcode


SIMS = json.dumps({"devices": {
    "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
        {"name": "iPhone 17 Pro", "udid": "AAA", "state": "Shutdown", "isAvailable": True},
        {"name": "iPad Air", "udid": "BBB", "state": "Booted", "isAvailable": True},
        {"name": "Gone", "udid": "ZZZ", "state": "Shutdown", "isAvailable": False}],
    "com.apple.CoreSimulator.SimRuntime.watchOS-12-0": [
        {"name": "Apple Watch Ultra", "udid": "CCC", "state": "Shutdown", "isAvailable": True}],
}})
PHONES = {"result": {"devices": [{"identifier": "DEV-1", "deviceProperties": {"name": "My Phone", "osVersionNumber": "26.1"},
                                  "hardwareProperties": {"marketingName": "iPhone 16", "platform": "iOS"},
                                  "connectionProperties": {"tunnelState": "connected"}}]}}


class Fake:
    """Stands in for Apple's tools: answers by the command, and keeps
    every command it was asked to run."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, argv, seconds):
        self.calls.append(argv)
        if argv[:4] == ["xcrun", "simctl", "list", "devices"]:
            return SIMS, "", 0
        if argv[:4] == ["xcrun", "devicectl", "list", "devices"]:
            with open(argv[argv.index("--json-output") + 1], "w") as f:
                json.dump(PHONES, f)
            return "", "", 0
        for key, answer in self.answers.items():
            if key in " ".join(argv):
                return answer
        return "", "", 0


def test_build_log_errors_come_with_file_and_line():
    log = ("/u/App/ContentView.swift:12:5: error: cannot find 'Foo' in scope\n"
           "/u/App/ContentView.swift:12:5: error: cannot find 'Foo' in scope\n"
           "/u/App/Model.swift:3:1: warning: variable 'x' was never used\n"
           "xcodebuild: error: Signing for \"App\" requires a development team.\n")
    found = xcode.issues(log)
    assert found["errors"] == ["/u/App/ContentView.swift:12: cannot find 'Foo' in scope",
                               'Signing for "App" requires a development team.']
    assert found["warnings"] == ["/u/App/Model.swift:3: variable 'x' was never used"]


def test_simulators_and_devices_are_read():
    sims = xcode.simulators(SIMS)
    assert {s["udid"] for s in sims} == {"AAA", "BBB", "CCC"}
    assert next(s for s in sims if s["udid"] == "AAA")["runtime"] == "iOS 26.0"
    assert xcode.devices(json.dumps(PHONES)) == [{"name": "My Phone", "id": "DEV-1", "model": "iPhone 16",
                                                  "os": "iOS 26.1", "connected": "connected"}]


def test_target_prefers_names_then_the_booted_simulator():
    x = xcode.Xcode(Fake())
    assert x.target("")["udid"] == "BBB"                       # the booted one
    assert x.target("iphone 17")["udid"] == "AAA"
    assert x.target("my phone") == {"kind": "device", "name": "My Phone", "id": "DEV-1", "model": "iPhone 16", "os": "iOS 26.1", "connected": "connected"}
    with pytest.raises(xcode.XcodeError):
        x.target("pixel")


def test_build_asks_for_the_right_destination(tmp_path):
    proj = tmp_path / "App.xcodeproj"; proj.mkdir()
    fake = Fake({"xcodebuild": ("/u/A.swift:1:1: error: boom\n", "", 65)})
    x = xcode.Xcode(fake)
    result = x.build(str(proj), "App", x.target("watch"))
    argv = next(c for c in fake.calls if c[0] == "xcodebuild")
    assert argv[argv.index("-destination") + 1] == "platform=watchOS Simulator,id=CCC"
    assert argv[argv.index("-project") + 1] == str(proj) and argv[-1] == "build"
    assert not result["ok"] and result["errors"] == ["/u/A.swift:1: boom"]
    assert "Build failed" in xcode.describe(result) and "boom" in xcode.describe(result)


def test_a_folder_finds_its_workspace_first(tmp_path):
    (tmp_path / "App.xcodeproj").mkdir(); (tmp_path / "App.xcworkspace").mkdir()
    assert xcode.Xcode._project_args(str(tmp_path)) == ["-workspace", str(tmp_path / "App.xcworkspace")]


def test_run_on_a_simulator_boots_installs_and_watches_the_console(monkeypatch):
    monkeypatch.setattr(xcode.Xcode, "hub", staticmethod(lambda: ""))
    fake = Fake({"simctl launch": ("hello from the app\n", "", xcode.TIMED_OUT)})
    x = xcode.Xcode(fake)
    said = x.install_and_launch(x.target("iphone 17"), "/b/App.app", "com.me.app", console_seconds=5)
    joined = [" ".join(c) for c in fake.calls]
    assert any(c.startswith("xcrun simctl boot AAA") for c in joined)
    assert any(c == "xcrun simctl install AAA /b/App.app" for c in joined)
    assert any("--console-pty" in c and c.endswith("AAA com.me.app") for c in joined)
    assert said == "hello from the app"


def test_run_on_a_device_uses_devicectl():
    fake = Fake()
    x = xcode.Xcode(fake)
    x.install_and_launch(x.target("My Phone"), "/b/App.app", "com.me.app")
    joined = [" ".join(c) for c in fake.calls]
    assert "xcrun devicectl device install app --device DEV-1 /b/App.app" in joined
    assert any(c.startswith("xcrun devicectl device process launch --device DEV-1") and c.endswith("com.me.app") for c in joined)


def test_a_crash_report_is_cut_to_the_crashed_thread():
    head = {"app_name": "App", "app_version": "1.0", "timestamp": "2026-11-18 10:00:00"}
    body = {"exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"},
            "usedImages": [{"name": "App"}, {"name": "libswiftCore.dylib"}],
            "threads": [{"frames": [{"imageIndex": 1, "symbol": "idle"}]},
                        {"triggered": True, "frames": [{"imageIndex": 0, "symbol": "ContentView.load()", "sourceFile": "/u/ContentView.swift", "sourceLine": 42}]}]}
    text = xcode.crash_summary(json.dumps(head) + "\n" + json.dumps(body))
    assert "App 1.0 crashed" in text and "EXC_BREAKPOINT SIGTRAP" in text
    assert "Crashed thread 1" in text and "ContentView.load() (ContentView.swift:42)" in text
    assert "idle" not in text


def test_the_tool_says_plainly_when_there_is_no_xcode(monkeypatch):
    from flash import tools
    monkeypatch.setattr(tools.xcode, "available", lambda: False)
    assert "needs a Mac with Xcode" in tools.xcode_tool("status")
    assert "Unknown action" in tools.xcode_tool("explode")


def test_built_in_apps_open_by_name():
    assert xcode.bundle_for("Wallet") == "com.apple.Passbook"  # nosec B101
    assert xcode.bundle_for("the Settings app".removeprefix("the ")) == "com.apple.Preferences"  # nosec B101
    assert xcode.bundle_for("App Store") == "com.apple.AppStore"  # nosec B101
    assert xcode.bundle_for("com.me.app") == "com.me.app"  # nosec B101
    with pytest.raises(xcode.XcodeError, match="Known"):
        xcode.bundle_for("Nonsense")


def test_wallet_on_a_plugged_in_phone_opens_and_shows_in_device_hub(monkeypatch):
    monkeypatch.setattr(xcode.Xcode, "hub", staticmethod(lambda: "/X/DeviceHub.app"))
    fake = Fake()
    x = xcode.Xcode(fake)
    said = x.open_app(x.target("My Phone"), "Wallet")
    joined = [" ".join(c) for c in fake.calls]
    assert "xcrun devicectl device process launch --device DEV-1 com.apple.Passbook" in joined  # nosec B101
    assert "open /X/DeviceHub.app" in joined and "open devices://device/open?id=DEV-1" in joined  # nosec B101
    assert "Wallet" in said and "My Phone" in said  # nosec B101


def test_a_simulator_shows_in_device_hub_else_simulator(monkeypatch):
    fake = Fake()
    x = xcode.Xcode(fake)
    monkeypatch.setattr(xcode.Xcode, "hub", staticmethod(lambda: ""))
    assert "Simulator" in x.show(x.target("iphone 17"))  # nosec B101
    monkeypatch.setattr(xcode.Xcode, "hub", staticmethod(lambda: "/X/DeviceHub.app"))
    assert "Device Hub" in x.show(x.target("iphone 17"))  # nosec B101
    joined = [" ".join(c) for c in fake.calls]
    assert "open devices://device/open?id=AAA" in joined  # nosec B101


def test_a_device_screenshot_is_its_window_in_device_hub(monkeypatch):
    monkeypatch.setattr(xcode.Xcode, "hub", staticmethod(lambda: "/X/DeviceHub.app"))
    monkeypatch.setattr(xcode.time, "sleep", lambda s: None)
    fake = Fake({"osascript": ("100, 50, 400, 820\n", "", 0)})
    x = xcode.Xcode(fake)
    assert x.screenshot(x.target("My Phone"), "/t/p.png") == "/t/p.png"  # nosec B101
    assert ["screencapture", "-x", "-R", "100,50,400,820", "/t/p.png"] in fake.calls  # nosec B101
    monkeypatch.setattr(xcode.Xcode, "hub", staticmethod(lambda: ""))
    with pytest.raises(xcode.XcodeError, match="Device Hub"):
        x.screenshot(x.target("My Phone"), "/t/p.png")


def test_the_tool_keeps_its_hands_off_payments():
    from flash import tools
    said = tools.XCODE_TOOLS[0]["function"]["description"]
    assert "Never pay" in said and "show me how" in said  # nosec B101
    assert "open_app" in tools.XCODE_TOOLS[0]["function"]["parameters"]["properties"]["action"]["enum"]  # nosec B101
