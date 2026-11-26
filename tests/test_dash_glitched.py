"""flash-dash-glitched: the practice level with six secrets in it, glitches
that help, for an agent to find by playing. This is the answer key; each
test pins one, so a change to the level cannot quietly remove it.

1. Pad fling: tap again just after a pad throws you, and it flings you far
   up and forward, clean over the spike pit (or, from the last pad, over
   the finish).
2. Ring slingshot: hold the jump through the ring instead of tapping, and
   it slings you on.
3. Spike surf: come down square on a spike's tip and it bounces you.
4. Wall climb: hit a block's side with the jump held and you climb it.
5. Arch backflip: jump through the speed arch and the level slows down
   instead, until the ship.
6. Ceiling ghost: grind the ship along the ceiling and it goes through
   pillars until you let go.

A careful run finds none of them: the reference reflex wins with 0.
"""

from pathlib import Path

import pytest

from flash import tools

GAME = Path(__file__).parent.parent / "flash" / "games" / "dash-glitched.html"
PLAIN = Path(__file__).parent.parent / "flash" / "games" / "dash.html"


def test_it_is_a_game_flash_opens_by_name():
    assert tools.GAMES["flash-dash-glitched"] == GAME
    assert GAME.exists()


def test_same_level_as_the_plain_one():
    def level(path):
        text = path.read_text(encoding="utf-8")
        return text[text.index("const LEVEL = ["):text.index("const END")]
    assert level(GAME) == level(PLAIN)


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


LAND = "let k = 0; while (!S.ground && !S.over && k++ < 2000) go(1); "


def run(page, script):
    return page.evaluate(
        "() => { reset(); const S = window.dash; "
        "const go = (n, held) => { for (let i = 0; i < n; i++) { "
        "if (held && !S.held) S.pressed = true; S.held = !!held; "
        "step(1 / 240); if (S.over) break; } }; " + script + " }")


def test_pad_fling_clears_the_spike_pit(page):
    to_pad = "S.x = 2120; let i = 0; while (S.padT < 0 && i++ < 400) go(1); "
    plain = run(page, to_pad + "go(5); " + LAND + "return S.x + PX;")
    flung = run(page, to_pad + "go(4, true); go(1); " + LAND
                + "return [S.x + PX, S.over, S.found];")
    assert flung[0] > 3000 > plain  # past the last spike of the pit
    assert flung[1] is False and flung[2] == ["pad fling"]


def test_pad_fling_from_the_last_pad_carries_you_past_the_last_spikes(page):
    # the pad at 7400 sits just before five spikes and three more after
    landed = run(page, "S.x = 7240; S.fast = true; let i = 0; "
                       "while (S.padT < 0 && i++ < 400) go(1); go(4, true); "
                       "go(1); " + LAND + "return [S.x + PX, S.over];")
    assert landed[0] > 8040 and landed[1] is False


def test_ring_slingshot_goes_further_than_a_tap(page):
    at_ring = "S.x = 2870 - 150 - 15; S.y = -63; S.vy = 0; S.ground = false; "
    tapped = run(page, at_ring + "go(1, true); go(1); " + LAND
                 + "return [S.x + PX, S.found];")
    held = run(page, at_ring + "go(40, true); " + LAND
               + "return [S.x + PX, S.over, S.found];")
    assert tapped[1] == [] and held[2] == ["ring slingshot"]
    assert held[0] > tapped[0] + 200 and held[1] is False


def test_spike_surf_bounces_off_a_tip(page):
    assert run(page, "S.x = 548; S.y = -31; S.vy = 120; S.ground = false; "
                     "go(30); return !S.over && S.vy < 0 && "
                     "S.found.includes('spike surf');")


def test_wall_climb_only_with_the_jump_held(page):
    assert run(page, "S.x = 1164; go(2); go(30, true); "
                     "return !S.over && S.found.includes('wall climb');")
    assert run(page, "S.x = 1164; go(60); return S.over;")


def test_arch_backflip_slows_the_level(page):
    assert run(page, "S.x = 2910; go(1, true); go(60, true); "
                     "return S.slow && !S.fast;")
    assert run(page, "S.x = 2910; go(80); return S.fast && !S.slow;")


def test_ceiling_ghost_passes_through_pillars(page):
    assert run(page, "S.x = 4140; S.ship = true; S.ground = false; "
                     "go(1500, true); return !S.over && S.x > 6000 && "
                     "S.found.includes('ceiling ghost');")


def test_the_screen_counts_secrets_and_time():
    shown = GAME.read_text(encoding="utf-8")
    assert "secrets ${state.found.length}/6" in shown
    assert "won in ${state.t.toFixed(2)}s" in shown


def test_the_plain_level_has_none_of_it():
    plain = PLAIN.read_text(encoding="utf-8")
    for secret in ("found(", "boost", "phase", "slow"):
        assert secret not in plain
