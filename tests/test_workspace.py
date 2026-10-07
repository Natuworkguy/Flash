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

        assert listed == [{
            "name": "This computer", "url": "http://localhost:11434",
            "saved": False, "locked": False,
        }]

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

        assert listed[-1] == {"name": "Current", "saved": False, "url":
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


class TestHostKeys:
    def test_a_key_is_kept_but_never_listed(self):
        added = workspace.add_host("Studio", "10.0.0.5", "s3cret")

        assert added == {"name": "Studio", "url": "http://10.0.0.5:11434",
                         "locked": True}
        listed = workspace.hosts()
        assert listed[-1]["locked"] is True
        assert "s3cret" not in repr(listed)
        assert workspace.api_key("10.0.0.5") == "s3cret"

    def test_the_file_with_keys_is_private(self):
        workspace.add_host("Studio", "10.0.0.5", "s3cret")

        mode = (workspace.store() / "hosts.json").stat().st_mode & 0o777
        if os.name != "nt":
            assert mode == 0o600

    def test_headers_carry_the_key(self):
        workspace.add_host("Studio", "10.0.0.5", "s3cret")

        assert workspace.auth_headers("http://10.0.0.5:11434") == {
            "authorization": "Bearer s3cret",
        }
        assert workspace.client_options("10.0.0.5") == {
            "headers": {"authorization": "Bearer s3cret"},
        }
        # Another host gets nothing, and a client is made as before.
        assert workspace.client_options("10.0.0.9") == {}

    def test_the_environment_key_is_the_fallback(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "from-env")
        workspace.add_host("Studio", "10.0.0.5", "s3cret")

        assert workspace.api_key("10.0.0.5") == "s3cret"
        assert workspace.api_key("10.0.0.9") == "from-env"

    def test_adding_again_without_a_key_drops_it(self):
        workspace.add_host("Studio", "10.0.0.5", "s3cret")
        workspace.add_host("Studio", "10.0.0.5")

        assert workspace.api_key("10.0.0.5") == ""
        assert workspace.hosts()[-1]["locked"] is False

    def test_this_computer_can_have_a_key(self):
        workspace.add_host("", "localhost:11434", "s3cret")

        listed = workspace.hosts()
        assert [h["name"] for h in listed] == ["This computer"]
        assert listed[0]["locked"] is True
        assert workspace.api_key("127.0.0.1:11434") == "s3cret"

    def test_model_lookups_send_it(self, monkeypatch):
        from flash import sysprompt

        workspace.add_host("Studio", "10.0.0.5", "s3cret")
        seen = []

        def urlopen(request, timeout):
            seen.append(request.get_header("Authorization"))
            raise OSError("not really there")

        monkeypatch.setattr(sysprompt.urllib.request, "urlopen", urlopen)
        sysprompt._show("http://10.0.0.5:11434", "llama3.1")

        assert seen == ["Bearer s3cret"]

    def test_a_key_has_no_spaces(self):
        with pytest.raises(WorkspaceError):
            workspace.add_host("Studio", "10.0.0.5", "two words")

    def test_a_server_behind_a_key(self):
        class Guarded(BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.headers.get("Authorization") == "Bearer right"
                self.send_response(200 if ok else 401)
                self.end_headers()
                self.wfile.write(b'{"version":"0.35.0"}' if ok else b"no")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Guarded)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"127.0.0.1:{server.server_address[1]}"
        try:
            assert workspace.host_state(url) == "refused"
            workspace.add_host("Guarded", url, "wrong")
            assert workspace.host_state(url) == "refused"
            workspace.add_host("Guarded", url, "right")
            assert workspace.host_state(url) == "up"
            health = {
                h["name"]: h for h in workspace.hosts_with_health()
            }
        finally:
            server.shutdown()
            server.server_close()

        assert health["Guarded"]["up"] is True
        assert health["Guarded"]["refused"] is False


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

    def test_a_project_takes_in_more_folders(self, tmp_path):
        main, api, docs = (tmp_path / n for n in ("web", "api", "docs"))
        for folder in (main, api, docs):
            folder.mkdir()

        made = workspace.create_project(
            "App", str(main), folders=[str(api), "", str(api), str(main)],
        )
        # Blanks, repeats and the main folder itself are left out.
        assert made.folders == [str(api.resolve())]
        assert workspace.project(made.id).folders == [str(api.resolve())]

        workspace.update_project(made.id, folders=[str(docs)])
        assert workspace.project(made.id).folders == [str(docs.resolve())]
        # The main folder moved to one it had besides: it is not twice.
        workspace.update_project(made.id, path=str(docs))
        found = workspace.project(made.id)
        assert found.path == str(docs.resolve()) and found.folders == []

    def test_more_folders_have_to_exist_and_are_capped(self, tmp_path):
        with pytest.raises(WorkspaceError, match="not a folder"):
            workspace.create_project(
                "x", str(tmp_path), folders=[str(tmp_path / "missing")],
            )
        many = []
        for n in range(workspace.MAX_FOLDERS + 1):
            (tmp_path / f"f{n}").mkdir()
            many.append(str(tmp_path / f"f{n}"))
        with pytest.raises(WorkspaceError, match="at most"):
            workspace.create_project("x", str(tmp_path), folders=many)

    def test_a_project_from_before_folders_reads_as_none(self, tmp_path):
        made = workspace.create_project("Old", str(tmp_path))
        workspace._write("projects.json", [{
            "id": made.id, "name": "Old", "path": made.path,
        }])

        assert workspace.project(made.id).folders == []

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


class TestThisComputer:
    @pytest.mark.parametrize("current", [
        "http://127.0.0.1:11434", "127.0.0.1:11434", "http://[::1]:11434",
        "localhost:11434",
    ])
    def test_a_loopback_host_is_this_computer_not_another(self, current):
        listed = workspace.hosts(current)

        assert [h["name"] for h in listed] == ["This computer"]
        assert workspace.listed_url(current) == workspace.LOCAL_HOST

    def test_another_port_is_another_server(self):
        listed = workspace.hosts("127.0.0.1:9000")

        assert [h["name"] for h in listed] == ["This computer", "Current"]

    def test_only_saved_hosts_are_marked_saved(self):
        workspace.add_host("Studio", "10.0.0.5")

        saved = {h["name"]: h["saved"] for h in workspace.hosts("10.0.0.9")}

        assert saved == {
            "This computer": False, "Studio": True, "Current": False,
        }

    def test_this_computer_is_never_saved_under_another_name(self):
        workspace.add_host("Loopback", "127.0.0.1:11434")

        assert [h["name"] for h in workspace.hosts()] == ["This computer"]

    def test_removing_matches_however_it_was_written(self):
        workspace.add_host("Studio", "http://10.0.0.5:11434/")

        assert workspace.remove_host("10.0.0.5")
        assert [h["name"] for h in workspace.hosts()] == ["This computer"]
