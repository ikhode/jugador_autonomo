"""
Pruebas unitarias para verificar correcciones GTO, clasificaciones preflop,
combinatoria de mesa y decisiones heurísticas sin Overfold.
"""
import unittest
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
from poker_gto_engine import (
    clasificar_mano_preflop,
    evaluar_combinacion_mano_y_mesa,
)
from bot import decidir_accion_rapida


class TestGTOFixesAndHeuristicDecisions(unittest.TestCase):
    """Verifica que no ocurran sobre-descartes de EV ni confusiones de parejitas de mesa."""

    def test_preflop_tier_classifications(self):
        qjs = clasificar_mano_preflop("Q♠ J♠")
        self.assertIn(qjs["tier"], ("A", "B"))
        self.assertGreaterEqual(qjs["score"], 76)

        ajo = clasificar_mano_preflop("J♠ A♥")
        self.assertEqual(ajo["tier"], "A")

        qts = clasificar_mano_preflop("Q♦ 10♦")
        self.assertEqual(qts["tier"], "B")

        trash = clasificar_mano_preflop("3♠ 2♦")
        self.assertEqual(trash["tier"], "BASURA")

    def test_board_pair_vs_pocket_pair_distinction(self):
        # A4o en tablero K-K-6-8-7 -> Pareja compartida en mesa
        res_a4 = evaluar_combinacion_mano_y_mesa("A♠ 4♥", "K♠ K♦ 6♥ 8♠ 7♣")
        self.assertFalse(res_a4["es_pareja_en_mano"])
        self.assertTrue(res_a4["pareja_en_mesa"])
        self.assertEqual(res_a4["categoria"], "PAREJA COMPARTIDA EN MESA (Board Pair)")

        # 810 en tablero 3-8-J -> Pareja conectada (Middle/Bottom Pair)
        res_810 = evaluar_combinacion_mano_y_mesa("8♥ 10♣", "3♠ 8♣ J♣")
        self.assertFalse(res_810["es_pareja_en_mano"])
        self.assertIn("Middle/Bottom Pair", res_810["categoria"])

    def test_no_overfold_preflop_premium_hands(self):
        # Turno 38 log original: AJo con bote 470 y 380 a igualar NO debe ser FOLD
        dom_ajo = {
            "mis_cartas": "J♠ A♥",
            "cartas_comunitarias": "",
            "bote": 470,
            "a_igualar": 380,
            "mi_stack": 500,
            "stack_rival": 500,
            "es_mi_turno": True,
            "botones_visibles": "FOLD | CALL | RAISE",
        }
        dec_ajo = decidir_accion_rapida(dom_ajo)
        self.assertIn(dec_ajo.accion, ("CALL", "RAISE"))
        self.assertNotEqual(dec_ajo.accion, "FOLD")

        # Turno 39 log original: QJs con bote 190 y 140 a igualar NO debe ser FOLD
        dom_qjs = {
            "mis_cartas": "Q♠ J♠",
            "cartas_comunitarias": "",
            "bote": 190,
            "a_igualar": 140,
            "mi_stack": 500,
            "stack_rival": 500,
            "es_mi_turno": True,
            "botones_visibles": "FOLD | CALL | RAISE",
        }
        dec_qjs = decidir_accion_rapida(dom_qjs)
        self.assertIn(dec_qjs.accion, ("CALL", "RAISE"))
        self.assertNotEqual(dec_qjs.accion, "FOLD")

    def test_pot_control_middle_pair_postflop(self):
        # Pareja media en postflop con paseo gratis disponible debe ser CHECK, no RAISE
        dom_mid = {
            "mis_cartas": "8♥ 10♣",
            "cartas_comunitarias": "3♠ 8♣ J♣",
            "bote": 40,
            "a_igualar": 0,
            "mi_stack": 500,
            "stack_rival": 500,
            "es_mi_turno": True,
            "botones_visibles": "CHECK | BET",
        }
        dec_mid = decidir_accion_rapida(dom_mid)
        self.assertEqual(dec_mid.accion, "CHECK")

    def test_selectors_contain_ipoker_variants(self):
        call_sel = config.SELECTORS.get("btn_call", "")
        self.assertIn("Igualar", call_sel)
        self.assertIn("Pagar", call_sel)

        allin_sel = config.SELECTORS.get("btn_allin", "")
        self.assertIn("All-in", allin_sel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
