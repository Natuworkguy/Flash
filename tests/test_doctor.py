# pylint: disable=C0114,C0115,C0116

from flash import cli, doctor, extensions, models, web


def test_it_says_what_is_wrong_and_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(models, "installed_names", lambda client: None)
    checks = doctor.run("http://localhost:1", "alpha:8b")
    by = {c.name: c for c in checks}

    assert by["Python"].state == doctor.OK  # nosec B101
    assert by["Ollama"].state == doctor.FAIL  # nosec B101
    assert "ollama serve" in by["Ollama"].fix  # nosec B101
    assert by["Extensions"].state == doctor.OFF  # nosec B101

    lines = []
    failed = doctor.report(checks, lambda line, state: lines.append(line))
    assert failed >= 1 and lines[-1].endswith("to look at.")  # nosec B101
    assert any(line.startswith("✗ Ollama") for line in lines)  # nosec B101


def test_a_model_ollama_does_not_have(monkeypatch):
    monkeypatch.setattr(models, "installed_names",
                        lambda client: {"beta:8b"})
    by = {c.name: c for c in doctor.run("http://x", "alpha:8b")}
    assert by["Ollama"].state == doctor.OK  # nosec B101
    assert by["Model"].state == doctor.FAIL  # nosec B101
    assert by["Model"].fix.startswith("ollama pull alpha:8b")  # nosec B101

    by = {c.name: c for c in doctor.run("http://x", "beta:8b")}
    assert by["Model"].state == doctor.OK  # nosec B101


def test_a_broken_extension_and_a_missing_connector(monkeypatch):
    monkeypatch.setattr(models, "installed_names", lambda client: set())
    monkeypatch.setattr(extensions, "problems", lambda: ["x: bad manifest"])
    from flash import connectors

    monkeypatch.setattr(connectors, "read_config", lambda: {
        "gone": {"command": "no-such-program-here"}})
    checks = doctor.run("http://x", "")
    said = [(c.name, c.state) for c in checks]
    assert ("Extension", doctor.WARN) in said  # nosec B101
    assert ("Connector gone", doctor.FAIL) in said  # nosec B101
    assert ("Model", doctor.WARN) in said  # nosec B101


def test_the_cli_and_the_page_ask_for_it(monkeypatch):
    assert cli.parse_args(["doctor"]).command == "doctor"  # nosec B101
    monkeypatch.setattr(models, "installed_names", lambda client: None)
    said = web.command(None, {"name": "doctor"})
    assert said["checks"][0]["name"] == "Python"  # nosec B101


def test_a_fix_that_is_a_command_runs_on_request(monkeypatch):
    monkeypatch.setattr(models, "installed_names", lambda client: {"b:1"})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/bin/" + name)
    checks = doctor.run("http://x", "alpha:8b")
    [model] = [c for c in checks if c.name == "Model"]
    assert model.fixable and model.command == ["ollama", "pull",  # nosec
                                               "alpha:8b"]
    assert doctor.hint(checks) == (  # nosec B101
        "Run flash doctor --fix to fix 1 fixable issue.")

    ran = []

    def run(argv, **kw):
        ran.append(argv)
        return doctor.subprocess.CompletedProcess(argv, 0, "pulled\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", run)
    lines = []
    assert doctor.fix_all(checks, lambda line, state: lines.append(  # nosec
        line)) == 1
    assert ran == [["ollama", "pull", "alpha:8b"]]  # nosec B101
    assert any("pulled" in line for line in lines)  # nosec B101


def test_the_page_runs_one_fix(monkeypatch):
    monkeypatch.setattr(models, "installed_names", lambda client: None)
    monkeypatch.setattr(doctor, "run_fix", lambda check: (True, "ok"))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    said = web.command(None, {"name": "doctor"})
    [ollama] = [c for c in said["checks"] if c["name"] == "Ollama"]
    assert ollama["fixable"] and ollama["background"]  # nosec B101
    said = web.command(None, {"name": "doctor-fix", "arg": "Ollama"})
    assert said["ok"] and said["checks"]  # nosec B101
    assert cli.parse_args(["doctor", "--fix"]).fix  # nosec B101
