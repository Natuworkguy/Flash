"""The setup window's server, for the installer: serves the page in DIRECTORY on this computer only, and the
status.json the installer keeps writing there. POST /open starts what open.json names (the app just installed),
POST /close stops the server. It stops by itself an hour after it starts, and deletes its folder when it stops.

    python serve.py DIRECTORY        prints the port it took, then serves until closed
"""

import json
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # quiet: the installer shows no logs
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):  # noqa: N802
        folder = Path(self.directory)
        if self.path == "/open":
            try:
                command = json.loads((folder / "open.json").read_text(encoding="utf-8"))["cmd"]
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # nosec B603
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self.send_response(204)
        self.end_headers()
        if self.path in ("/open", "/close"):
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def main() -> None:
    folder = Path(sys.argv[1]).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(folder)))
    print(server.server_address[1], flush=True)
    timer = threading.Timer(3600, server.shutdown)
    timer.daemon = True
    timer.start()
    server.serve_forever()
    # Its folder was made for this one setup: once the page is done with it, it goes.
    time.sleep(3)
    shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
