# pylint: disable=C0114,C0115,C0116

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

HERE = Path(__file__).parent
PAGE = HERE.parent / "flash" / "web" / "index.html"
DOM = HERE / "page_dom.js"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="needs node")


def _slice(page: str, start: str, end: str) -> str:
    return page[page.index(start):page.index(end)]


def run(script: str):
    """What SCRIPT prints as JSON, run in node after the team chat's card
    code, drawn into a stand-in document."""

    page = PAGE.read_text(encoding="utf-8")
    code = "\n".join([
        DOM.read_text(encoding="utf-8"),
        _slice(page, "// --- Cards on a message:",
               "// A call's one entry in the chat"),
        _slice(page, "function tcThreadLink(entry)",
               "function tcOpenThread(id)"),
        f"(async () => {{ {script} }})().catch((e) => {{ "
        "console.log(JSON.stringify({ error: String(e && e.stack || e) }));"
        " });",
    ])
    done = subprocess.run(  # nosec B603
        [node, "-e", code], capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    )
    said = json.loads(done.stdout)
    assert "error" not in said, said.get("error")  # nosec B101
    return said


WORK = ('const entry = { id: 5, kind: "spark", spark: "s1", '
        'work: { jobs: ["Check issue 12."], since: 1 } };\n')


def test_a_work_card_shows_the_step_and_steer_and_stop():
    said = run(WORK + """
      teamChat.work = { "5": { state: "working", step: "Running the tests" } };
      const card = tcWorkCard(entry);
      console.log(JSON.stringify({
        state: card.dataset.state, pill: texts(card, ".tc-work-pill"),
        job: texts(card, ".tc-work-job"), step: texts(card, ".tc-work-step"),
        acts: texts(card, ".tc-act"),
      }));
    """)

    assert said == {  # nosec B101
        "state": "working", "pill": ["Working"], "job": ["Check issue 12."],
        "step": ["Running the tests"], "acts": ["Steer", "Stop"],
    }


def test_a_work_card_turns_done_with_open_report():
    said = run(WORK + """
      const card = tcWorkCard(entry);
      const before = card.dataset.state;
      entry.work.end = { state: "done", step: "Filed its report", report: 9 };
      tcFillWork(card, entry);
      await card.querySelector(".tc-act").onclick();
      console.log(JSON.stringify({
        before, state: card.dataset.state, acts: texts(card, ".tc-act"),
        jumped: said.commands.filter((c) => c.name === "jump")
          .map((c) => c.arg),
      }));
    """)

    assert said == {  # nosec B101
        "before": "queued", "state": "done", "acts": ["Open report"],
        "jumped": [9],
    }


def test_steer_sends_the_change_and_keeps_the_card():
    said = run(WORK + """
      teamChat.work = { "5": { state: "working", step: "Step one" } };
      const card = tcWorkCard(entry);
      await card.querySelector(".tc-act").onclick();
      const form = card.querySelector(".tc-steer");
      teamChat.work["5"].step = "Step two";
      tcFillWork(card, entry);
      const kept = card.querySelector(".tc-steer") === form;
      form.querySelector("input").value = "Skip the docs";
      await form.onsubmit({ preventDefault() {} });
      console.log(JSON.stringify({ kept, sent: said.commands }));
    """)

    assert said["kept"] is True  # nosec B101
    assert said["sent"] == [{  # nosec B101
        "name": "spark-steer", "arg": "s1",
        "body": {"change": "Skip the docs"},
    }]


ASK = ('const entry = { id: 7, kind: "event", what: "ask", spark: "s1", '
       'name: "Scout", text: "Run a command", at: 1, '
       'ask: { id: "step:s1:100", detail: "git pull", state: "open" } };\n')


def test_an_ask_card_approves_in_the_chat():
    said = run(ASK + """
      teamChat.asks = { "7": "open" };
      said.answer = (name, arg, body) => ({ entry: { ...entry,
        ask: { ...entry.ask, state: body.yes ? "yes" : "no" } } });
      const row = el("div");
      const card = row.appendChild(tcAskCard(entry));
      document.body.appendChild(el("div")).setAttribute("id", "team-chat");
      $("#team-chat").appendChild(row);
      const acts = texts(card, ".tc-act");
      await card.querySelector(".tc-act").onclick();
      console.log(JSON.stringify({
        acts, what: texts(card, ".tc-ask-what"),
        detail: texts(card, ".tc-ask-detail"),
        sent: said.commands[0], after: texts(card, ".tc-ask-said"),
      }));
    """)

    assert said["acts"] == ["Approve", "Not now"]  # nosec B101
    assert said["what"] == ["Run a command"]  # nosec B101
    assert said["detail"] == ["git pull"]  # nosec B101
    assert said["sent"] == {  # nosec B101
        "name": "team-chat-answer", "arg": "desk",
        "body": {"id": 7, "yes": True},
    }
    assert said["after"] == ["You said yes"]  # nosec B101


def test_an_ask_answered_elsewhere_stops_asking():
    said = run(ASK + """
      teamChat.asks = { "7": "answered" };
      const card = tcAskCard(entry);
      console.log(JSON.stringify({ acts: texts(card, ".tc-act"),
        said: texts(card, ".tc-ask-said") }));
    """)

    assert said == {"acts": [], "said": ["No longer waiting"]}  # nosec B101


DRAFT = ('const entry = { id: 8, kind: "spark", spark: "s2", draft: { '
         'kind: "email", to: "ada@example.com", cc: "", subject: "Launch", '
         'body: "We launch Monday.", state: "open" } };\n')


def test_a_draft_card_sends_what_the_user_changed():
    said = run(DRAFT + """
      said.answer = (name, arg, body) => ({ entry: { ...entry,
        draft: { ...entry.draft, state: body.send ? "sent" : "dropped",
                 sent_to: "ada@example.com" } } });
      document.body.appendChild(el("div")).setAttribute("id", "team-chat");
      const card = $("#team-chat").appendChild(tcDraftCard(entry));
      const fields = card.querySelectorAll("[data-key]")
        .map((b) => b.dataset.key);
      const acts = texts(card, ".tc-act");
      const body = card.querySelector('[data-key="body"]');
      body.value = "We launch Monday at 9.";
      body.oninput();
      await card.querySelector(".tc-act").onclick();
      console.log(JSON.stringify({ fields, acts, sent: said.commands[0].body,
        state: card.dataset.state, line: texts(card, ".tc-draft-state") }));
    """)

    assert said["fields"] == ["to", "cc", "subject", "body"]  # nosec B101
    assert said["acts"] == ["Send", "Don't send", "Copy"]  # nosec B101
    assert said["sent"]["body"] == "We launch Monday at 9."  # nosec B101
    assert said["sent"]["send"] is True  # nosec B101
    assert said["state"] == "sent"  # nosec B101
    assert said["line"] == ["Sent to ada@example.com"]  # nosec B101


def test_a_thread_card_has_its_title_live_line_and_files():
    said = run("""
      teamChat.typing = [{ spark: "s1", doing: "Running tests", thread: 4 }];
      const entry = { id: 4, thread_count: 3, thread_title: "Login bug",
        thread_files: 2, thread_who: ["s1"], thread_last: 10 };
      const link = tcThreadLink(entry);
      const live = link.querySelector(".tl-live").textContent;
      entry.resolved = 20;
      tcFillThreadLink(entry, link);
      console.log(JSON.stringify({ live, card: link.className,
        resolved: link.textContent }));
    """)

    assert said["live"] == "Scout is running tests"  # nosec B101
    assert "resolved" in said["card"]  # nosec B101
    assert "Login bug" in said["resolved"]  # nosec B101
    assert "Resolved" in said["resolved"]  # nosec B101


def test_a_tap_on_a_card_in_the_team_chat_answers_there():
    said = run("""
      const chat = document.body.appendChild(el("div"));
      chat.setAttribute("id", "team-chat");
      const row = chat.appendChild(el("div", "tc-msg"));
      row.dataset.id = 12;
      const option = row.appendChild(el("button", "ui-option"));
      const outside = document.body.appendChild(el("button", "ui-option"));
      const here = tcCardSend(option, "The first one");
      const there = tcCardSend(outside, "Elsewhere");
      await new Promise((r) => setTimeout(r, 0));
      console.log(JSON.stringify({ here, there, sent: said.commands }));
    """)

    assert said["here"] is True and said["there"] is False  # nosec B101
    assert said["sent"] == [{  # nosec B101
        "name": "team-chat-say", "arg": "desk",
        "body": {"text": "The first one", "thread": 0, "reply_to": 12},
    }]


def test_back_after_time_away_marks_where_the_news_starts():
    said = run("""
      const log = el("div", "tc-log");
      const now = Date.now() / 1000;
      const entries = [
        { id: 1, kind: "user", at: now - 7200 },
        { id: 2, kind: "event", what: "report", spark: "s1", at: now - 3000 },
        { id: 3, kind: "spark", spark: "s2", text: "@user which one?",
          at: now - 2000 },
      ];
      entries.forEach((e) => {
        log.appendChild(el("div")).dataset.id = e.id;
      });
      tcAway(log, { seen_up_to: now - 3600, entries, asks: {} });
      const line = log.querySelector(".tc-away");
      console.log(JSON.stringify({ text: line.textContent,
        before: line.parentNode.children.indexOf(line) }));
    """)

    assert said["text"] == (  # nosec B101
        "While you were away · 1 needs you, 1 report, 1 message")
    assert said["before"] == 1  # nosec B101


def test_no_away_line_after_a_short_break():
    said = run("""
      const log = el("div", "tc-log");
      const now = Date.now() / 1000;
      const entries = [{ id: 2, kind: "spark", spark: "s1", at: now - 60 }];
      log.appendChild(el("div")).dataset.id = 2;
      tcAway(log, { seen_up_to: now - 600, entries, asks: {} });
      console.log(JSON.stringify({ away: !!log.querySelector(".tc-away") }));
    """)

    assert said == {"away": False}  # nosec B101
