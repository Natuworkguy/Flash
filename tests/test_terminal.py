# pylint: disable=C0114,C0115,C0116

import os
import shutil
import subprocess  # nosec B404
import sys
import time

import pytest

from flash import terminal


def _log(*rows):
    terminal.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with terminal.LOG_PATH.open("a", encoding="utf-8") as handle:
        for stamp, code, took, cwd, command in rows:
            handle.write(f"{stamp}\t{code}\t{took}\t{cwd}\t{command}\n")


# --- reading the log -------------------------------------------------------


def test_read_parses_and_skips_malformed_lines():
    _log((100, 0, 2, "/p", "make"), (101, 1, "-", "/p", "pytest -q"))
    with terminal.LOG_PATH.open("a") as handle:
        handle.write("garbage without tabs\nx\t1\t2\t/p\tcmd\n")

    assert [(c.time, c.exit, c.took, c.command) for c in terminal.read()] \
        == [(100.0, 0, 2, "make"), (101.0, 1, None, "pytest -q")]  # nosec


def test_read_trims_a_long_log(monkeypatch):
    monkeypatch.setattr(terminal, "MAX_LOG_LINES", 3)
    _log(*[(i, 0, 0, "/p", f"cmd {i}") for i in range(10)])

    assert [c.command for c in terminal.read()] == [  # nosec B101
        "cmd 7", "cmd 8", "cmd 9"
    ]
    assert len(terminal.LOG_PATH.read_text().splitlines()) == 3  # nosec


def test_no_log_means_nothing_to_say():
    assert terminal.read() == []  # nosec B101
    assert terminal.since(0) == ""  # nosec B101


def test_since_shows_new_commands_with_outcomes(isolated_home):
    project = f"{isolated_home}/proj"
    _log((100, 0, 0, project, "git pull"),
         (200, 0, 3, project, "npm install"),
         (201, 1, 12, project, "npm test"),
         (202, 0, 0, project, "flash"))

    block = terminal.since(150)

    assert block == (  # nosec B101
        f"{terminal.HEADER}\n"
        "✓ · 3s · ~/proj · npm install\n"
        "✗ exit 1 · 12s · ~/proj · npm test\n"
        f"{terminal.FOOTER}"
    )


def test_since_keeps_the_latest_few(monkeypatch):
    monkeypatch.setattr(terminal, "SHOWN", 2)
    _log(*[(i, 0, 0, "/p", f"cmd {i}") for i in range(1, 6)])

    block = terminal.since(0)

    assert "(3 earlier left out)" in block  # nosec B101
    assert "cmd 4" in block and "cmd 5" in block  # nosec B101
    assert "cmd 3" not in block  # nosec B101


@pytest.mark.parametrize(("typed", "shown"), [
    ("export GITHUB_TOKEN=ghp_abc123", "export GITHUB_TOKEN=[redacted]"),
    ('OPENAI_API_KEY="sk live" python app.py',
     "OPENAI_API_KEY=[redacted] python app.py"),
    ("mysql -u root --password hunter2 db",
     "mysql -u root --password [redacted] db"),
    ("gh auth login --token=abc", "gh auth login --token=[redacted]"),
    ("curl -H 'Authorization: Bearer xyz.123' api",
     "curl -H 'Authorization: Bearer [redacted]' api"),
    ("git clone https://me:s3cret@github.com/x/y",
     "git clone https://me:[redacted]@github.com/x/y"),
    ("pytest -k test_password_reset", "pytest -k test_password_reset"),
    ("ls -la ~/keys", "ls -la ~/keys"),
])
def test_secrets_are_redacted(typed, shown):
    assert terminal.redact(typed) == shown  # nosec B101


def test_since_redacts_and_caps_length(monkeypatch):
    monkeypatch.setattr(terminal, "COMMAND_CHARS", 30)
    _log((1, 0, 0, "/p", "export API_TOKEN=abc && " + "x" * 100))

    line = terminal.since(0).splitlines()[1]

    assert "abc" not in line  # nosec B101
    assert line.endswith("…")  # nosec B101


# --- installing the hook ---------------------------------------------------


def test_current_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert terminal.current_shell() == "zsh"  # nosec B101
    monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
    assert terminal.current_shell() == ""  # nosec B101


def test_install_is_idempotent_and_remove_restores(isolated_home):
    rc = isolated_home / ".zshrc"
    original = "export PATH=$HOME/bin:$PATH\nalias ll='ls -la'\n"
    rc.write_text(original)

    assert "Added to" in terminal.install("zsh")  # nosec B101
    assert terminal.installed("zsh")  # nosec B101
    assert terminal.hook_path("zsh").read_text() == terminal.ZSH_HOOK
    assert rc.read_text() == original + "\n" + terminal.rc_block("zsh")

    assert terminal.install("zsh").startswith("Already set up")  # nosec
    assert rc.read_text().count(terminal.BEGIN) == 1  # nosec B101

    assert terminal.remove("zsh").startswith("Removed from")  # nosec B101
    assert rc.read_text() == original  # nosec B101
    assert not terminal.hook_path("zsh").exists()  # nosec B101
    assert terminal.remove("zsh").startswith("Nothing to remove")  # nosec


def test_install_into_a_missing_rc_and_remove_keeps_what_follows(
    isolated_home,
):
    rc = isolated_home / ".bashrc"
    terminal.install("bash")
    assert rc.read_text() == terminal.rc_block("bash")  # nosec B101

    with rc.open("a") as handle:
        handle.write("alias gs='git status'\n")
    terminal.remove("bash")

    assert rc.read_text() == "alias gs='git status'\n"  # nosec B101


# --- the hooks in real shells ----------------------------------------------


def _drive(shell: str, home, term_program: str, lines: list[str]) -> None:
    """Type LINES into a real interactive SHELL on a pseudo-terminal."""

    import pty
    import select

    argv = {
        "zsh": ["zsh", "-f", "-i"],
        "bash": ["bash", "--norc", "--noprofile", "-i"],
    }[shell]
    env = dict(os.environ, HOME=str(home), TERM_PROGRAM=term_program,
               PS1="$ ", PROMPT="$ ", HISTFILE=str(home / ".hist"))
    master, slave = pty.openpty()
    proc = subprocess.Popen(  # nosec B603
        argv, stdin=slave, stdout=slave, stderr=slave, env=env,
        cwd=str(home), start_new_session=True,
    )
    os.close(slave)

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    os.read(master, 4096)
                except OSError:
                    return

    drain(0.5)
    for line in lines:
        os.write(master, (line + "\n").encode())
        drain(1.6 if line.startswith("sleep") else 0.4)
    proc.wait(10)
    os.close(master)


needs_pty = pytest.mark.skipif(
    sys.platform == "win32", reason="needs a POSIX pseudo-terminal"
)


@needs_pty
@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_hook_records_commands_in_vscode(shell, isolated_home):
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    terminal.write_hook(shell)

    _drive(shell, isolated_home, "vscode", [
        f". {terminal.hook_path(shell)}",
        "true", "false", "sh -c 'exit 2'", "sleep 1",
        " echo hidden-by-a-leading-space", "exit",
    ])

    got = terminal.read()
    assert [(c.exit, c.command) for c in got] == [  # nosec B101
        (0, "true"), (1, "false"), (2, "sh -c 'exit 2'"), (0, "sleep 1"),
    ]
    assert all(c.cwd == str(isolated_home) for c in got)  # nosec B101
    if shell == "zsh":
        assert got[3].took >= 1  # nosec B101
    else:
        assert got[3].took is None  # nosec B101

    # Whole-second stamps would put a command that finished in the same
    # second as the user's message before it, and since() would skip it.
    realtime = shell == "zsh" or subprocess.run(  # nosec B603 B607
        ["bash", "-c", 'echo "${EPOCHREALTIME:+yes}"'],
        capture_output=True, text=True, check=False,
    ).stdout.strip() == "yes"
    if realtime:
        assert any(c.time % 1 for c in got)  # nosec B101


@needs_pty
@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_hook_stays_silent_outside_vscode(shell, isolated_home):
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    terminal.write_hook(shell)

    _drive(shell, isolated_home, "iTerm.app", [
        f". {terminal.hook_path(shell)}", "false", "exit",
    ])

    assert terminal.read() == []  # nosec B101
