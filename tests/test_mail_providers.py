"""Mail providers: an extension that reads and sends email for addresses
with no IMAP or SMTP, through a program Flash runs for each thing."""

import json
import textwrap
from email.message import EmailMessage

import pytest

from flash import extensions, mail, triggers, vault, web

PROGRAM = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path

    state_path = Path(os.environ["FLASH_EXTENSION_DIR"]) / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    request = json.loads(sys.stdin.read())
    state.setdefault("calls", []).append(
        {k: v for k, v in request.items() if k != "password"})
    action = request["action"]

    def answer(found):
        state_path.write_text(json.dumps(state))
        print("noise before the answer")
        print(json.dumps(found))
        sys.exit(0)

    if action == "signin":
        if request.get("password") != "the account password":
            answer({"ok": False, "error": "The email or password is wrong."})
        if request.get("code") != "123456":
            answer({"ok": True, "needs": "code",
                    "text": "Enter the code from your authenticator app.",
                    "url": "javascript:alert(1)"})
        state["signed_in"] = request["address"]
        answer({"ok": True, "done": True, "text": "Signed in."})
    if action == "signout":
        state["signed_in"] = ""
        answer({"ok": True, "text": "Signed out."})
    if not state.get("signed_in"):
        answer({"ok": False, "error": "Sign in again."})
    if action == "test":
        answer({"ok": True, "text": "Connected: 1 unread."})
    if action == "inbox":
        answer({"ok": True, "messages": [
            {"uid": "older-Id==", "from": "Ann <ann@acme.example>",
             "subject": "Lunch", "at": 100, "unread": False,
             "snippet": "Noon?", "attachments": 0, "link": "https://x"},
            {"uid": "newer_Id-2", "from": "Bo <bo@acme.example>",
             "subject": "Plans", "at": 200, "unread": True,
             "snippet": "  Friday   then  ", "attachments": 1},
            {"uid": "bad uid!", "subject": "never shown", "at": 300},
        ]})
    if action == "read":
        answer({"ok": True, "message": {
            "from": "Bo <bo@acme.example>", "to": request["address"],
            "subject": "Plans", "body": "Friday then.",
            "message_id": "<m1@acme.example>", "unread": True,
            "attachments": ["plan.pdf"]}})
    if action == "send":
        state["sent"] = request["raw"]
        answer({"ok": True, "text": "bo@acme.example"})
    if action == "trash":
        answer({"ok": True, "folder": "Trash"})
    if action == "latest":
        answer({"ok": True, "next": 200})
    answer({"ok": False, "error": "no such action"})
''')


@pytest.fixture
def provider(tmp_path, monkeypatch):
    root = tmp_path / "acme-mail"
    root.mkdir()
    (root / "provider.py").write_text(PROGRAM)
    (root / extensions.MANIFEST).write_text(json.dumps({
        "name": "acme-mail",
        "mail": [{"name": "acme", "label": "Acme Mail",
                  "domains": ["acme.example", "Acmemail.example"],
                  "run": ["python", "./provider.py"], "timeout": 30}],
    }))
    found = extensions.load(root)
    monkeypatch.setattr(extensions, "installed", lambda: [found])
    return root


def state(root):
    return json.loads((root / "state.json").read_text())


def connect(address="me@acme.example"):
    first = mail.connect_provider(address, "the account password",
                                  restart=True)
    assert first["needs"] == "code"
    return mail.connect_provider(address, "the account password",
                                 code="123456")


def test_a_provider_web_inbox_opens_a_message_with_no_link(
        provider, monkeypatch):
    connect()
    [ext] = extensions.installed()
    monkeypatch.setattr(ext.mail[0], "web", "https://mail.acme.example/")

    found = {m["uid"]: m for m in mail.inbox()}

    assert found["newer_Id-2"]["link"] == "https://mail.acme.example/"
    assert found["newer_Id-2"]["opens"] == "inbox"
    assert found["older-Id=="]["link"] == "https://x"


def test_a_provider_link_that_is_not_a_web_address_is_dropped(provider):
    account = {"address": "me@acme.example", "provider": "acme"}

    assert mail.opener("", account, "javascript:alert(1)") == {}
    assert mail.opener("", account, "https://mail.acme.example/m/1") == {
        "link": "https://mail.acme.example/m/1", "service": "Acme Mail",
        "opens": "message"}


def test_the_manifest_takes_mail_providers(provider):
    [ext] = extensions.installed()
    [one] = ext.mail
    assert (one.name, one.label, one.timeout) == ("acme", "Acme Mail", 30)
    assert one.domains == ["acme.example", "acmemail.example"]
    assert any("Acme Mail" in line for line in ext.contents())


@pytest.mark.parametrize("entry", [
    {"name": "x", "domains": [], "run": ["python", "p.py"]},
    {"name": "x", "domains": ["not a domain"], "run": ["python", "p.py"]},
    {"name": "Bad Name", "domains": ["a.example"], "run": ["python", "p.py"]},
    {"name": "x", "domains": ["a.example"]},
    {"name": "x", "domains": ["a.example"], "run": ["python", "p.py"],
     "web": "http://mail.a.example"},
])
def test_a_bad_mail_provider_is_refused(tmp_path, entry):
    (tmp_path / extensions.MANIFEST).write_text(
        json.dumps({"name": "bad", "mail": [entry]}))
    with pytest.raises(extensions.ExtensionError):
        extensions.load(tmp_path)


def test_another_extension_cannot_claim_the_same_addresses(provider,
                                                           tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / extensions.MANIFEST).write_text(json.dumps({
        "name": "other",
        "mail": [{"name": "other", "domains": ["acme.example"],
                  "run": ["python", "./p.py"]}]}))
    found = extensions.clashes(extensions.load(other), frozenset(),
                               frozenset())
    assert any("acme.example" in line for line in found)


def test_signing_in_asks_for_what_it_needs_and_keeps_no_password(provider):
    assert mail.provider_for("Me@ACME.example") == {
        "name": "acme", "label": "Acme Mail"}
    assert mail.provider_for("me@elsewhere.example") is None
    done = connect()
    assert done["done"] is True and "Signed in" in done["text"]
    kept = json.loads(mail._path().read_text())
    assert kept["accounts"] == [{"address": "me@acme.example",
                                 "provider": "acme"}]
    assert "the account password" not in mail._path().read_text()
    assert all("password" not in call for call in state(provider)["calls"])
    assert mail.configured() and mail.addresses() == ["me@acme.example"]
    [shown] = mail.settings()["accounts"]
    assert shown["provider_label"] == "Acme Mail" and shown["has_password"]
    assert mail.settings()["providers"][0]["domains"] == [
        "acme.example", "acmemail.example"]


def test_a_wrong_password_is_said_and_nothing_is_connected(provider):
    with pytest.raises(mail.MailError, match="password is wrong"):
        mail.connect_provider("me@acme.example", "nope")
    assert not mail.configured()


def test_an_address_no_extension_signs_in_to_is_refused(provider):
    with pytest.raises(mail.MailError, match="No installed extension"):
        mail.connect_provider("me@elsewhere.example", "x")


def test_reading_sending_and_the_trash_go_through_the_provider(provider):
    connect()
    found = mail.inbox()
    assert [m["uid"] for m in found] == ["newer_Id-2", "older-Id=="]
    assert found[0]["snippet"] == "Friday then"
    assert found[0]["account"] == "me@acme.example"
    assert found[1]["link"] == "https://x"
    assert (found[1]["service"], found[1]["opens"]) == (
        "Acme Mail", "message")
    # One with no link of its own has nowhere to open, with no web inbox.
    assert found[0]["link"] == "" and found[0]["service"] == ""
    message = mail.read("newer_Id-2")
    assert message["body"] == "Friday then." and message["uid"] == "newer_Id-2"
    assert message["attachments"] == ["plan.pdf"]
    assert mail.trash("newer_Id-2") == "Trash"
    assert mail.test() == "Connected: 1 unread."
    reply = mail.compose(body="Yes, Friday.", reply_to=message)
    assert mail.send(reply) == "bo@acme.example"
    sent = state(provider)["sent"]
    assert "In-Reply-To: <m1@acme.example>" in sent and "Yes, Friday." in sent
    with pytest.raises(mail.MailError):
        mail.read("bad uid!")


def test_new_mail_is_what_came_after_the_latest_time(provider):
    connect()
    assert mail.next_uids() == {"me@acme.example": 200}
    assert triggers._new_since({"uid": "abc", "at": 201}, 200)
    assert not triggers._new_since({"uid": "abc", "at": 200}, 200)
    assert triggers._new_since({"uid": "57", "at": 0}, 57)


def test_disconnecting_signs_out(provider):
    connect()
    mail.forget("me@acme.example")
    assert state(provider)["signed_in"] == ""
    assert not mail.configured()


def test_an_account_whose_extension_went_says_so(provider, monkeypatch):
    connect()
    monkeypatch.setattr(extensions, "installed", lambda: [])
    [shown] = mail.settings()["accounts"]
    assert shown["provider_missing"] is True
    with pytest.raises(mail.MailError, match="not installed"):
        mail.inbox(account="me@acme.example")
    mail.forget("me@acme.example")
    assert not mail.configured()


def test_saving_without_a_password_keeps_a_provider_account(provider):
    connect()
    mail.save("me@acme.example")
    assert mail._raw_accounts() == [{"address": "me@acme.example",
                                     "provider": "acme"}]
    mail.save("me@acme.example", "an app password", "127.0.0.1:1143",
              "127.0.0.1:1025")
    assert mail._raw_accounts()[0]["imap_host"] == "127.0.0.1"
    assert "provider" not in mail._raw_accounts()[0]


def test_the_web_signs_in_a_step_at_a_time(provider):
    first = web._email_command("email-signin", {
        "address": "me@acme.example", "password": "the account password",
        "restart": True})
    assert first["needs"] == "code" and not first["done"]
    # Only a web page is passed on to open.
    assert "url" not in first
    done = web._email_command("email-signin", {
        "address": "me@acme.example", "password": "the account password",
        "code": "123456"})
    assert done["done"] and done["said"] == "Connected: 1 unread."
    assert done["email"]["accounts"][0]["provider"] == "acme"
    with pytest.raises(ValueError, match="password is wrong"):
        web._email_command("email-signin", {
            "address": "me@acme.example", "password": "wrong"})


def test_an_extension_keeps_its_secrets_apart_and_out_of_sight():
    session = json.dumps({"access": "an-access-token-0123456789",
                          "short": "abc"})
    vault.keep("acme", "session:me@acme.example", session)
    assert vault.kept("acme", "session:me@acme.example") == session
    assert vault.kept("other", "session:me@acme.example") is None
    # Not one of the secrets the model can name, use or expand.
    assert vault.names() == []
    assert "an-access-token" not in vault.prompt_block()
    assert vault.expand("{{secret:session:me@acme.example}}") != session
    said = vault.scrub("the token was an-access-token-0123456789, abc")
    assert "an-access-token-0123456789" not in said and "abc" in said
    assert vault.unkeep("acme")
    assert vault.kept("acme", "session:me@acme.example") is None
    with pytest.raises(vault.VaultError):
        vault.keep("Bad Owner", "x", "y")


def test_compose_from_a_provider_account(provider):
    connect()
    message = mail.compose("bo@acme.example", "Hi", "Hello there.")
    assert isinstance(message, EmailMessage)
    assert message["From"] == "me@acme.example"
