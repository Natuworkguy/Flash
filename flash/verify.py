"""Checking a change: the project's own tests and linters, run and read.

After Flash changes code, it finds the checks the project already has
(its test runner, linter and type checker, from pyproject.toml,
package.json, Cargo.toml, go.mod or a Makefile), runs them, and reads
what failed down to the files and lines to look at. When a reply is
about to end with code changed and nothing run since, the turn is sent
back once to run them, fix what fails, and run them again: a change is
done when its checks pass, not when it has been written.
"""

import json
import os
import re
import shutil
import subprocess  # nosec B404 -- the project's own checks, asked first
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SETTING = "VERIFY_CHANGES"
TIMEOUT = 900
TAIL_LINES = 60
MAX_PLACES = 12

CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue",
    ".svelte", ".rs", ".go", ".java", ".kt", ".kts", ".swift", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".scala", ".dart", ".lua",
    ".ex", ".exs", ".zig",
}

# A shell command that is itself a check: running one counts as checking.
CHECK_COMMAND = re.compile(
    r"\b(pytest|tox|nox|unittest|ruff|flake8|mypy|pyright|pylint|jest|"
    r"vitest|mocha|eslint|tsc|rspec|phpunit|"
    r"(npm|pnpm|yarn|bun)( run)? (test|lint|check|typecheck|type-check)|"
    r"cargo (test|clippy|check)|go (test|vet)|make (test|check|lint)|"
    r"gradlew? (test|check)|mvn (test|verify)|swift test|dotnet test|"
    r"mix test)\b"
)

# Where a failure points: file and line, as each tool prints it.
PLACE = re.compile(
    r"(?P<file>(?:[\w.\-]+/)*[\w.\-]+\.(?:py|pyi|js|jsx|mjs|ts|tsx|vue|"
    r"svelte|rs|go|java|kt|swift|c|h|cc|cpp|hpp|cs|rb|php))"
    r"(?:[:(](?P<line>\d+))"
)
FAILED_TEST = re.compile(
    r"^(?:FAILED (\S+)|--- FAIL: (\S+)|test (\S+) \.\.\. FAILED|"
    r"\s*● (.+?)$|ERROR (\S+::\S+))", re.M,
)


def enabled() -> bool:
    return (os.environ.get(SETTING) or "1").strip().lower() \
        not in ("0", "off", "false", "no")


def project_root(start: str = "") -> Path:
    """The folder the project's checks run in: the nearest one up from
    START holding a project file or .git, never above home."""

    here = Path(start or os.getcwd()).expanduser().resolve()
    if here.is_file():
        here = here.parent
    home = Path.home().resolve()
    markers = ("pyproject.toml", "setup.cfg", "setup.py", "package.json",
               "Cargo.toml", "go.mod", "Makefile", ".git")
    for folder in (here, *here.parents):
        if folder == home or folder == folder.parent:
            break
        if any((folder / m).exists() for m in markers):
            return folder
    return here


def _make_targets(root: Path) -> list[str]:
    try:
        text = (root / "Makefile").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    found = set(re.findall(r"^(test|check|lint)\s*:", text, re.M))
    return [t for t in ("lint", "check", "test") if t in found]


def _python_checks(root: Path) -> list[str]:
    try:
        config = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        config = ""
    for name in ("setup.cfg", "tox.ini"):
        try:
            config += (root / name).read_text(encoding="utf-8")
        except OSError:
            pass
    checks = []
    if "ruff" in config or (root / "ruff.toml").exists() or \
            (root / ".ruff.toml").exists():
        checks.append("ruff check .")
    elif "flake8" in config or (root / ".flake8").exists():
        checks.append("flake8")
    if "[tool.mypy]" in config or (root / "mypy.ini").exists():
        checks.append("mypy .")
    has_tests = (root / "tests").is_dir() or (root / "test").is_dir() \
        or "pytest" in config or (root / "pytest.ini").exists() \
        or any(root.glob("test_*.py"))
    if has_tests:
        checks.append("python -m pytest -q")
    return checks


def _node_checks(root: Path) -> list[str]:
    try:
        scripts = json.loads((root / "package.json").read_text(
            encoding="utf-8")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return []
    runner = "pnpm" if (root / "pnpm-lock.yaml").exists() else \
        "yarn" if (root / "yarn.lock").exists() else \
        "bun" if (root / "bun.lockb").exists() else "npm"
    checks = []
    for name in ("lint", "typecheck", "type-check", "check", "test"):
        script = str(scripts.get(name) or "")
        if not script or "no test specified" in script:
            continue
        checks.append(f"{runner} test" if name == "test" and runner != "bun"
                      else f"{runner} run {name}")
    return checks


def detect(start: str = "") -> tuple[Path, list[str]]:
    """The project's own checks: where they run and what they are. A
    Makefile's lint, check and test targets come first, as the project
    put them there; else the usual ones for its language."""

    root = project_root(start)
    targets = _make_targets(root)
    if targets:
        return root, [f"make {t}" for t in targets]
    checks: list[str] = []
    if (root / "package.json").exists():
        checks += _node_checks(root)
    if (root / "Cargo.toml").exists():
        checks += ["cargo test"] + (
            ["cargo clippy -q"] if shutil.which("cargo-clippy") else [])
    if (root / "go.mod").exists():
        checks += ["go vet ./...", "go test ./..."]
    if any((root / f).exists() for f in ("pyproject.toml", "setup.cfg",
                                         "setup.py", "pytest.ini")) \
            or (root / "tests").is_dir() and any(root.rglob("test_*.py")):
        checks += _python_checks(root)
    return root, checks


@dataclass
class Ran:
    command: str
    ok: bool
    seconds: float
    output: str
    code: Optional[int] = None


def run_one(command: str, root: Path, timeout: int = TIMEOUT) -> Ran:
    started = time.monotonic()
    try:
        done = subprocess.run(  # nosec B602 -- the project's check, asked
            command, shell=True, cwd=root, capture_output=True, text=True,
            timeout=timeout, errors="replace",
            env={**os.environ, "CI": os.environ.get("CI", "1"),
                 "FORCE_COLOR": "0", "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return Ran(command, False, time.monotonic() - started,
                   out + f"\n(stopped after {timeout} seconds)")
    except OSError as exc:
        return Ran(command, False, time.monotonic() - started, str(exc))
    output = (done.stdout or "") + (done.stderr or "")
    return Ran(command, done.returncode == 0, time.monotonic() - started,
               output, done.returncode)


def failures(ran: Ran) -> tuple[list[str], list[str]]:
    """What RAN says failed: the tests by name, and the files and lines
    it points at, project files first."""

    tests = []
    for match in FAILED_TEST.finditer(ran.output):
        name = next(g for g in match.groups() if g)
        if name not in tests:
            tests.append(name.strip())
    places = []
    for match in PLACE.finditer(ran.output):
        place = f"{match['file']}:{match['line']}"
        if "site-packages" in place or "node_modules" in place:
            continue
        if place not in places:
            places.append(place)
    return tests[:MAX_PLACES], places[:MAX_PLACES]


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    kept = [ln for ln in text.strip().splitlines() if ln.strip()]
    cut = len(kept) - lines
    return ("…\n" if cut > 0 else "") + "\n".join(kept[-lines:])


def report(root: Path, results: list[Ran]) -> str:
    """The checks in words for the model: each with how it went, and for
    each failure the tests, the places to look and the end of its
    output."""

    passed = sum(r.ok for r in results)
    lines = [f"Checks in {root}: {passed} of {len(results)} passed."]
    for r in results:
        lines.append(f"{'PASS' if r.ok else 'FAIL'}  {r.command}  "
                     f"({r.seconds:.1f}s)")
    for r in results:
        if r.ok:
            continue
        tests, places = failures(r)
        lines.append(f"\n--- {r.command} failed"
                     + (f" (exit {r.code})" if r.code is not None else ""))
        if tests:
            lines.append("Failing: " + ", ".join(tests))
        if places:
            lines.append("Look at: " + ", ".join(places))
        lines.append(_tail(r.output))
    if passed == len(results):
        lines.append("\nAll passed. Say which checks ran and passed when "
                     "you report the change.")
    else:
        lines.append("\nFind the cause in the output above, fix it (never "
                     "by skipping or deleting a test), and verify again.")
    return "\n".join(lines)


NUDGE = (
    "[Before you finish: you changed code ({files}) and have not run the "
    "project's checks since. Call verify now. If anything fails, find the "
    "cause in its output, fix it and verify again, until they pass or "
    "you are sure a failure has nothing to do with this change. Then "
    "give your answer again, saying which checks passed.]"
)


@dataclass
class Watch:
    """One turn's changes to code, and whether they were checked since."""

    changed: list = field(default_factory=list)
    checked: bool = False
    nudged: bool = False

    def saw(self, name: str, args: dict, output: object) -> None:
        said = str(output or "")
        if name in ("edit", "multi_edit", "write"):
            path = str((args or {}).get("path") or "")
            if not path or Path(path).suffix.lower() not in CODE_SUFFIXES:
                return
            if said.startswith(("Error", "Edit blocked", "Write blocked")) \
                    or "blocked by user" in said:
                return
            if path not in self.changed:
                self.changed.append(path)
            self.checked = False
        elif name == "verify" or (
                name == "shell"
                and CHECK_COMMAND.search(str((args or {}).get("command")))):
            if self.changed:
                self.checked = True

    def nudge(self) -> str:
        """Once a turn: what to tell the model when it is about to end
        with code changed and not checked, or "" to let it end."""

        if self.nudged or self.checked or not self.changed or \
                not enabled():
            return ""
        _, checks = detect(str(Path(self.changed[0]).expanduser().parent))
        if not checks:
            return ""
        self.nudged = True
        names = ", ".join(Path(p).name for p in self.changed[:5]) + (
            f" and {len(self.changed) - 5} more"
            if len(self.changed) > 5 else "")
        return NUDGE.format(files=names)
