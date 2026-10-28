# pylint: disable=C0114,C0116

from flash import hardware, web

GB = hardware.GB


def test_a_graphics_card_s_memory_is_the_budget():
    here = hardware.Machine(memory=64 * GB, gpu="Card", vram=12 * GB)
    assert hardware.fit(5 * GB, here) == hardware.FITS
    assert hardware.fit(9 * GB, here) == hardware.TIGHT
    assert hardware.fit(20 * GB, here) == hardware.TOO_BIG
    assert hardware.fit(0, here) == ""
    assert "Card with 12 GB" in here.describe()


def test_shared_memory_keeps_some_back():
    mac = hardware.Machine(memory=16 * GB, unified=True)
    pc = hardware.Machine(memory=16 * GB)
    assert mac.budget > pc.budget
    assert mac.biggest().startswith("about ")


def test_this_computer_is_looked_at():
    assert hardware.machine().memory > 0


def test_over_ssh(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5 6.7.8.9 22")
    assert web.over_ssh()
    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.delenv("SSH_TTY", raising=False)
    assert not web.over_ssh()
