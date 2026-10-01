# pylint: disable=C0114,C0115,C0116

import imaplib
import json
import os
import stat
import sys
from email.message import EmailMessage

import pytest

from flash import mail, sparks, tools, web
from flash.theme import answer_from


def _message(sender, subject, text="", html="", attach="", msgid=""):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "me@example.com"
    message["Subject"] = subject
    message["Date"] = "Thu, 01 Oct 2026 09:12:00 +0000"
    if msgid:
        message["Message-ID"] = msgid
    if text:
        message.set_content(text)
    if html:
        if text:
            message.add_alternative(html, subtype="html")
        else:
            message.set_content(html, subtype="html")
    if attach:
        message.add_attachment(
            b"%PDF", maintype="application", subtype="pdf", filename=attach,
        )
    return message.as_bytes()


class FakeIMAP:
    """A mailbox of three messages; the second and third unread."""

    instances: list = []
    password = "abcdefghijklmnop"

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host, self.port = host, port
        self.selected = None
        self.readonly = None
        self.literal = None
        self.searches = []
        self.fetches = []
        self.closed = False
        self.messages = {
            b"7": (b"\\Seen", _message(
                "Ann <ann@example.com>", "Lunch?", "Lunch on Friday?",
            )),
            b"8": (b"", _message(
                "Shop <news@shop.example>", "50% off",
                html="<style>p{}</style><p>Big <b>sale</b> today</p>",
                attach="receipt.pdf",
            )),
            b"9": (b"", _message(
                "Bo <bo@example.com>", "Contract", "Can you sign by Monday?",
                msgid="<contract-1@example.com>",
            )),
        }
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        if password != FakeIMAP.password:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid")
        self.user = user

    def select(self, mailbox, readonly=False):
        self.selected, self.readonly = mailbox, readonly
        if mailbox != '"INBOX"':
            return "NO", [b"no such folder"]
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searches.append(args)
            uids = sorted(self.messages)
            if "UNSEEN" in args:
                uids = [u for u in uids if not self.messages[u][0]]
            if "TEXT" in args:
                words = args[-1].strip('"').lower().encode()
                uids = [
                    u for u in uids if words in self.messages[u][1].lower()
                ]
            return "OK", [b" ".join(uids)]
        if command == "FETCH":
            self.fetches.append(args)
            out = []
            wanted = args[0] if isinstance(args[0], bytes) \
                else args[0].encode()
            for uid in wanted.split(b","):
                flags, raw = self.messages[uid]
                out.append((
                    b"1 (UID " + uid + b" FLAGS (" + flags + b") BODY[] {9}",
                    raw,
                ))
                out.append(b")")
            return "OK", out
        raise AssertionError(command)

    def close(self):
        self.closed = True

    def logout(self):
        pass


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port
        self.tls = port == 465

    def starttls(self, context=None):
        self.tls = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        assert password == FakeIMAP.password

    def send_message(self, message):
        assert self.tls
        FakeSMTP.sent.append(message)


@pytest.fixture
def server(monkeypatch):
    FakeIMAP.instances = []
    FakeSMTP.sent = []
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail.save("me@gmail.com", "abcd efgh ijkl mnop")
    return FakeIMAP


# --- Settings ------------------------------------------------------------


def test_nothing_is_set_up_at_first():
    assert not mail.configured()
    assert mail.settings() == {"accounts": [], "configured": False}
    with pytest.raises(mail.MailError, match="not set up"):
        mail.inbox()


def test_a_known_provider_gets_its_servers():
    shown = mail.save("me@gmail.com", "pw")["accounts"][0]

    assert shown["imap"] == "imap.gmail.com:993"
    assert shown["smtp"] == "smtp.gmail.com:465"
    assert shown["help"] == "https://myaccount.google.com/apppasswords"
    assert mail.servers_for("x@example.org") == (
        "imap.example.org", 993, "smtp.example.org", 465,
    )


def test_servers_can_be_set_by_hand():
    shown = mail.save("me@work.example", "pw", "mail.work.example",
                      "smtp.work.example:587")["accounts"][0]

    assert shown["imap"] == "mail.work.example:993"
    assert shown["smtp"] == "smtp.work.example:587"


def test_the_password_is_never_shown_and_kept_private():
    shown = mail.save("me@gmail.com", "abcd efgh ijkl mnop")["accounts"][0]

    assert "password" not in shown and shown["has_password"]
    assert mail._raw_accounts()[0]["password"] == "abcdefghijklmnop"
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(mail._path()).st_mode)
        assert mode == 0o600


def test_no_password_keeps_the_saved_one_for_the_same_address():
    mail.save("me@gmail.com", "first")
    mail.save("me@gmail.com", None, "", "")
    assert mail._raw_accounts()[0]["password"] == "first"

    mail.forget()
    mail.save("other@gmail.com", None)
    assert not mail.configured()


def test_an_address_must_be_one():
    with pytest.raises(mail.MailError, match="not an email address"):
        mail.save("nobody", "pw")


def test_forgetting_disconnects():
    mail.save("me@gmail.com", "pw")
    mail.forget()
    assert not mail.configured()


# --- Reading -------------------------------------------------------------


def test_the_inbox_lists_newest_first_without_marking_anything(server):
    found = mail.inbox()

    assert [m["uid"] for m in found] == ["9", "8", "7"]
    assert found[0]["from"] == "Bo <bo@example.com>"
    assert found[0]["subject"] == "Contract"
    assert found[0]["unread"] and not found[2]["unread"]
    assert found[0]["snippet"] == "Can you sign by Monday?"
    # HTML comes as its words, styles left out; attachments are counted.
    assert found[1]["snippet"] == "Big sale today"
    assert found[1]["attachments"] == 1
    box = server.instances[-1]
    assert box.readonly is True
    assert "BODY.PEEK" in box.fetches[0][1]
    assert box.closed


def test_only_unread_and_matching(server):
    assert [m["uid"] for m in mail.inbox(unread=True)] == ["9", "8"]
    assert [m["uid"] for m in mail.inbox(query="lunch")] == ["7"]
    assert mail.inbox(unread=True, query="lunch") == []


def test_the_limit_takes_the_newest(server):
    assert [m["uid"] for m in mail.inbox(limit=1)] == ["9"]


def test_reading_one_in_full(server):
    found = mail.read("8")

    assert found["body"] == "Big sale today"
    assert found["attachments"] == ["receipt.pdf"]
    assert found["unread"]
    assert "BODY.PEEK[]" in server.instances[-1].fetches[0][1]


def test_a_missing_folder_or_uid_says_so(server):
    with pytest.raises(mail.MailError, match="no folder"):
        mail.inbox(folder="Nope")
    with pytest.raises(mail.MailError, match="not a message's uid"):
        mail.read("abc")


def test_a_wrong_password_says_to_use_an_app_password(server):
    mail.save("me@gmail.com", "wrong")

    with pytest.raises(mail.MailError, match="app password"):
        mail.inbox()


def test_testing_counts_the_unread(server):
    assert mail.test() == (
        "Connected as me@gmail.com: 2 unread messages in the inbox."
    )


# --- Sending -------------------------------------------------------------


def test_a_reply_threads_and_fills_itself_in(server):
    message = mail.compose(body="Signed.", reply_to=mail.read("9"))

    assert message["To"] == "bo@example.com"
    assert message["Subject"] == "Re: Contract"
    assert message["In-Reply-To"] == "<contract-1@example.com>"
    assert message["From"] == "me@gmail.com"


def test_an_empty_email_or_no_address_is_refused(server):
    with pytest.raises(mail.MailError, match="nothing in it"):
        mail.compose("a@example.com", "Hi", "  ")
    with pytest.raises(mail.MailError, match="no email address"):
        mail.compose("nobody", "Hi", "Hello")


def test_sending_goes_out_over_tls(server):
    sent = mail.send(mail.compose("a@example.com", "Hi", "Hello"))

    assert sent == "a@example.com"
    assert FakeSMTP.sent[0]["Subject"] == "Hi"


# --- The tools -----------------------------------------------------------


def test_check_inbox_lists_with_uids(server):
    said = tools.check_inbox(unread_only=True)

    assert said.startswith("2 emails, newest first.")
    assert "- uid 9 (unread) · " in said
    assert "Contract: Can you sign by Monday?" in said
    assert "[1 attached]" in said


def test_read_email_gives_the_whole_of_one(server):
    said = tools.read_email("9")

    assert "From: Bo <bo@example.com>" in said
    assert said.endswith("Can you sign by Monday?")


def test_send_email_asks_even_in_autonomous_mode(server, monkeypatch):
    monkeypatch.setattr(tools, "NO_COMMAND_CONFIRMATION", True)
    asked = []

    def no(question):
        asked.append(question)
        return "n"

    with answer_from(no):
        said = tools.send_email(reply_to="9", body="Signed.")

    assert said.startswith("Not sent")
    assert FakeSMTP.sent == []
    assert "To: bo@example.com" in asked[0]
    assert "Subject: Re: Contract" in asked[0] and "Signed." in asked[0]


def test_send_email_sends_once_agreed(server):
    with answer_from(lambda question: "y"):
        said = tools.send_email("a@example.com", "Hi", "Hello")

    assert said == "Sent to a@example.com."
    assert len(FakeSMTP.sent) == 1


def test_email_tools_are_offered_only_once_set_up(server):
    names = {t["function"]["name"] for t in tools.turn_tools()}
    assert {"check_inbox", "read_email", "send_email"} <= names

    mail.forget()
    names = {t["function"]["name"] for t in tools.turn_tools()}
    assert "check_inbox" not in names
    assert tools.check_inbox().startswith("Error: Email is not set up")


# --- Sparks --------------------------------------------------------------


def test_a_spark_waits_for_approval_to_send_even_when_autonomous(
    server, monkeypatch,
):
    from tests.test_sparks import FakeClient, _reply

    monkeypatch.setattr(sparks, "get_model_system_prompt", lambda h, m: "")
    monkeypatch.setattr(tools, "MODEL_NAME", "test-model")
    monkeypatch.setattr(sparks, "autonomous", lambda: True)
    made = sparks.add_from("Inbox")
    client = FakeClient([
        _reply("", ("check_inbox", {"unread_only": True})),
        _reply("", ("send_email", {"reply_to": "9", "body": "Signed."})),
    ])

    report = sparks.shift(made.id, client=client)

    offered = [t["function"]["name"] for t in client.calls[0]["tools"]]
    assert {"check_inbox", "read_email", "send_email"} <= set(offered)
    assert report.approval
    waiting = sparks.find(made.id)
    assert waiting.pending["label"] == "Reply to email 9"
    assert "Signed." in waiting.pending["detail"]
    assert FakeSMTP.sent == []
    assert report.steps[0].startswith("CheckInbox(unread")


def test_the_settings_page_connects_and_disconnects(server):
    session = web.Session()
    mail.forget()

    got = web.command(session, {
        "name": "email-save", "address": "me@gmail.com",
        "password": FakeIMAP.password,
    })
    assert got["email"]["configured"]
    assert got["said"].startswith("Connected as me@gmail.com")

    # A blank password keeps the saved one.
    again = web.command(session, {
        "name": "email-save", "address": "me@gmail.com", "password": "",
    })
    assert again["email"]["configured"]

    with pytest.raises(ValueError, match="app password"):
        web.command(session, {
            "name": "email-save", "address": "me@gmail.com",
            "password": "wrong",
        })

    with pytest.raises(ValueError, match="which address"):
        web.command(session, {"name": "email-forget"})
    gone = web.command(session, {
        "name": "email-forget", "address": "me@gmail.com",
    })
    assert not gone["email"]["configured"]


# --- Links ---------------------------------------------------------------


def test_a_gmail_link_opens_that_message_in_that_account():
    account = {"address": "me@gmail.com", "imap_host": "imap.gmail.com"}

    url = mail.link("<contract-1@example.com>", account)

    assert url == (
        "https://mail.google.com/mail/?authuser=me@gmail.com"
        "#search/rfc822msgid%3Acontract-1%40example.com"
    )


def test_other_mail_gets_a_link_for_the_mail_app():
    account = {"address": "me@icloud.com"}

    assert mail.link("<a+b/c@mx.example>", account) == (
        "message:%3Ca%2Bb%2Fc@mx.example%3E"
    )


def test_a_message_with_no_id_has_no_link():
    assert mail.link("", {"address": "me@gmail.com"}) == ""


def test_the_inbox_and_a_read_carry_links(server):
    found = {m["uid"]: m for m in mail.inbox()}

    assert found["9"]["link"].endswith(
        "rfc822msgid%3Acontract-1%40example.com"
    )
    assert mail.read("9")["link"] == found["9"]["link"]


def test_the_tools_hand_the_links_on(server):
    listed = tools.check_inbox()
    read = tools.read_email("9")

    assert "  link: https://mail.google.com/mail/" in listed
    assert "[its subject](its link)" in listed
    assert "Link, to give the user: https://mail.google.com/" in read


def test_the_inbox_spark_is_told_to_link_what_it_mentions():
    inbox = next(t for t in sparks.TEMPLATES if t["name"] == "Inbox")

    assert "[subject](link)" in inbox["goal"]


# --- Several accounts ----------------------------------------------------


@pytest.fixture
def two(server):
    """A Gmail and a work address, both on the fake server."""

    mail.save("me@work.example", FakeIMAP.password)
    return server


def test_a_file_from_when_there_was_one_account_still_reads(monkeypatch):
    path = mail._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"address": "me@gmail.com", "password": "p"}))

    assert mail.addresses() == ["me@gmail.com"]
    mail.save("me@work.example", "q")
    assert mail.addresses() == ["me@gmail.com", "me@work.example"]
    assert "accounts" in json.loads(path.read_text())


def test_accounts_are_added_changed_and_removed_one_at_a_time(two):
    assert mail.addresses() == ["me@gmail.com", "me@work.example"]

    mail.save("ME@work.example", None, "mail.work.example")
    shown = {a["address"]: a for a in mail.settings()["accounts"]}
    assert shown["me@work.example"]["imap"] == "mail.work.example:993"
    assert len(shown) == 2

    mail.forget("me@gmail.com")
    assert mail.addresses() == ["me@work.example"]
    with pytest.raises(mail.MailError, match="not connected"):
        mail.forget("nobody@example.com")


def test_the_inbox_covers_every_account_saying_which(two):
    found = mail.inbox()

    assert {m["account"] for m in found} == {
        "me@gmail.com", "me@work.example",
    }
    assert len(found) == 6
    ats = [m["at"] for m in found]
    assert ats == sorted(ats, reverse=True)


def test_one_account_can_be_checked_alone(two):
    found = mail.inbox(account="me@work.example")

    assert {m["account"] for m in found} == {"me@work.example"}
    with pytest.raises(mail.MailError, match="not one of the connected"):
        mail.inbox(account="nobody@example.com")


def test_an_account_that_fails_does_not_cost_the_others(two, monkeypatch):
    mail.save("me@work.example", "wrong")
    problems = []

    found = mail.inbox(problems=problems)

    assert {m["account"] for m in found} == {"me@gmail.com"}
    assert problems[0][0] == "me@work.example"
    assert "app password" in problems[0][1]


def test_a_uid_needs_its_account_once_there_are_several(two):
    with pytest.raises(mail.MailError, match="which account"):
        mail.read("9")

    assert mail.read("9", account="me@work.example")["account"] == (
        "me@work.example"
    )


def test_a_reply_goes_from_the_account_it_came_to(two):
    original = mail.read("9", account="me@work.example")

    message = mail.compose(body="Signed.", reply_to=original)
    sent = mail.send(message)

    assert message["From"] == "me@work.example"
    assert sent == "bo@example.com"
    assert FakeSMTP.sent[-1]["From"] == "me@work.example"


def test_check_inbox_names_each_emails_account(two):
    said = tools.check_inbox()

    assert "with a uid and its account" in said
    assert " · in me@work.example · " in said
    assert " · in me@gmail.com · " in said


def test_check_inbox_says_which_account_it_could_not_check(two):
    mail.save("me@work.example", "wrong")

    said = tools.check_inbox()

    assert "Could not check me@work.example:" in said
    assert "- uid 9" in said


def test_send_email_says_who_it_is_from(two):
    asked = []
    with answer_from(lambda q: asked.append(q) or "n"):
        tools.send_email(
            "a@example.com", "Hi", "Hello", account="me@work.example",
        )

    assert asked[0].startswith("Send this email?\nFrom: me@work.example")


def test_the_settings_page_tests_and_removes_one_account(two):
    session = web.Session()

    tested = web.command(session, {
        "name": "email-test", "address": "me@work.example",
    })
    assert tested["said"].startswith("Connected as me@work.example")

    left = web.command(session, {
        "name": "email-forget", "address": "me@work.example",
    })
    assert [a["address"] for a in left["email"]["accounts"]] == [
        "me@gmail.com"
    ]
