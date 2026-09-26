"""Tests for what the web UI keeps: hosts, projects, and chats."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from flash import workspace
from flash.workspace import WorkspaceError


class TestHosts:
    @pytest.mark.parametrize("given, url", [
        ("10.0.0.5", "http://10.0.0.5:11434"),
        ("10.0.0.5:9000", "http://10.0.0.5:9000"),
        ("http://gpu.local:11434/", "http://gpu.local:11434"),
        ("https://ollama.example.com", "https://ollama.example.com:11434"),
        ("[::1]:11434", "http://[::1]:11434"),
    ])
    def test_addresses_are_normalized(self, given, url):
        assert workspace.normalize_host(given) == url

    @pytest.mark.parametrize("given", [
        "", "ftp://x", "http://", "http://host:11434/api", "http://h?q=1",
    ])
    def test_bad_addresses(self, given):
        with pytest.raises(WorkspaceError):
            workspace.normalize_host(given)

    def test_this_computer_is_always_first(self):
        listed = workspace.hosts()

        assert listed == [
            {"name": "This computer", "url": "http://localhost:11434"}
        ]

    def test_add_and_remove(self):
        workspace.add_host("Studio", "10.0.0.5")
        workspace.add_host("", "gpu.local:9000")

        listed = workspace.hosts()

        assert [h["name"] for h in listed] == [
            "This computer", "Studio", "gpu.local",
        ]
        assert workspace.remove_host("10.0.0.5")
        assert not workspace.remove_host("10.0.0.5")
        assert [h["url"] for h in workspace.hosts()][-1] == (
            "http://gpu.local:9000"
        )

    def test_adding_again_renames_rather_than_repeats(self):
        workspace.add_host("Old", "10.0.0.5")
        workspace.add_host("New", "http://10.0.0.5:11434")

        assert [h["name"] for h in workspace.hosts()] == [
            "This computer", "New",
        ]

    def test_an_unsaved_current_host_is_shown(self):
        listed = workspace.hosts("192.168.1.9:11434")

        assert listed[-1] == {"name": "Current", "url":
                              "http://192.168.1.9:11434"}

    def test_health(self):
        class Ollama(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"version":"0.12"}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        up = f"127.0.0.1:{server.server_address[1]}"
        try:
            workspace.add_host("Up", up)
            workspace.add_host("Down", "127.0.0.1:1")

            health = {
                h["name"]: h["up"] for h in workspace.hosts_with_health()
            }
        finally:
            server.shutdown()
            server.server_close()

        assert health["Up"] is True
        assert health["Down"] is False


class TestProjects:
    def test_create_find_update_delete(self, tmp_path):
        made = workspace.create_project("", str(tmp_path), "Use pytest.")

        assert made.name == tmp_path.name
        assert workspace.project(made.id).instructions == "Use pytest."

        workspace.update_project(made.id, name="Renamed",
                                 instructions="Use uv.")
        assert workspace.project(made.id).name == "Renamed"
        assert workspace.project(made.id).instructions == "Use uv."

        assert workspace.delete_project(made.id)
        assert workspace.project(made.id) is None
        assert tmp_path.is_dir()  # the folder itself is never touched

    def test_the_folder_has_to_exist(self, tmp_path):
        with pytest.raises(WorkspaceError, match="not a folder"):
            workspace.create_project("x", str(tmp_path / "missing"))
        with pytest.raises(WorkspaceError, match="not a folder"):
            workspace.create_project("x", "")

    def test_home_is_expanded(self):
        made = workspace.create_project("Home", "~")

        # Resolved on both sides: a Windows runner's home can come back
        # as an 8.3 short name that resolve() expands.
        assert made.path == str(Path(os.path.expanduser("~")).resolve())

    def test_instructions_are_capped(self, tmp_path):
        with pytest.raises(WorkspaceError, match="limited"):
            workspace.create_project(
                "x", str(tmp_path), "x" * (workspace.MAX_INSTRUCTIONS + 1)
            )

    def test_folder_suggestions(self, tmp_path):
        for name in ("alpha", "alps", "beta", ".hidden"):
            (tmp_path / name).mkdir()
        (tmp_path / "also-a-file").write_text("x")

        assert workspace.folder_suggestions(f"{tmp_path}/al") == [
            str(tmp_path / "alpha"), str(tmp_path / "alps"),
        ]
        assert str(tmp_path / ".hidden") not in (
            workspace.folder_suggestions(f"{tmp_path}/")
        )
        assert workspace.folder_suggestions(f"{tmp_path}/.h") == [
            str(tmp_path / ".hidden")
        ]
        assert workspace.folder_suggestions("/no/such/place/") == []


class TestChats:
    def test_save_load_delete(self):
        workspace.save_chat({"id": "0123abcd", "title": "t", "log": []})

        assert [c["id"] for c in workspace.load_chats()] == ["0123abcd"]

        workspace.delete_chat("0123abcd")
        assert workspace.load_chats() == []

    def test_an_id_cannot_become_a_path(self):
        workspace.save_chat({"id": "../../escape", "title": "t"})

        assert workspace.load_chats() == []
        assert not (workspace.store().parent / "escape.json").exists()

    def test_a_broken_file_is_skipped(self):
        folder = workspace.store() / "chats"
        folder.mkdir(parents=True)
        (folder / "0123abcd.json").write_text("{broken")

        assert workspace.load_chats() == []
