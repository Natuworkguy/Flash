"""Connectors: other programs' tools, through the Model Context Protocol.

An MCP server is a program that offers tools (GitHub's issues, a
database, a design file, a calendar) in one shared shape that any
assistant can call. Flash connects to the ones listed in
~/.flash/mcp.json, in the shape every MCP client reads, so a config
written for another one can be pasted in whole:

    {"mcpServers": {
        "github": {"command": "npx",
                   "args": ["-y", "@modelcontextprotocol/server-github"],
                   "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}},
        "docs": {"url": "https://example.com/mcp",
                 "headers": {"Authorization": "Bearer ..."}}
    }}

One with a command is a program Flash starts and talks to over its
standard input and output; one with a url is a server elsewhere, spoken
to over HTTP. One with a url can have a start command too: a program
Flash runs first, and stops with it, that serves that url once it is up
("start": {"command": "python", "args": ["mymcp/server.py"]}, then
"url": "http://localhost:1234/mcp"). Either way its tools join Flash's
own, named <server>__<tool>. A tool its server marks read-only runs
straight away; any other asks first, as a shell command does, unless
autonomous mode is on.

Servers start in the background the first time tools are wanted, so a
slow one (npx fetching a package) never holds up Flash starting, and
each is stopped when Flash exits.
"""

import atexit
import base64
import itertools
import json
import os
import re
import shutil
import subprocess  # nosec B404
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .paths import FLASH_DIR
from .version import __version__

PROTOCOL_VERSION = "2025-06-18"
CONFIG_NAME = "mcp.json"
# A server started for the first time may have a package to fetch first.
START_SECONDS = 60.0
CALL_SECONDS = 120.0
# How long the first turn that wants tools waits for servers still
# starting, before going on without them.
FIRST_WAIT_SECONDS = 10.0
# How long refresh() waits for servers still starting.
REFRESH_SECONDS = 15.0
SEPARATOR = "__"
NAME_CHARS = 64
# What a server prints to its error output, kept for when it fails.
KEPT_LINES = 40
RESULT_CHARS = 60_000
_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")

# How a server stands, for /mcp and Settings.
OFF = "off"
STARTING = "starting"
READY = "ready"
FAILED = "failed"


class ConnectorError(Exception):
    """A server that could not be reached, or a call it refused."""


def config_path() -> Path:
    return FLASH_DIR / CONFIG_NAME


def read_config() -> dict:
    """The servers listed in the config, by name: {} when there is none
    or it cannot be read."""

    try:
        found = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = found.get("mcpServers") if isinstance(found, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {
        str(name): spec for name, spec in servers.items()
        if isinstance(spec, dict)
    }


def write_config(servers: dict) -> None:
    """Write SERVERS back, readable by this user only: it holds tokens."""

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        whole = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(whole, dict):
            whole = {}
    except (OSError, ValueError):
        whole = {}
    whole["mcpServers"] = servers
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(whole, indent=2) + "\n", encoding="utf-8")
    os.chmod(partial, 0o600)
    partial.replace(path)


def check_name(name: str) -> str:
    name = str(name or "").strip()
    if not _SERVER_NAME.match(name):
        raise ConnectorError(
            "Name a connector with letters, digits, - and _ only, up to 32."
        )
    return name


# A url typed without its scheme: localhost:1234/mcp, 127.0.0.1:8000.
_BARE_URL = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\]|[\w.-]+\.\w+)"
                       r"(:\d+)?(/\S*)?$", re.IGNORECASE)


def _command(text: str) -> dict:
    """A command line as {"command", "args"}; a .py file alone runs on
    Flash's own Python."""

    import shlex
    import sys

    try:
        words = shlex.split(str(text), posix=os.name != "nt")
    except ValueError as exc:
        raise ConnectorError(f"That command does not parse: {exc}") from None
    if not words:
        raise ConnectorError("Give the command that starts it.")
    if words[0].endswith(".py"):
        words = [sys.executable, *words]
    return {"command": words[0], "args": words[1:]}


def _url(text: str) -> str:
    text = str(text or "").strip()
    if re.match(r"^https?://", text, re.IGNORECASE):
        return text
    if _BARE_URL.match(text):
        local = re.match(r"^(localhost|127\.|\[::1\])", text)
        return ("http://" if local else "https://") + text
    return ""


def spec_from(target: str, env: Optional[dict] = None,
              start: str = "") -> dict:
    """A server's entry from what was typed: a URL, or a command and its
    arguments. ENV is its environment, or a URL's headers. START, with a
    URL: the command that runs the server serving it."""

    target = str(target or "").strip()
    start = str(start or "").strip()
    if not target:
        raise ConnectorError("Give the server's command, or its URL.")
    # "run X then connect to URL", as said.
    said = re.match(r"^(?:run\s+)?(.+?)\s+(?:then\s+(?:connect\s+to\s+)?"
                    r"|->\s*|=>\s*)(\S+)$", target, re.IGNORECASE)
    if said and _url(said.group(2)) and not start:
        start, target = said.group(1), said.group(2)
    extra = {str(k): str(v) for k, v in (env or {}).items()}
    url = _url(target)
    if url:
        return {"url": url,
                **({"start": {**_command(start),
                              **({"env": extra} if extra else {})}}
                   if start else {}),
                **({"headers": extra} if extra and not start else {})}
    if start:
        raise ConnectorError("A start command goes with a URL to connect "
                             "to once it is up.")
    return {**_command(target), **({"env": extra} if extra else {})}


def target_of(spec: dict) -> tuple[str, str]:
    """What spec_from would be given for SPEC: (target, start)."""

    import shlex

    def line(part: dict) -> str:
        words = [part.get("command") or "", *(part.get("args") or [])]
        return " ".join(shlex.quote(str(w)) for w in words)

    if "url" in spec:
        begin = spec.get("start")
        return str(spec["url"]), line(begin) if isinstance(begin, dict) \
            else str(begin or "")
    return line(spec), ""


# --- Talking to one server --------------------------------------------


class _Link:
    """How messages get to a server and back: its subclasses send one
    request and return its answer. ANSWER says what Flash replies to a
    request the server makes of it."""

    def __init__(self, answer: Callable[[dict], Optional[dict]]) -> None:
        self._answer = answer

    def request(self, message: dict, seconds: float) -> dict:
        raise NotImplementedError

    def notify(self, message: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Nothing to let go of, by default."""

    def problem(self) -> str:
        return ""

    def alive(self) -> bool:
        return True


class _StdioLink(_Link):
    """A program Flash starts, one JSON message per line each way."""

    def __init__(self, spec: dict, answer) -> None:
        super().__init__(answer)
        command = str(spec.get("command") or "")
        args = [str(a) for a in spec.get("args") or []]
        env = {**os.environ, **{
            str(k): str(v) for k, v in (spec.get("env") or {}).items()
        }}
        found = shutil.which(command, path=env.get("PATH")) or command
        cwd = spec.get("cwd")
        try:
            self._process = subprocess.Popen(  # nosec B603
                [found, *args], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                cwd=str(Path(cwd).expanduser()) if cwd else None,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                # Its own group, so Ctrl+C in Flash's terminal is
                # Flash's, and the server is stopped by Flash.
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise ConnectorError(f"Could not start {command}: {exc}") from None
        self._waiting: dict[Any, list] = {}
        self._lock = threading.Lock()
        self._said: list[str] = []
        self._readers = [
            threading.Thread(target=self._read, daemon=True),
            threading.Thread(target=self._read_errors, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _read(self) -> None:
        for line in self._process.stdout or ():
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                # Some servers print to standard output what belongs on
                # standard error: kept with the errors, not fatal.
                self._said.append(line[:500])
                del self._said[:-KEPT_LINES]
                continue
            self._arrived(message)
        with self._lock:
            for slot in self._waiting.values():
                slot[1].set()

    def _arrived(self, message: Any) -> None:
        for one in message if isinstance(message, list) else [message]:
            if not isinstance(one, dict):
                continue
            if "method" in one:
                reply = self._answer(one)
                if reply is not None:
                    self._write(reply)
                continue
            with self._lock:
                slot = self._waiting.get(one.get("id"))
            if slot is not None:
                slot[0] = one
                slot[1].set()

    def _read_errors(self) -> None:
        for line in self._process.stderr or ():
            self._said.append(line.rstrip()[:500])
            del self._said[:-KEPT_LINES]

    def _write(self, message: dict) -> None:
        try:
            self._process.stdin.write(json.dumps(message) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError, AttributeError) as exc:
            raise ConnectorError(f"The server went away: {exc}") from None

    def request(self, message: dict, seconds: float) -> dict:
        slot: list = [None, threading.Event()]
        with self._lock:
            self._waiting[message["id"]] = slot
        try:
            self._write(message)
            if not slot[1].wait(seconds):
                raise ConnectorError(
                    f"No answer to {message['method']} in {seconds:.0f}s."
                )
            if slot[0] is None:
                raise ConnectorError("The server stopped." + self._last())
            return slot[0]
        finally:
            with self._lock:
                self._waiting.pop(message["id"], None)

    def notify(self, message: dict) -> None:
        self._write(message)

    def _last(self) -> str:
        said = [line for line in self._said if line.strip()]
        return f" It said: {said[-1]}" if said else ""

    def problem(self) -> str:
        return "\n".join(self._said[-8:])

    def alive(self) -> bool:
        return self._process.poll() is None

    def close(self) -> None:
        process = self._process
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        # Its output ends with it: read to the end, then let go.
        for reader in self._readers:
            reader.join(2)
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass


class _HttpLink(_Link):
    """A server elsewhere, each message a POST, its answer JSON or a
    stream of events (Streamable HTTP)."""

    def __init__(self, spec: dict, answer) -> None:
        super().__init__(answer)
        self._url = str(spec["url"])
        self._headers = {
            str(k): str(v) for k, v in (spec.get("headers") or {}).items()
        }
        self._session = ""
        self.version = ""

    def _post(self, message: dict, seconds: float):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"Flash/{__version__}",
            **self._headers,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        request = Request(  # nosec B310 -- a URL the user configured
            self._url, data=json.dumps(message).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            response = urlopen(request, timeout=seconds)  # nosec B310
        except HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300].strip()
            raise ConnectorError(
                f"The server answered {exc.code}"
                + (f": {body}" if body else ".")
            ) from None
        except (URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise ConnectorError(f"Could not reach {self._url}: {reason}") \
                from None
        session = response.headers.get("Mcp-Session-Id")
        if session:
            self._session = session
        return response

    def request(self, message: dict, seconds: float) -> dict:
        with self._post(message, seconds) as response:
            kind = response.headers.get("Content-Type", "")
            if "text/event-stream" in kind:
                return self._from_stream(response, message["id"])
            try:
                found = json.loads(response.read().decode("utf-8"))
            except ValueError:
                raise ConnectorError("The server's answer was not JSON.") \
                    from None
        for one in found if isinstance(found, list) else [found]:
            if isinstance(one, dict) and one.get("id") == message["id"]:
                return one
        raise ConnectorError("The server did not answer that request.")

    def _from_stream(self, response, wanted) -> dict:
        data: list[str] = []
        for raw in response:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line or not data:
                continue
            try:
                message = json.loads("\n".join(data))
            except ValueError:
                message = None
            data = []
            if not isinstance(message, dict):
                continue
            if message.get("id") == wanted and "method" not in message:
                return message
            if "method" in message:
                reply = self._answer(message)
                if reply is not None:
                    self._post(reply, CALL_SECONDS).close()
        raise ConnectorError("The server's stream ended without an answer.")

    def notify(self, message: dict) -> None:
        self._post(message, CALL_SECONDS).close()

    def close(self) -> None:
        if not self._session:
            return
        request = Request(  # nosec B310 -- a URL the user configured
            self._url, method="DELETE",
            headers={"Mcp-Session-Id": self._session, **self._headers},
        )
        try:
            urlopen(request, timeout=5).close()  # nosec B310
        except (URLError, OSError, ValueError):
            pass


class Server:
    """One configured MCP server: started, its tools listed, called."""

    def __init__(self, name: str, spec: dict) -> None:
        self.name = name
        self.spec = spec
        # The program a url server is started with, while it runs.
        self._process: Optional[subprocess.Popen] = None
        self._started: list[str] = []
        self.state = OFF
        self.why = ""
        self.tools: list[dict] = []
        self.title = ""
        # What it said on its way down, when it failed.
        self.said = ""
        self._link: Optional[_Link] = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    @property
    def remote(self) -> bool:
        return "url" in self.spec

    def start(self) -> None:
        """Connect and list its tools. Sets state and why; never raises.
        Stopped while it starts, it closes what it opened and stays off."""

        self.state = STARTING
        self.why = ""
        link: Optional[_Link] = None
        try:
            begin = self.spec.get("start") if self.remote else None
            if begin:
                self._launch(begin if isinstance(begin, dict)
                             else _command(str(begin)))
            link = (
                _HttpLink(self.spec, self._answer) if self.remote
                else _StdioLink(self.spec, self._answer)
            )
            self._link = link
            hello = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "flash", "version": __version__},
            }
            told = self._first_hello(hello) if begin else \
                self._request("initialize", hello, START_SECONDS)
            info = told.get("serverInfo") or {}
            self.title = str(info.get("title") or info.get("name") or "")
            if isinstance(link, _HttpLink):
                link.version = str(
                    told.get("protocolVersion") or PROTOCOL_VERSION
                )
            link.notify({
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            tools = self._list_tools()
            if self._link is link:
                self.tools = tools
                self.state = READY
                return
        except Exception as exc:  # noqa: BLE001
            # Whatever went wrong, a server that would not start is a
            # failed one, not a Flash that stops.
            if self._link is link:
                self.state = FAILED
                self.why = str(exc) if isinstance(exc, ConnectorError) \
                    else f"{exc.__class__.__name__}: {exc}"
                self.said = link.problem() if link is not None else ""
                if not self.said and self._started:
                    self.said = "\n".join(self._started[-5:])
                self.stop(keep_state=True)
                return
        if link is not None and self._link is not link:
            link.close()

    def _launch(self, begin: dict) -> None:
        """Run the program that serves this url, its output kept for
        when it fails."""

        env = {**os.environ, **{
            str(k): str(v) for k, v in (begin.get("env") or {}).items()}}
        command = str(begin.get("command") or "")
        found = shutil.which(command, path=env.get("PATH")) or command
        cwd = begin.get("cwd")
        try:
            self._process = subprocess.Popen(  # nosec B603
                [found, *[str(a) for a in begin.get("args") or []]],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, env=env,
                cwd=str(Path(cwd).expanduser()) if cwd else None,
                text=True, encoding="utf-8", errors="replace",
                start_new_session=os.name != "nt")
        except OSError as exc:
            raise ConnectorError(f"Could not start {command}: {exc}") \
                from None
        self._started = []
        process = self._process

        def read() -> None:
            for said in process.stdout or ():
                self._started.append(said.rstrip())
                del self._started[:-KEPT_LINES]

        threading.Thread(target=read, daemon=True).start()

    def _first_hello(self, hello: dict) -> dict:
        """Say hello until the program it started answers at the url, or
        it gives up, or the program does."""

        deadline = time.monotonic() + START_SECONDS
        while True:
            process = self._process
            if process is not None and process.poll() is not None:
                last = self._started[-1] if self._started else ""
                raise ConnectorError(
                    f"Its start command stopped (exit {process.returncode})"
                    + (f": {last}" if last else "."))
            try:
                return self._request("initialize", hello, 3.0)
            except ConnectorError:
                if time.monotonic() > deadline:
                    raise
            time.sleep(0.4)

    def stop(self, keep_state: bool = False) -> None:
        link, self._link = self._link, None
        if link is not None:
            link.close()
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
        if not keep_state:
            self.state = OFF
            self.tools = []

    def problem(self) -> str:
        said = self._link.problem() if self._link is not None else self.said
        if not said and self._started:
            said = "\n".join(self._started[-5:])
        return said

    def _request(self, method: str, params: dict, seconds: float) -> dict:
        if self._link is None:
            raise ConnectorError(f"{self.name} is not connected.")
        answer = self._link.request({
            "jsonrpc": "2.0", "id": next(self._ids), "method": method,
            "params": params,
        }, seconds)
        error = answer.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) \
                else error
            raise ConnectorError(f"{self.name}: {message}")
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    def _list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(50):
            found = self._request(
                "tools/list", {"cursor": cursor} if cursor else {},
                START_SECONDS,
            )
            tools.extend(t for t in found.get("tools") or []
                         if isinstance(t, dict) and t.get("name"))
            cursor = found.get("nextCursor")
            if not cursor:
                break
        return tools

    def _answer(self, message: dict) -> Optional[dict]:
        """What Flash says back to a server's own request: yes to a
        ping, no to anything Flash does not offer. None for a
        notification, which has no answer."""

        method = message.get("method")
        if method == "notifications/tools/list_changed":
            threading.Thread(target=self._relist, daemon=True).start()
        if "id" not in message:
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": message["id"], "result": {}}
        return {"jsonrpc": "2.0", "id": message["id"], "error": {
            "code": -32601, "message": f"Flash does not offer {method}.",
        }}

    def _relist(self) -> None:
        try:
            self.tools = self._list_tools()
        except ConnectorError:
            pass

    def call(self, tool: str, arguments: dict) -> dict:
        with self._lock:
            try:
                return self._request("tools/call", {
                    "name": tool, "arguments": arguments,
                }, CALL_SECONDS)
            except ConnectorError as exc:
                # A server that has stopped stays stopped: /mcp reload
                # starts it again.
                if self._link is not None and not self._link.alive():
                    self.state = FAILED
                    self.why = str(exc)
                raise


# --- Every server ------------------------------------------------------

_servers: dict[str, Server] = {}
_lock = threading.Lock()
_begun = threading.Event()
_starting: list[threading.Thread] = []


def _enabled(spec: dict) -> bool:
    return not spec.get("disabled")


def start() -> None:
    """Start every enabled server not already running, each on a thread
    of its own. Returns at once."""

    _begun.set()
    wanted = read_config()
    with _lock:
        for name in list(_servers):
            if name not in wanted or _servers[name].spec != wanted[name]:
                _servers.pop(name).stop()
        for name, spec in wanted.items():
            if name in _servers or not _enabled(spec):
                continue
            server = Server(name, spec)
            _servers[name] = server
            thread = threading.Thread(target=server.start, daemon=True)
            _starting.append(thread)
            thread.start()


def refresh(wait: float = REFRESH_SECONDS) -> list[Server]:
    """Bring the servers up to date, for a model that is missing tools:
    start the ones not running (one added since, one that failed), wait
    a while for them, and have the running ones list their tools again.
    Every configured server, as it stands then."""

    _begun.set()
    with _lock:
        for name in list(_servers):
            if _servers[name].state in (FAILED, OFF):
                _servers.pop(name).stop()
    start()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and any(
        s.state == STARTING for s in servers()
    ):
        time.sleep(0.2)
    for server in _ready():
        server._relist()
    return servers()


def reload() -> None:
    """Stop them all and start what the config lists now."""

    stop_all()
    start()


def stop_all() -> None:
    with _lock:
        servers = list(_servers.values())
        _servers.clear()
    for server in servers:
        server.stop()


atexit.register(stop_all)


def servers() -> list[Server]:
    """Every configured server, running or not, in the config's order."""

    with _lock:
        running = dict(_servers)
    return [
        running.get(name) or Server(name, spec)
        for name, spec in read_config().items()
    ]


def _wait_first() -> None:
    """The first time tools are wanted, start the servers, and give them
    a moment to be ready."""

    if not _begun.is_set():
        if not read_config():
            _begun.set()
            return
        start()
    deadline = time.monotonic() + FIRST_WAIT_SECONDS
    while _starting:
        thread = _starting[0]
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            return
        _starting.pop(0)


def tool_name(server: str, tool: str) -> str:
    """The name the model calls SERVER's TOOL by."""

    return _UNSAFE.sub("_", f"{server}{SEPARATOR}{tool}")[:NAME_CHARS]


def _ready() -> list[Server]:
    with _lock:
        return [s for s in _servers.values() if s.state == READY]


def tool_schemas(taken: frozenset = frozenset()) -> list[dict]:
    """The connected servers' tools, in the shape Flash offers its own,
    leaving out any whose name is TAKEN."""

    _wait_first()
    offered = []
    seen = set(taken)
    for server in _ready():
        for tool in server.tools:
            name = tool_name(server.name, str(tool["name"]))
            if name in seen:
                continue
            seen.add(name)
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            what = str(tool.get("description") or tool.get("title") or "")
            offered.append({"type": "function", "function": {
                "name": name,
                "description": (
                    f"From the {server.name} connector: {what}".strip()
                )[:1024],
                "parameters": schema,
            }})
    return offered


def find(name: str) -> Optional[tuple[Server, dict]]:
    """The server and tool the model's NAME is, if it is one."""

    if SEPARATOR not in name:
        return None
    for server in _ready():
        for tool in server.tools:
            if tool_name(server.name, str(tool["name"])) == name:
                return server, tool
    return None


def read_only(tool: dict) -> bool:
    """Whether its server says it only looks, changing nothing."""

    hints = tool.get("annotations")
    return isinstance(hints, dict) and hints.get("readOnlyHint") is True


def call(server: Server, tool: dict, arguments: Any) -> tuple[str, list]:
    """Call TOOL on SERVER. Returns its text, and the images it gave
    back as bytes."""

    if not isinstance(arguments, dict):
        arguments = {}
    try:
        result = server.call(str(tool["name"]), arguments)
    except ConnectorError as exc:
        return f"Error: {exc}", []
    texts: list[str] = []
    images: list[bytes] = []
    for part in result.get("content") or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            texts.append(str(part.get("text") or ""))
        elif kind == "image":
            try:
                images.append(base64.b64decode(str(part.get("data") or "")))
                texts.append("(an image, attached)")
            except ValueError:
                pass
        elif kind == "resource":
            found = part.get("resource") or {}
            texts.append(str(found.get("text") or found.get("uri") or ""))
        elif kind == "resource_link":
            texts.append(f"{part.get('name') or ''} {part.get('uri') or ''}")
    if not texts and result.get("structuredContent") is not None:
        texts.append(json.dumps(result["structuredContent"], indent=1))
    text = "\n".join(t for t in texts if t.strip()) or "(no output)"
    if len(text) > RESULT_CHARS:
        text = text[:RESULT_CHARS] + "\n(cut off: the rest was too long)"
    if result.get("isError"):
        text = f"Error: {text}"
    return text, images


def listing() -> list[dict]:
    """How each server stands, for /mcp and Settings: never a token."""

    shown = []
    for server in servers():
        spec = server.spec
        where = spec.get("url") or " ".join(
            [str(spec.get("command") or ""),
             *[str(a) for a in spec.get("args") or []]]
        )
        target, begin = target_of(spec)
        shown.append({
            "name": server.name,
            "where": where,
            # For the edit form: what was typed, never a secret's value.
            "target": target,
            "start": begin,
            "remote": server.remote,
            "on": _enabled(spec),
            "state": server.state if _enabled(spec) else OFF,
            "why": server.why,
            "said": server.problem() if server.state == FAILED else "",
            "title": server.title,
            "secrets": sorted(spec.get("env") or spec.get("headers") or {}),
            "tools": [
                {"name": str(t["name"]),
                 "description": str(t.get("description") or "")[:300],
                 "read_only": read_only(t)}
                for t in server.tools
            ],
        })
    return shown


def add(name: str, target: str, env: Optional[dict] = None,
        begin: str = "") -> None:
    """Add a server, or replace the one of that name, and start it."""

    name = check_name(name)
    found = read_config()
    found[name] = spec_from(target, env, begin)
    write_config(found)
    start()


def edit(name: str, target: str, env: Optional[dict] = None,
         begin: str = "", rename: str = "") -> None:
    """Change server NAME: what it runs or reaches, its start command,
    and its environment or headers, where ENV gives a key with no value
    to keep the one it has. RENAME gives it a new name."""

    found = read_config()
    if name not in found:
        raise ConnectorError(f"No connector called {name!r}.")
    old = found[name]
    had = {**(old.get("env") or {}), **(old.get("headers") or {}),
           **((old.get("start") or {}).get("env") or {}
              if isinstance(old.get("start"), dict) else {})}
    kept = {k: (v if v else had.get(k, "")) for k, v in (env or {}).items()}
    spec = spec_from(target, kept, begin)
    if old.get("disabled"):
        spec["disabled"] = True
    new = check_name(rename) if rename else name
    if new != name and new in found:
        raise ConnectorError(f"There is a connector called {new!r} already.")
    rebuilt = {}
    for key, value in found.items():
        rebuilt[new if key == name else key] = spec if key == name else value
    write_config(rebuilt)
    start()


def remove(name: str) -> None:
    found = read_config()
    if name not in found:
        raise ConnectorError(f"No connector called {name!r}.")
    del found[name]
    write_config(found)
    start()


def switch(name: str, on: bool) -> None:
    found = read_config()
    if name not in found:
        raise ConnectorError(f"No connector called {name!r}.")
    if on:
        found[name].pop("disabled", None)
    else:
        found[name]["disabled"] = True
    write_config(found)
    start()
