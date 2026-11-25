"""Windows that ask agents to keep out, and Computer Use keeping out."""

import json

import pytest

from flash import agentguard, computeruse


@pytest.fixture
def lists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("flash.notify._saved", lambda k: "Payroll App")
    (tmp_path / "agent-control").mkdir()
    (tmp_path / "agent-control" / "deny.json").write_text(
        json.dumps({"apps": ["com.example.bank"], "titles": ["tax return"]}))


def test_what_opts_out(lists):
    assert agentguard.refused({"app": "Safari", "title": "Bank [no agents]"})
    assert agentguard.refused({"app": "1Password 8", "title": "Vault"})
    assert agentguard.refused({"app": "Bank", "bundle": "com.example.bank"})
    assert agentguard.refused({"app": "Preview", "title": "Tax Return.pdf"})
    assert agentguard.refused({"app": "Payroll App", "title": "x"})
    assert agentguard.refused({"app": "Safari", "title": "News"}) == ""


def test_computer_use_stops_before_an_opted_out_window(monkeypatch):
    monkeypatch.setattr(agentguard, "window",
                        lambda kind, at=None: {"app": "Notes",
                                               "title": "keys [no agents]"})
    with pytest.raises(computeruse.UseError, match="no agents"):
        computeruse._check_window("type", {"text": "hi"})
    monkeypatch.setattr(agentguard, "window",
                        lambda kind, at=None: {"app": "Notes", "title": "x"})
    computeruse._check_window("type", {"text": "hi"})
