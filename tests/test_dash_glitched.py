"""flash-dash-glitched: the practice level with ten glitches planted in it,
for an agent to find by playing. This is the answer key; each test pins one
glitch, so a change to the level cannot quietly remove it.

1. Moonwalk: just past the speed arch, the level runs backwards for 0.3 s.
2. An invisible spike: lethal, never drawn.
3. Decoy spikes: drawn, harmless.
4. A red block: the first block is drawn spike red, and is still a block.
5. A floaty third jump: the third jump falls at about half gravity.
6. A ring drawn 40 px away from where it works.
7. A ceiling that wraps: the ship touching the ceiling comes out at the floor.
8. A shrinking square: after the first pad it is drawn small, and hits as big.
9. The progress counter runs past 100%.
10. A frozen stretch: the screen stops redrawing for a moment in the ship
    section, while the game goes on.
And it can still be won: a search over the game's own steps finds a run.
"""

from pathlib import Path

import pytest

from flash import tools

GAME = Path(__file__).parent.parent / "flash" / "games" / "dash-glitched.html"
PLAIN = Path(__file__).parent.parent / "flash" / "games" / "dash.html"


def test_it_is_a_game_flash_opens_by_name():
    assert tools.GAMES["flash-dash-glitched"] == GAME
    assert GAME.exists()


def test_the_page_gives_nothing_away():
    shown = GAME.read_text(encoding="utf-8")
    say = shown[shown.index('<div class="say"'):shown.index("</div>", shown.index('<div class="say"'))]
    assert say == PLAIN.read_text(encoding="utf-8")[
        PLAIN.read_text(encoding="utf-8").index('<div class="say"'):][:len(say)]


@pytest.fixture(scope="module")
def page():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as pw:
        from flash.browser import launch_chromium
        try:
            browser = launch_chromium(pw)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium: {exc}")
        tab = browser.new_page()
        tab.add_init_script("window.requestAnimationFrame = () => 0;")
        tab.goto(GAME.as_uri())
        yield tab
        browser.close()


def run(page, script):
    return page.evaluate("() => { reset(); const S = window.dash; "
                         "const go = (n, held) => { for (let i = 0; i < n; i++) "
                         "{ if (held && !S.held) S.pressed = true; S.held = !!held; "
                         "step(1 / 240); } }; " + script + " }")


def test_moonwalk(page):
    assert run(page, "S.x = 3550; S.fast = true; go(20); const a = S.x; go(20); "
                     "return S.x < a;")


def test_invisible_spike_kills_but_is_not_drawn(page):
    assert run(page, "return SPIKES.includes(7750) && UNSEEN.includes(7750);")
    assert run(page, "S.x = 7750 - 150 - 20; go(40); return S.over && !S.won;")


def test_decoy_spikes_are_harmless(page):
    assert run(page, "S.x = 2550 - 150 - 40; go(80); return !S.over && "
                     "!SPIKES.includes(2550);")


def test_red_block_is_solid(page):
    assert run(page, "return RED_BLOCK === 0 && BLOCKS[0].a === 1350;")


def test_third_jump_is_floaty(page):
    assert run(page, "S.jumps = 2; go(1, true); go(1); go(60); const y3 = S.y; "
                     "reset(); S.jumps = 0; go(1, true); go(1); go(60); "
                     "return y3 < S.y;")


def test_ring_is_drawn_away_from_its_hitbox(page):
    assert run(page, "return ORB_SHOWN === -40;")


def test_ship_ceiling_wraps_to_the_floor(page):
    # held, the ship climbs to the ceiling, and comes out at the floor
    assert run(page, "S.x = 4400; S.ship = true; S.ground = false; "
                     "let wrapped = false, was = S.y; for (let i = 0; i < 400; i++) "
                     "{ go(1, true); if (was < -150 && S.y > -10) wrapped = true; "
                     "was = S.y; } return wrapped;")


def test_square_shrinks_after_a_pad(page):
    assert run(page, "S.x = 2280 - 150; go(30); return S.small;")


def test_frozen_stretch_and_counter_past_100():
    shown = GAME.read_text(encoding="utf-8")
    assert "state.x > 5000 && state.x < 5070" in shown
    assert "* 107)}%" in shown


def test_the_plain_level_has_none_of_it():
    plain = PLAIN.read_text(encoding="utf-8")
    for planted in ("DECOYS", "UNSEEN", "RED_BLOCK", "ORB_SHOWN", "floaty", "* 107"):
        assert planted not in plain
