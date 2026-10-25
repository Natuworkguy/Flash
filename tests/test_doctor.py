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
