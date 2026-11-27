"""The user's email: read over IMAP, sent over SMTP.

With an address and an app password set (/email in the terminal, or
Settings > Email in the web UI), Flash and its sparks can look through
the inbox, read a message, and send one. Looking never changes anything
on the server: messages are read with BODY.PEEK, so what was unread
stays unread, and nothing is moved or deleted. Sending always asks the
user first, autonomous mode or not.

The password is an app password, made in the account's security
settings, never the account's own. It is kept in ~/.flash/email.json,
readable only by its owner.

Some services have no IMAP or SMTP to sign in to (end-to-end encrypted
ones, mostly). An extension can bring one as a mail provider (docs/
EXTENSIONS.md, Mail providers): a program Flash runs for each thing it
does, with JSON on stdin. Such an account is kept here as its address
and the provider's name alone. The provider signs in with the account's
own password, which Flash hands it and keeps nowhere, and keeps its
session in Flash's vault (vault.keep).
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import json
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from . import extensions
from .paths import FLASH_DIR

# Per attempt: a server with an IPv6 and an IPv4 address that answers
# on neither takes twice this to give up on.
TIMEOUT_SECONDS = 12
LIST_LIMIT = 15
MAX_LIST = 50
# How much of each message a listing reads, for its first lines: enough
# to get past the headers and into the text of most mail, not so much
# that a listing downloads every attachment in the inbox.
PEEK_BYTES = 16384
SNIPPET_CHARS = 160
BODY_CHARS = 6000
SUBJECT_CHARS = 200
SEND_CHARS = 20000

# Where the big providers keep their servers, by the address's domain.
# Anything else gets imap.<domain> and smtp.<domain>, which is right more
# often than not, and can be changed in the settings.
PROVIDERS = {
    "gmail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
    "googlemail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
    "outlook.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "hotmail.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "live.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "msn.com": ("outlook.office365.com", 993, "smtp.office365.com", 587),
    "icloud.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "me.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "mac.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587),
    "yahoo.com": ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465),
    "aol.com": ("imap.aol.com", 993, "smtp.aol.com", 465),
    "fastmail.com": ("imap.fastmail.com", 993, "smtp.fastmail.com", 465),
    "zoho.com": ("imap.zoho.com", 993, "smtp.zoho.com", 465),
}

# Where each provider makes an app password, for the settings to link.
APP_PASSWORD_HELP = {
    "imap.gmail.com": "https://myaccount.google.com/apppasswords",
    "imap.mail.me.com": "https://account.apple.com/account/manage",
    "imap.mail.yahoo.com": "https://login.yahoo.com/account/security",
    "imap.fastmail.com": "https://app.fastmail.com/settings/security/apps",
}

NOT_SET_UP = (
    "Email is not set up yet, so nothing can be read or sent. Tell the "
    "user to connect it: Settings > Email in the web UI, or /email "
    "connect in the terminal."
)


class MailError(Exception):
    """Email could not be read or sent, in words the user can act on."""


# A message's uid: an IMAP server's number, or a mail provider's own id.
UID_RE = re.compile(r"^[A-Za-z0-9_=+/-]{1,200}$")


def uid_ok(uid) -> bool:
    """Whether UID can be a message's uid, in some account."""

    return bool(UID_RE.match(str(uid or "")))


# --- Settings ------------------------------------------------------------


def _path() -> Path:
    # FLASH_DIR is looked up here, not bound at import, so a test can
    # point it at a temporary home.
    return FLASH_DIR / "email.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _server(text: str, default_port: int) -> tuple[str, int]:
    host, _, port = str(text or "").strip().rpartition(":")
    if not host:
        host, port = port, ""
    if port and not port.isdigit():
        raise MailError(f"{text!r} is not a server: write host or host:port.")
    return host.strip().lower(), int(port) if port else default_port


def servers_for(address: str) -> tuple[str, int, str, int]:
    """(IMAP host, port, SMTP host, port) for ADDRESS's provider."""

    domain = address.rpartition("@")[2].strip().lower()
    return PROVIDERS.get(domain) or (
        f"imap.{domain}", 993, f"smtp.{domain}", 465,
    )


# --- Accounts ------------------------------------------------------------
#
# Any number of addresses, each with its own app password and servers:
# a personal Gmail, a work Outlook. Kept as {"accounts": [...]}; a file
# from when there could be only one, {"address": ...}, reads as a list
# of that one.


def _raw_accounts() -> list[dict]:
    data = _load()
    found = data.get("accounts")
    if isinstance(found, list):
        return [a for a in found if isinstance(a, dict) and a.get("address")]
    return [data] if data.get("address") else []


def _write(accounts: list[dict], default: Optional[str] = None) -> None:
    """Keep ACCOUNTS, and DEFAULT as the default address (None keeps the
    one there is, while it is still one of them)."""

    path = _path()
    if not accounts:
        path.unlink(missing_ok=True)
        return
    if default is None:
        default = str(_load().get("default") or "")
    if not any(_same(a["address"], default) for a in accounts if default):
        default = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Made readable by its owner only before a password goes in.
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    data: dict = {"accounts": accounts}
    if default:
        data["default"] = default
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _full(raw: dict) -> dict:
    """RAW, as kept, with its provider's servers filled in."""

    address = str(raw.get("address") or "")
    imap_host, imap_port, smtp_host, smtp_port = servers_for(address)
    return {
        "address": address,
        "password": str(raw.get("password") or ""),
        "provider": str(raw.get("provider") or ""),
        "imap_host": str(raw.get("imap_host") or imap_host),
        "imap_port": int(raw.get("imap_port") or imap_port),
        "smtp_host": str(raw.get("smtp_host") or smtp_host),
        "smtp_port": int(raw.get("smtp_port") or smtp_port),
    }


def _shown(raw: dict) -> dict:
    """One account, for the settings to show: never its password."""

    full = _full(raw)
    if full["provider"]:
        found = extensions.find_mail_provider(name=full["provider"])
        return {
            "address": full["address"],
            "has_password": True,
            "provider": full["provider"],
            "provider_label": found[1].label if found else full["provider"],
            "provider_missing": found is None,
            "imap": "",
            "smtp": "",
            "help": "",
        }
    return {
        "address": full["address"],
        "has_password": bool(full["password"]),
        "provider": "",
        "imap": f"{full['imap_host']}:{full['imap_port']}",
        "smtp": f"{full['smtp_host']}:{full['smtp_port']}",
        "help": APP_PASSWORD_HELP.get(full["imap_host"], ""),
    }


def settings() -> dict:
    """Every account, for the settings to show, whether any works, and
    which is the default."""

    return {
        "accounts": [_shown(a) for a in _raw_accounts()],
        "configured": configured(),
        "default": default_address(),
        # Services an extension signs in to, for the form to know which
        # addresses sign in with their own password.
        "providers": [
            {"name": p.name, "label": p.label, "domains": p.domains}
            for _, p in extensions.mail_providers()
        ],
    }


def default_address() -> str:
    """The user's default address: the one they chose, or the first
    connected. Email goes to it when no one else is named, and from it
    when no account is. "" with none connected."""

    connected = addresses()
    chosen = str(_load().get("default") or "")
    for address in connected:
        if _same(address, chosen):
            return address
    return connected[0] if connected else ""


def set_default(address: str) -> dict:
    """Make ADDRESS, one of the connected ones, the default."""

    found = next(
        (a for a in addresses() if _same(a, str(address or ""))), None,
    )
    if found is None:
        raise MailError(f"{address} is not connected.")
    _write(_raw_accounts(), found)
    return settings()


def _connected(raw: dict) -> bool:
    """Whether RAW can be used: it has an app password, or a provider."""

    return bool(raw.get("password") or raw.get("provider"))


def configured() -> bool:
    return any(_connected(a) for a in _raw_accounts())


def addresses() -> list[str]:
    """The connected addresses, in the order they were added."""

    return [str(a["address"]) for a in _raw_accounts() if _connected(a)]


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def save(
    address: str, password: Optional[str] = None, imap: str = "",
    smtp: str = "",
) -> dict:
    """Add ADDRESS, or change it if it is already here: its PASSWORD
    unless that is None (to keep the one it has), and IMAP and SMTP as
    host[:port], or "" for the provider's."""

    address = str(address or "").strip()
    name, parsed = parseaddr(address)
    if not parsed or "@" not in parsed or name:
        raise MailError(f"{address!r} is not an email address.")
    accounts = _raw_accounts()
    old = next((a for a in accounts if _same(a["address"], parsed)), None)
    # Changed, it keeps the address as it was first written.
    data: dict = {"address": old["address"] if old else parsed}
    if str(imap or "").strip():
        imap_host, imap_port = _server(imap, 993)
        data.update(imap_host=imap_host, imap_port=imap_port)
    if str(smtp or "").strip():
        smtp_host, smtp_port = _server(smtp, 465)
        data.update(smtp_host=smtp_host, smtp_port=smtp_port)
    if password is None:
        data["password"] = (old or {}).get("password", "")
        # A provider's account stays one, unless servers are given.
        if (old or {}).get("provider") and not (
                str(imap or "").strip() or str(smtp or "").strip()):
            data = {"address": data["address"], "provider": old["provider"]}
    else:
        # App passwords are shown in groups ("abcd efgh ijkl mnop") and
        # used without the spaces.
        data["password"] = "".join(str(password).split())
    if old is not None:
        accounts[accounts.index(old)] = data
    else:
        accounts.append(data)
    _write(accounts)
    return settings()


def forget(address: str = "") -> None:
    """Disconnect ADDRESS: its password goes, and a provider's account
    is signed out. With none, every one."""

    accounts = _raw_accounts()
    if not str(address or "").strip():
        for raw in accounts:
            _sign_out(raw)
        _path().unlink(missing_ok=True)
        return
    kept = [a for a in accounts if not _same(a["address"], address)]
    if len(kept) == len(accounts):
        raise MailError(f"{address} is not connected.")
    for raw in accounts:
        if _same(raw["address"], address):
            _sign_out(raw)
    _write(kept)


def _sign_out(raw: dict) -> None:
    """End RAW's session with its provider, if it has one. Going is not
    held up by a provider that cannot be reached, or is gone."""

    if not raw.get("provider"):
        return
    found = extensions.find_mail_provider(name=str(raw["provider"]))
    if found:
        extensions.call_mail(*found, {"action": "signout",
                                      "address": str(raw["address"])})


# --- Accounts a provider signs in to ------------------------------------


def provider_for(address: str) -> Optional[dict]:
    """{"name", "label"} of the installed mail provider for ADDRESS's
    domain, or None when it is an IMAP and SMTP account."""

    found = extensions.find_mail_provider(address=str(address or ""))
    if found is None:
        return None
    return {"name": found[1].name, "label": found[1].label}


def connect_provider(
    address: str, password: str = "", code: str = "",
    mailbox_password: str = "", restart: bool = False, provider: str = "",
) -> dict:
    """Sign ADDRESS in with its provider (PROVIDER, or the one for its
    domain), and connect it once that is done.

    Signing in can take more than one step, each answered with what the
    provider asks for next: {"needs": "code" | "mailbox_password" |
    "verification", "text", maybe "url"}, to be called again with the
    same PASSWORD and what was asked. {"done": True, "text"} when it is
    connected. The password is passed on and kept nowhere.
    """

    address = str(address or "").strip()
    name, parsed = parseaddr(address)
    if not parsed or "@" not in parsed or name:
        raise MailError(f"{address!r} is not an email address.")
    found = extensions.find_mail_provider(name=provider, address=parsed)
    if found is None:
        raise MailError(
            f"No installed extension signs in to {parsed}"
            + (f" with {provider}." if provider else "."))
    extension, chosen = found
    answer = extensions.call_mail(extension, chosen, {
        "action": "signin", "address": parsed,
        "password": str(password or ""), "code": str(code or "").strip(),
        "mailbox_password": str(mailbox_password or ""),
        "restart": bool(restart),
    })
    if not answer.get("ok"):
        raise MailError(str(answer.get("error")))
    out = {
        key: str(answer[key]) for key in ("needs", "text", "url")
        if answer.get(key)
    }
    if not answer.get("done"):
        out.setdefault("needs", "code")
        return out
    accounts = _raw_accounts()
    old = next((a for a in accounts if _same(a["address"], parsed)), None)
    data = {"address": old["address"] if old else parsed,
            "provider": chosen.name}
    if old is not None:
        accounts[accounts.index(old)] = data
    else:
        accounts.append(data)
    _write(accounts)
    out["done"] = True
    out.setdefault("text", f"Connected {data['address']} with "
                   f"{chosen.label}.")
    return out


def _call(account: dict, action: str, **payload) -> dict:
    """The answer of ACCOUNT's provider to ACTION, or a MailError."""

    found = extensions.find_mail_provider(name=account["provider"])
    if found is None:
        raise MailError(
            f"{account['address']} is read through the "
            f"{account['provider']} extension, which is not installed. "
            "Install it again, or disconnect the account.")
    answer = extensions.call_mail(*found, {
        "action": action, "address": account["address"], **payload,
    })
    if not answer.get("ok"):
        raise MailError(str(answer.get("error")))
    return answer


def _account(address: str = "") -> dict:
    """The account to use: ADDRESS, or with none, the default one."""

    found = [a for a in _raw_accounts() if _connected(a)]
    if not found:
        raise MailError(NOT_SET_UP)
    if not str(address or "").strip():
        address = default_address()
    for raw in found:
        if _same(raw["address"], str(address)):
            return _full(raw)
    raise MailError(
        f"{address} is not one of the connected email accounts: "
        f"{', '.join(a['address'] for a in found)}."
    )


def _accounts() -> list[dict]:
    """Every connected account, filled in."""

    found = [_full(a) for a in _raw_accounts() if _connected(a)]
    if not found:
        raise MailError(NOT_SET_UP)
    return found


# --- Links ---------------------------------------------------------------

# Gmail opens one message from a link that searches for its Message-ID,
# in whichever of the browser's Google accounts has this address.
GMAIL_HOSTS = {"imap.gmail.com"}


def link(message_id: str, account: Optional[dict] = None) -> str:
    """A link that opens the message with MESSAGE_ID, or "" without one.

    Gmail's web inbox, for a Gmail account. Anything else gets a
    message: link, which Apple Mail opens to that message, as any app
    that registered for message: links does.
    """

    found = message_id.strip().strip("<>").strip()
    if not found:
        return ""
    if account is None:
        account = _full((_raw_accounts() or [{}])[0])
    host = str(account.get("imap_host") or servers_for(
        str(account.get("address") or ""))[0])
    if host in GMAIL_HOSTS:
        who = quote(str(account.get("address") or ""), safe="@")
        return (
            f"https://mail.google.com/mail/?authuser={who}"
            f"#search/rfc822msgid%3A{quote(found, safe='')}"
        )
    return f"message:%3C{quote(found, safe='@')}%3E"


# --- Reading -------------------------------------------------------------


def _imap(account: dict) -> imaplib.IMAP4:
    try:
        box = imaplib.IMAP4_SSL(
            account["imap_host"], account["imap_port"],
            ssl_context=ssl.create_default_context(),
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, imaplib.IMAP4.error) as e:
        raise MailError(
            f"Could not reach {account['imap_host']}: {e}. Check the IMAP "
            "server in the email settings."
        ) from None
    try:
        box.login(account["address"], account["password"])
    except imaplib.IMAP4.error as e:
        _close(box)
        raise MailError(
            f"{account['imap_host']} refused the login: "
            f"{_said(e)}. Use an app password, not the account's own, "
            "and check that IMAP is switched on for the account."
        ) from None
    return box


def _close(box: imaplib.IMAP4) -> None:
    for step in (box.close, box.logout):
        try:
            step()
        except (OSError, imaplib.IMAP4.error):
            pass


def _said(error: Exception) -> str:
    text = error.args[0] if error.args else error
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return str(text).strip() or error.__class__.__name__


def _select(box: imaplib.IMAP4, folder: str) -> None:
    quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
    status, _ = box.select(quoted, readonly=True)
    if status != "OK":
        raise MailError(f"There is no folder called {folder!r}.")


def _search(box: imaplib.IMAP4, unread: bool, query: str) -> list[bytes]:
    criteria: list = ["UNSEEN"] if unread else ["ALL"]
    if query:
        criteria = [*criteria[:1], "TEXT"]
        if query.isascii():
            criteria.append('"' + query.replace('"', "") + '"')
            status, data = box.uid("SEARCH", *criteria)
        else:
            box.literal = query.encode("utf-8")
            status, data = box.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
    else:
        status, data = box.uid("SEARCH", *criteria)
    if status != "OK":
        raise MailError("The server would not search the folder.")
    return (data[0] or b"").split()


class _Text(HTMLParser):
    """An HTML email's words, without its markup, styles or scripts."""

    SKIP = {"style", "script", "head", "title"}
    BREAK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BREAK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(data)


def _html_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError):
        pass
    text = "".join(parser.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _body(message: email.message.EmailMessage) -> tuple[str, list[str]]:
    """MESSAGE's text, plain when it has it, and its attachments' names."""

    plain, html, attached = "", "", []
    for part in message.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if name or part.get_content_disposition() == "attachment":
            attached.append(name or "(unnamed)")
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_content()
        except (LookupError, ValueError, AssertionError):
            payload = part.get_payload(decode=True) or b""
            text = payload.decode("utf-8", "replace")
        if kind == "text/plain" and not plain:
            plain = str(text)
        elif kind == "text/html" and not html:
            html = str(text)
    text = plain.strip() or _html_text(html)
    return text, attached


def _header(message, name: str) -> str:
    return " ".join(str(message.get(name, "") or "").split())


def _when(message) -> float:
    """When MESSAGE was sent, as a timestamp; 0 when it does not say."""

    try:
        when = email.utils.parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError, IndexError):
        return 0.0
    return when.timestamp() if when is not None else 0.0


def _date(message) -> str:
    try:
        when = email.utils.parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError, IndexError):
        return ""
    if when is None:
        return ""
    return time.strftime(
        "%a %d %b %H:%M", time.localtime(when.timestamp())
    )


def _parse(raw: bytes) -> email.message.EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)


def _fetched(data: list) -> list[tuple[bytes, bytes, bytes]]:
    """(uid, flags, raw) for each message an IMAP FETCH answered with."""

    out = []
    for item in data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        head = item[0] if isinstance(item[0], bytes) else b""
        uid = re.search(rb"UID (\d+)", head)
        flags = re.search(rb"FLAGS \(([^)]*)\)", head)
        out.append((
            uid.group(1) if uid else b"",
            flags.group(1) if flags else b"",
            item[1] if isinstance(item[1], bytes) else b"",
        ))
    return out


def inbox(
    unread: bool = False, query: str = "", limit: int = LIST_LIMIT,
    folder: str = "INBOX", account: str = "",
    problems: Optional[list] = None,
) -> list[dict]:
    """The newest messages in FOLDER, newest first: only UNREAD ones, or
    ones matching QUERY, if asked. Nothing is marked read.

    From ACCOUNT, by its address, or with none from every account, each
    message saying which it is in. An account that cannot be read then
    goes in PROBLEMS, as (address, why), rather than costing the rest;
    when none can be, the first one's MailError is raised.
    """

    limit = max(1, min(int(limit or LIST_LIMIT), MAX_LIST))
    if str(account or "").strip():
        return _inbox(_account(account), unread, query, limit, folder)
    found: list[dict] = []
    failed: list[tuple[str, MailError]] = []
    every = _accounts()
    for one in every:
        try:
            found += _inbox(one, unread, query, limit, folder)
        except MailError as exc:
            failed.append((one["address"], exc))
    if failed and len(failed) == len(every):
        raise failed[0][1]
    if problems is not None:
        problems.extend((who, str(why)) for who, why in failed)
    found.sort(key=lambda m: m["at"], reverse=True)
    return found[:limit]


def _inbox(
    account: dict, unread: bool, query: str, limit: int, folder: str,
) -> list[dict]:
    """inbox(), from ACCOUNT alone."""

    if account["provider"]:
        return _provider_inbox(account, unread, query, limit, folder)
    box = _imap(account)
    try:
        _select(box, folder)
        uids = _search(box, unread, str(query or "").strip())[-limit:]
        if not uids:
            return []
        status, data = box.uid(
            "FETCH", b",".join(uids),
            f"(UID FLAGS BODY.PEEK[]<0.{PEEK_BYTES}>)",
        )
        if status != "OK":
            raise MailError("The server would not hand over the messages.")
    except imaplib.IMAP4.error as e:
        raise MailError(f"Reading {folder} failed: {_said(e)}") from None
    finally:
        _close(box)

    found = []
    for uid, flags, raw in _fetched(data):
        message = _parse(raw)
        text, attached = _body(message)
        found.append({
            "uid": uid.decode(),
            "from": _header(message, "From"),
            "subject": _header(message, "Subject")[:SUBJECT_CHARS],
            "date": _date(message),
            "unread": b"\\Seen" not in flags,
            "snippet": " ".join(text.split())[:SNIPPET_CHARS],
            "attachments": len(attached),
            "link": link(_header(message, "Message-ID"), account),
            "account": account["address"],
            "at": _when(message),
        })
    found.sort(key=lambda m: (m["at"], int(m["uid"] or 0)), reverse=True)
    return found


def _provider_inbox(
    account: dict, unread: bool, query: str, limit: int, folder: str,
) -> list[dict]:
    answer = _call(account, "inbox", unread=bool(unread),
                   query=str(query or "").strip(), limit=limit,
                   folder=folder)
    found = []
    for message in answer.get("messages") or []:
        if not isinstance(message, dict) or not uid_ok(message.get("uid")):
            continue
        try:
            at = float(message.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        found.append({
            "uid": str(message["uid"]),
            "from": str(message.get("from") or ""),
            "subject": str(message.get("subject") or "")[:SUBJECT_CHARS],
            "date": str(message.get("date") or ""),
            "unread": bool(message.get("unread")),
            "snippet": " ".join(
                str(message.get("snippet") or "").split())[:SNIPPET_CHARS],
            "attachments": int(message.get("attachments") or 0),
            "link": str(message.get("link") or ""),
            "account": account["address"],
            "at": at,
        })
    found.sort(key=lambda m: m["at"], reverse=True)
    return found[:limit]


def next_uids(folder: str = "INBOX") -> dict[str, int]:
    """The uid the next email to FOLDER will get, in each account, by its
    address: a change means something came in. A quick question of the
    server, reading no email. An account that cannot be asked is left
    out."""

    found: dict[str, int] = {}
    quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
    for account in _accounts():
        if account["provider"]:
            # A provider's number is when the newest message came, not
            # a uid: a message is new when it came after (later_than).
            try:
                found[account["address"]] = int(
                    _call(account, "latest", folder=folder).get("next") or 0)
            except (MailError, TypeError, ValueError):
                pass
            continue
        try:
            box = _imap(account)
        except MailError:
            continue
        try:
            status, data = box.status(quoted, "(UIDNEXT)")
            said = b" ".join(d for d in data if isinstance(d, bytes))
            number = re.search(rb"UIDNEXT (\d+)", said)
            if status == "OK" and number:
                found[account["address"]] = int(number.group(1))
        except (imaplib.IMAP4.error, OSError):
            pass
        finally:
            try:
                box.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    return found


def read(uid: str, folder: str = "INBOX", account: str = "") -> dict:
    """The whole of message UID in ACCOUNT: its headers, its text, and
    the names of its attachments. It stays unread if it was.

    A uid is only one account's, so with more than one connected, ACCOUNT
    has to say which.
    """

    uid = str(uid or "").strip()
    _one_account_or(account)
    account = _account(account)
    if account["provider"] and uid_ok(uid):
        return _provider_read(account, uid)
    if not uid.isdigit():
        raise MailError(f"{uid!r} is not a message's uid.")
    box = _imap(account)
    try:
        _select(box, folder)
        status, data = box.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
    except imaplib.IMAP4.error as e:
        raise MailError(f"Reading message {uid} failed: {_said(e)}") from None
    finally:
        _close(box)
    fetched = _fetched(data) if status == "OK" else []
    if not fetched:
        raise MailError(f"There is no message {uid} in {folder}.")
    _, flags, raw = fetched[0]
    message = _parse(raw)
    text, attached = _body(message)
    return {
        "uid": uid,
        "from": _header(message, "From"),
        "to": _header(message, "To"),
        "cc": _header(message, "Cc"),
        "subject": _header(message, "Subject"),
        "date": _date(message),
        "message_id": _header(message, "Message-ID"),
        "link": link(_header(message, "Message-ID"), account),
        "references": _header(message, "References"),
        "reply_to": _header(message, "Reply-To"),
        "unread": b"\\Seen" not in flags,
        "body": text,
        "attachments": attached,
        "account": account["address"],
    }


def _provider_read(account: dict, uid: str) -> dict:
    message = _call(account, "read", uid=uid).get("message")
    if not isinstance(message, dict):
        raise MailError(f"There is no message {uid}.")
    out = {key: str(message.get(key) or "") for key in (
        "from", "to", "cc", "subject", "date", "message_id", "link",
        "references", "reply_to", "body")}
    out.update(
        uid=uid,
        unread=bool(message.get("unread")),
        attachments=[str(a) for a in message.get("attachments") or []],
        account=account["address"],
    )
    return out


# What servers call the trash, when the server does not mark it \Trash itself.
TRASH_NAMES = ("Trash", "[Gmail]/Trash", "[Google Mail]/Trash", "Deleted Items", "Deleted Messages", "Deleted",
               "INBOX.Trash", "Bin", "[Gmail]/Bin")


def _trash_folder(box: imaplib.IMAP4) -> str:
    status, data = box.list()
    if status != "OK":
        return ""
    names = []
    for line in data or []:
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line or "")
        found = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', text)
        if not found:
            continue
        name = found.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"')
        if "\\trash" in found.group("flags").lower():
            return name
        names.append(name)
    for wanted in TRASH_NAMES:
        for name in names:
            if name.lower() == wanted.lower():
                return name
    return ""


def trash(uid: str, account: str = "", folder: str = "INBOX") -> str:
    """Message UID moved to ACCOUNT's trash (the folder the server marks as
    its trash, else the usual names), where it can still be got back."""

    uid = str(uid or "").strip()
    _one_account_or(account)
    account = _account(account)
    if account["provider"] and uid_ok(uid):
        return str(_call(account, "trash", uid=uid, folder=folder)
                   .get("folder") or "Trash")
    if not uid.isdigit():
        raise MailError(f"{uid!r} is not a message's uid.")
    box = _imap(account)
    try:
        bin_name = _trash_folder(box)
        if not bin_name:
            raise MailError(f"{account['address']} has no trash folder Flash can find.")
        quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
        status, _ = box.select(quoted)
        if status != "OK":
            raise MailError(f"There is no folder called {folder!r}.")
        target = '"' + bin_name.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if "MOVE" in getattr(box, "capabilities", ()):
            status, _ = box.uid("MOVE", uid, target)
        else:
            status, _ = box.uid("COPY", uid, target)
            if status == "OK":
                box.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
                box.expunge()
        if status != "OK":
            raise MailError(f"Moving message {uid} to {bin_name} failed.")
    except imaplib.IMAP4.error as e:
        raise MailError(f"Moving message {uid} to the trash failed: {_said(e)}") from None
    finally:
        _close(box)
    return bin_name


def _one_account_or(account: str) -> None:
    """Refuse a message's uid without its account, when there are
    several: the same uid is a different message in each."""

    if str(account or "").strip():
        return
    connected = addresses()
    if len(connected) > 1:
        raise MailError(
            "Say which account the message is in: one of "
            f"{', '.join(connected)}."
        )


def test(address: str = "") -> str:
    """Log in to ADDRESS, or the first account, and look: how many
    messages wait, or a MailError."""

    account = _account(address)
    if account["provider"]:
        return str(_call(account, "test").get("text") or
                   f"Connected as {account['address']}.")
    box = _imap(account)
    try:
        _select(box, "INBOX")
        unread = len(_search(box, True, ""))
    finally:
        _close(box)
    return (
        f"Connected as {account['address']}: {unread} unread "
        f"message{'' if unread == 1 else 's'} in the inbox."
    )


# --- Sending -------------------------------------------------------------


def _addresses(text: str) -> list[str]:
    found = [a for _, a in getaddresses([str(text or "")]) if "@" in a]
    if not found:
        raise MailError(f"{text!r} holds no email address to send to.")
    return found


def compose(
    to: str = "", subject: str = "", body: str = "",
    reply_to: Optional[dict] = None, cc: str = "", account: str = "",
) -> EmailMessage:
    """The message as it would be sent, from ACCOUNT, or the default one.
    REPLY_TO is the message read() gave for the one being answered: it
    threads the reply, is sent from the account it came to, and fills
    in who it goes to and its subject when they are left out. Anything
    else with no one to send TO goes to the user's default address."""

    account = _account(account or (reply_to or {}).get("account", ""))
    message = EmailMessage()
    message["From"] = account["address"]
    if reply_to:
        to = to or reply_to.get("reply_to") or reply_to.get("from", "")
        subject = subject or reply_to.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        original = reply_to.get("message_id", "")
        if original:
            message["In-Reply-To"] = original
            message["References"] = " ".join(
                part for part in (reply_to.get("references", ""), original)
                if part
            )
    if not str(to or "").strip():
        # Nobody named: the user's own default address, as "email me
        # this" means.
        to = default_address()
    message["To"] = ", ".join(_addresses(to))
    if str(cc or "").strip():
        message["Cc"] = ", ".join(_addresses(cc))
    message["Subject"] = " ".join(str(subject or "").split())[:SUBJECT_CHARS]
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = make_msgid(
        domain=account["address"].rpartition("@")[2] or None,
    )
    body = str(body or "").strip()
    if not body:
        raise MailError("The email has nothing in it.")
    message.set_content(body[:SEND_CHARS] + "\n")
    return message


def send(message: EmailMessage) -> str:
    """Send MESSAGE, as compose() made it, through the account it is
    from. Who it went to."""

    account = _account(str(message["From"] or ""))
    if account["provider"]:
        said = _call(account, "send", raw=message.as_string()).get("text")
        return str(said or ", ".join(
            part for part in (message["To"], message.get("Cc", "")) if part))
    port = account["smtp_port"]
    context = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(
                account["smtp_host"], port, context=context,
                timeout=TIMEOUT_SECONDS,
            )
        else:
            server = smtplib.SMTP(
                account["smtp_host"], port, timeout=TIMEOUT_SECONDS,
            )
            server.starttls(context=context)
        with server:
            server.login(account["address"], account["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise MailError(
            f"{account['smtp_host']} refused the login. Use an app "
            "password, not the account's own."
        ) from None
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(
            f"Sending through {account['smtp_host']} failed: {e}"
        ) from None
    return ", ".join(
        part for part in (message["To"], message.get("Cc", "")) if part
    )
