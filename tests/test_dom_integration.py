import sys
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
from poker_agent import PokerDecision


def _build_table_html(
    hero_cards=None,
    board_cards=None,
    pot="60",
    blinds="10/20",
    hero_stack="500",
    opp_stack="500",
    opp_bet="",
    buttons_html="",
    modal_html="",
    my_turn=True,
):
    hero_cards = hero_cards or []
    board_cards = board_cards or []
    hc_html = "".join(
        f'<div class="card-image-backup"><span class="card-rank">{r}</span><span class="card-suit">{s}</span></div>'
        for r, s in hero_cards
    )
    bc_html = "".join(
        f'<div class="card-image-backup"><span class="card-rank">{r}</span><span class="card-suit">{s}</span></div>'
        for r, s in board_cards
    )
    timer_html = '<div class="text-countdown">12</div>' if my_turn else ""
    opp_bet_html = f'<div class="player-bet"><span class="amount">{opp_bet}</span></div>' if opp_bet else ""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
  <div class="blinds-info">{blinds}</div>
  <div id="main-pot">{pot}</div>
  <div class="community-cards">{bc_html}</div>
  <div class="player-area my-player">
    <div class="player-chips">{hero_stack}</div>
    {timer_html}
    {hc_html}
  </div>
  <div class="player-area">
    <div class="player-name">Villain1</div>
    <div class="player-chips">{opp_stack}</div>
    {opp_bet_html}
  </div>
  <div class="actions-area">{buttons_html}</div>
  {modal_html}
  <script>
    window.__clicked = [];
    document.addEventListener('click', (e) => {{
      const el = e.target.closest('button, #FOLD, #CHECK, #CALL, #BET, #RAISE, #ALLIN, #general-popup-button-action-REPLAY');
      if (el) window.__clicked.push(el.id || el.innerText.trim());
    }});
  </script>
</body>
</html>"""


class TestDOMIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def test_01_turno_propio_check_y_raise(self):
        page = self._browser.new_page()
        page.set_content(_build_table_html(
            hero_cards=[("A", "♠"), ("K", "♦")],
            pot="60",
            buttons_html=(
                '<button id="FOLD" class="action-button">Fold</button>'
                '<button id="CHECK" class="action-button">Check</button>'
                '<input class="betting-input" value="40" />'
                '<button id="RAISE" class="action-button">Raise</button>'
            ),
        ))
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        self.assertTrue(dom["es_mi_turno"])
        self.assertIn("A", dom["mis_cartas"])
        res = bot.ejecutar_accion(page, PokerDecision(estado_pantalla="EN_MESA_JUEGO", es_mi_turno=True, accion="RAISE", monto_apuesta=80, confianza=0.9, evaluacion="Valor"), dom_data=dom)
        self.assertEqual(res, "RAISE")
        clicked = page.evaluate("window.__clicked")
        self.assertIn("RAISE", clicked)
        page.close()

    def test_02_popup_check_gratis(self):
        page = self._browser.new_page()
        page.set_content(_build_table_html(
            hero_cards=[("7", "♣"), ("2", "♦")],
            buttons_html='<button id="FOLD" class="action-button">Fold</button><button id="CHECK" class="action-button">Check</button>',
            modal_html=(
                '<div id="general-popup" role="dialog">'
                '<div>¿Seguro que quieres foldear? Puedes hacer Check sin coste alguno.</div>'
                '<button id="popup-check" style="width:80px;height:30px;">Check</button>'
                '</div>'
            ),
        ))
        resolved = bot.resolver_popup_fold_check_gratis(page)
        self.assertTrue(resolved)
        clicked = page.evaluate("window.__clicked")
        self.assertIn("popup-check", clicked)
        page.close()

    def test_03_apuesta_rival_call(self):
        page = self._browser.new_page()
        page.set_content(_build_table_html(
            hero_cards=[("Q", "♠"), ("Q", "♥")],
            board_cards=[("Q", "♦"), ("8", "♣"), ("2", "♠")],
            pot="140",
            opp_bet="40",
            buttons_html=(
                '<button id="FOLD" class="action-button">Fold</button>'
                '<button id="CALL" class="action-button"><span class="action-title">Call</span><span class="action-value">40</span></button>'
                '<input class="betting-input" value="80" />'
                '<button id="RAISE" class="action-button">Raise</button>'
            ),
        ))
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        self.assertEqual(dom["apuestas_rivales"], "40")
        res = bot.ejecutar_accion(page, PokerDecision(estado_pantalla="EN_MESA_JUEGO", es_mi_turno=True, accion="CALL", monto_apuesta=40, confianza=0.9, evaluacion="Set"), dom_data=dom)
        self.assertEqual(res, "CALL")
        self.assertIn("CALL", page.evaluate("window.__clicked"))
        page.close()

    def test_04_all_in(self):
        page = self._browser.new_page()
        page.set_content(_build_table_html(
            hero_cards=[("A", "♠"), ("A", "♣")],
            pot="300",
            hero_stack="120",
            opp_bet="120",
            buttons_html=(
                '<button id="FOLD" class="action-button">Fold</button>'
                '<button id="ALLIN" class="action-button">All-in</button>'
            ),
        ))
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        res = bot.ejecutar_accion(page, PokerDecision(estado_pantalla="EN_MESA_JUEGO", es_mi_turno=True, accion="ALL_IN", monto_apuesta=120, confianza=0.99, evaluacion="AA"), dom_data=dom)
        self.assertEqual(res, "ALL_IN")
        self.assertIn("ALLIN", page.evaluate("window.__clicked"))
        page.close()

    def test_05_fin_de_mano_sin_botones_turno(self):
        page = self._browser.new_page()
        page.set_content(_build_table_html(
            hero_cards=[("K", "♠"), ("J", "♠")],
            board_cards=[("A", "♠"), ("T", "♠"), ("4", "♦"), ("2", "♣"), ("9", "♥")],
            pot="240",
            buttons_html="",
            my_turn=False,
        ))
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        self.assertFalse(dom["es_mi_turno"])
        self.assertEqual(dom["botones_visibles"], "")
        page.close()

    def test_06_fin_de_partida_modal_replay(self):
        page = self._browser.new_page()
        page.set_content(_build_table_html(
            hero_cards=[],
            buttons_html="",
            my_turn=False,
            modal_html=(
                '<div id="general-popup" role="dialog">'
                '<button id="general-popup-button-action-REPLAY">Jugar de nuevo</button>'
                '</div>'
            ),
        ))
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        self.assertFalse(dom["es_mi_turno"])
        page.locator("#general-popup-button-action-REPLAY").click()
        self.assertIn("general-popup-button-action-REPLAY", page.evaluate("window.__clicked"))
        page.close()

    def test_07_captura_real_html_ipoker_mesa_vivo_captura_1(self):
        html_captura_1 = """<!DOCTYPE html><html><body>
        <div class="table-area">
          <div class="player-area player-seat-6">
            <div class="player-nameplate"><div class="text-block nickname"><div class="target">wallhaxor707</div></div><div id='chips-wallhaxor707"-"4' class="text-block amount with-badge">€3.95</div></div>
            <div class="player-bet"><div class="bet player-bet-6"><div class="amount-cont"><div id="player-bet-4" class="amount">€0.01</div></div></div></div>
          </div>
          <div class="player-area player-seat-1">
            <div class="player-nameplate"><div class="text-block nickname long-nickname"><div class="target">justinbeekeeper</div></div><div id='chips-justinbeekeeper"-"5' class="text-block amount with-badge">€3.98</div></div>
            <div class="player-bet"><div class="bet player-bet-1"><div class="amount-cont"><div id="player-bet-5" class="amount">€0.02</div></div></div></div>
          </div>
          <div class="player-area player-seat-3"><div class="player-nameplate inactive"><div class="target">buster400</div></div></div>
          <div class="player-area my-player player-seat-4">
            <div class="cards-holder-hero"><div class="card card1"><img src="../../resources/images/cards-classic-assets/c7.svg"></div><div class="card card2"><img src="../../resources/images/cards-classic-assets/s9.svg"></div></div>
            <div class="player-nameplate"><div class="target">Belnebu</div><div class="amount">€0.39</div></div>
          </div>
          <div class="total-pot"><span class="total-pot-amount">€0.03</span></div>
        </div>
        </body></html>"""
        page = self._browser.new_page()
        page.set_content(html_captura_1)
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        self.assertIn("7♣", dom["mis_cartas"])
        self.assertIn("9♠", dom["mis_cartas"])
        self.assertEqual(dom["bote"], "€0.03")

        jugadores = dom["jugadores_mesa"]
        nicks = [j["nickname"] for j in jugadores]
        self.assertIn("wallhaxor707", nicks)
        self.assertIn("justinbeekeeper", nicks)
        self.assertIn("Belnebu", nicks)
        page.close()

    def test_08_captura_real_html_ipoker_mesa_vivo_captura_2(self):
        html_captura_2 = """<!DOCTYPE html><html><body>
        <div class="table-area">
          <div class="player-area player-seat-6">
            <div class="player-nameplate"><div class="target">wallhaxor707</div><div class="amount">€4</div></div>
            <div class="game-position game-position-6"><div id="dealer-seat-4" class="dealer"></div></div>
            <div class="player-bet"><div class="bet player-bet-6"><div class="amount">€0.04</div></div></div>
          </div>
          <div class="player-area player-seat-1">
            <div class="player-nameplate"><div class="text-countdown">Time: 18</div><div id='chips-justinbeekeeper"-"5' class="amount">€3.88</div></div>
            <div class="turn-to-act-indicator"></div>
          </div>
          <div class="player-area my-player player-seat-4">
            <div class="cards-holder-hero"><div class="card card1"><img src="../../resources/images/cards-classic-assets/c7.svg"></div><div class="card card2"><img src="../../resources/images/cards-classic-assets/s2.svg"></div></div>
            <div class="player-nameplate"><div class="target">Belnebu</div><div class="amount">€0.37</div></div>
          </div>
          <div class="community-cards"><div class="cardset-community">
            <div class="card"><img src="../../resources/images/cards-classic-assets/c6.svg"></div>
            <div class="card"><img src="../../resources/images/cards-classic-assets/d5.svg"></div>
            <div class="card"><img src="../../resources/images/cards-classic-assets/hk.svg"></div>
            <div class="card"><img src="../../resources/images/cards-classic-assets/ca.svg"></div>
          </div></div>
          <div class="total-pot"><span class="total-pot-amount">€0.10</span></div>
        </div>
        </body></html>"""
        page = self._browser.new_page()
        page.set_content(html_captura_2)
        dom = page.evaluate(bot.JS_EXTRAER_ESTADO_DOM)
        self.assertIn("6♣", dom["cartas_comunitarias"])
        self.assertIn("5♦", dom["cartas_comunitarias"])
        self.assertIn("K♥", dom["cartas_comunitarias"])
        self.assertIn("A♣", dom["cartas_comunitarias"])

        jugadores = dom["jugadores_mesa"]
        nicks = [j["nickname"] for j in jugadores]
        self.assertIn("justinbeekeeper", nicks) # Extracted successfully from ID even when nickname text was "Time: 18"!
        self.assertIn("wallhaxor707", nicks)

        dealer_p = next((j for j in jugadores if j["is_dealer"]), None)
        self.assertIsNotNone(dealer_p)
        self.assertEqual(dealer_p["nickname"], "wallhaxor707")
        page.close()


if __name__ == "__main__":
    unittest.main()
