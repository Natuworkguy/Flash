"""A tunnel: Flash's web UI on a public https address, through a service
that forwards it to this computer, to open from anywhere.

Each service is a program Flash starts and stops (cloudflared, ssh,
tailscale, ngrok), told to forward to the web server on this machine,
and the public address is read from what it prints. Flash then answers
requests to that address too, as well as to this machine's own names.

This puts Flash on the internet. Anyone with the link, token and all,
can use it as the user, and Flash can run commands, change files and
send messages: the link is as good as the user's password. Off until
turned on, and never turned on by itself: it stops when Flash stops.
"""

import atexit
import re
import shutil
import subprocess  # nosec B404
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class Service:
    """A tunnel service: what it is called, the program it needs, how it
    is started (with {port}), and how its public address looks."""

    name: str
    title: str
    about: str
    program: str
    argv: tuple
    address: str
    install: str


SERVICES = {
    service.name: service for service in (
        Service(
            "cloudflare", "Cloudflare quick tunnel",
            "Free, with no account. A new random address each time.",
            "cloudflared",
            ("cloudflared", "tunnel", "--no-autoupdate", "--url",
             "http://127.0.0.1:{port}"),
            r"https://[-a-z0-9]+\.trycloudflare\.com",
            "brew install cloudflared, or see "
            "developers.cloudflare.com/cloudflare-one/connections/"
            "connect-networks/downloads",
        ),
        Service(
            "localhost.run", "localhost.run",
            "Free, with nothing to install: it goes over ssh. A new "
            "random address each time.",
            "ssh",
            ("ssh", "-T", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
             "-R", "80:127.0.0.1:{port}", "nokey@localhost.run"),
            r"https://[-a-z0-9]+\.lhr\.life",
            "ssh comes with macOS and Linux, and with Windows 10 and later",
        ),
        Service(
            "tailscale", "Tailscale Funnel",
            "Your own tailnet's address, the same each time. Needs "
            "Tailscale signed in, with Funnel allowed for the tailnet.",
            "tailscale",
            ("tailscale", "funnel", "{port}"),
            r"https://[-a-z0-9.]+\.ts\.net",
            "tailscale.com/download",
        ),
        Service(
            "ngrok", "ngrok",
            "Needs an ngrok account, with its authtoken added "
            "(ngrok config add-authtoken).",
            "ngrok",
            ("ngrok", "http", "127.0.0.1:{port}", "--log", "stdout"),
            r"https://[-a-z0-9.]+\.ngrok(?:-free)?\.(?:app|dev|io)",
            "ngrok.com/download",
        ),
    )
}
# How long a service has to say its address before it is given up on.
START_SECONDS = 45.0
# What a tunnel that stopped is remembered as having said, for the why.
SAID_LINES = 40

OFF = "off"
STARTING = "starting"
ON = "on"
FAILED = "failed"


class TunnelError(Exception):
    """A tunnel that could not be started, and why."""


def services() -> list[dict]:
    """Every service, for the picker: whether its program is here."""

    return [
        {"name": s.name, "title": s.title, "about": s.about,
         "installed": shutil.which(s.program) is not None,
         "install": s.install}
        for s in SERVICES.values()
    ]


class Tunnel:
    """The one tunnel a web UI has: started, watched, stopped."""

    def __init__(self) -> None:
        self.state = OFF
        self.service = ""
        self.url = ""
        self.why = ""
        self.since = 0.0
        self._process: Optional[subprocess.Popen] = None
        self._said: list[str] = []
        self._lock = threading.Lock()
        self._found = threading.Event()
        # Never left running after Flash: its program would go on putting
        # this computer on the internet with nothing to stop it.
        atexit.register(self.stop)

    @property
    def host(self) -> str:
        """The public name requests come in under, while it is on."""

        if self.state != ON:
            return ""
        return (urlparse(self.url).hostname or "").lower()

    def status(self) -> dict:
        return {
            "state": self.state, "service": self.service, "url": self.url,
            "why": self.why, "since": self.since,
        }

    def start(self, name: str, port: int,
              wait: float = START_SECONDS) -> dict:
        """Start the tunnel through service NAME to PORT, and wait for its
        public address. Raises TunnelError when it cannot be had."""

        service = SERVICES.get(str(name or "").strip().lower())
        if service is None:
            raise TunnelError(
                f"No tunnel service called {name!r}. One of: "
                + ", ".join(SERVICES) + "."
            )
        program = shutil.which(service.program)
        if program is None:
            raise TunnelError(
                f"{service.title} needs {service.program}, which is not "
                f"installed: {service.install}."
            )
        self.stop()
        argv = [program, *(str(a).format(port=int(port))
                           for a in service.argv[1:])]
        with self._lock:
            self.state, self.service, self.url, self.why = (
                STARTING, service.name, "", "")
            self._said = []
            self._found.clear()
            try:
                # A fixed program, never through a shell; the port is a
                # number.
                self._process = subprocess.Popen(  # nosec B603
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
            except OSError as exc:
                self.state, self.why = FAILED, str(exc)
                raise TunnelError(
                    f"{service.program} could not start: {exc}"
                ) from None
        threading.Thread(
            target=self._read, args=(self._process, service), daemon=True,
        ).start()
        if not self._found.wait(wait) or self.state != ON:
            why = self.why or (
                f"{service.title} gave no address in {int(wait)} seconds"
            )
            self.stop()
            self.state, self.why = FAILED, why
            raise TunnelError(why)
        return self.status()

    def _read(self, process: subprocess.Popen, service: Service) -> None:
        pattern = re.compile(service.address, re.IGNORECASE)
        for line in process.stdout or ():
            line = line.strip()
            if not line:
                continue
            self._said = (self._said + [line])[-SAID_LINES:]
            found = pattern.search(line)
            if found and self.state == STARTING and \
                    process is self._process:
                self.url = found.group(0).rstrip("/").lower()
                self.state, self.since = ON, time.time()
                self._found.set()
        process.wait()
        if process is self._process and self.state in (STARTING, ON):
            # It ended by itself: the network, the service, a sign-in.
            last = next((line for line in reversed(self._said)
                         if not line.startswith("+")), "")
            self.state = FAILED
            self.why = f"{service.title} stopped" + (
                f": {last}" if last else ".")
            self.url = ""
            self._found.set()

    def stop(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            self.state, self.url, self.since = OFF, "", 0.0
            self._found.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
