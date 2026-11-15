import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from bot import (
    actualizar_dom_data_conservando_contexto,
    decidir_accion_rapida,
    ejecutar_accion,
    es_mesa_dinero_real,
    validar_accion,
)
from poker_agent import PokerDecision, formatear_perfiles_para_prompt
from poker_experience_memory import PokerExperienceMemory


class TestFase1Fixes(unittest.TestCase):
    def test_01_dom_data_update_preserves_context_and_recalculates_gto(self):
        dom_data = {
            "mis_cartas": "",
            "historial_mano": "Mano #1: Hero BTN sube 40",
            "perfil_rival": {"nickname": "Villano1", "estilo": "LAG", "vpip_pct": 45},
            "perfiles_oponentes": [
                {"asiento": 1, "nickname": "Villano1", "estado": "ACTIVO", "stack": 500},
                {"asiento": 2, "nickname": "Villano2", "estado": "FOLD", "stack": 480},
            ],
            "memoria_errores": [{"hand_tier": "B", "lesson_learned": "No pagar 3-bet OOP"}],
        }
        nuevo_dom = {
            "mis_cartas": "As Kd",
            "cartas_comunitarias": "Ah 7c 2d",
            "bote": "120",
            "a_igualar": "40",
            "mi_stack": "460",
            "botones_visibles": "FOLD | CALL | RAISE",
            "es_mi_turno": True,
        }
        res = actualizar_dom_data_conservando_contexto(dom_data, nuevo_dom)

        self.assertEqual(res["historial_mano"], "Mano #1: Hero BTN sube 40")
        self.assertEqual(res["perfil_rival"]["nickname"], "Villano1")
        self.assertEqual(len(res["perfiles_oponentes"]), 2)
        self.assertEqual(len(res["memoria_errores"]), 1)
        self.assertEqual(res["mis_cartas"], "As Kd")
        self.assertIn("tier", res["gto_preflop"])
        self.assertIn("d100", res["gto_frecuencia"])
        self.assertIn("categoria", res["gto_combinacion"])

    def test_02_experience_memory_filters_by_similarity_key_without_false_positives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = PokerExperienceMemory(db_path=Path(tmpdir) / "mem.db")
            mem.auto_evaluar_y_registrar_mano_perdedora(
                hand_tier="A",
                board_texture="SECO (As 7c 2d)",
                opp_archetype="MANIAC (Muy Agresivo)",
                accion_tomada="CALL",
                net_chips=-180,
                ev_diff=-9.0,
            )
            # Mano ganadora no se registra
            mem.auto_evaluar_y_registrar_mano_perdedora(
                hand_tier="S",
                board_texture="SECO",
                opp_archetype="NIT",
                accion_tomada="RAISE",
                net_chips=120,
                ev_diff=3.0,
            )

            exact = mem.buscar_errores_similares("A", "SECO (Kh 8d 3c)", "MANIAC (Agresivo)")
            self.assertEqual(len(exact), 1)
            self.assertEqual(exact[0]["action_taken"], "CALL")

            # Mismo hand_tier pero distinta textura y arquetipo -> NO debe dar falso positivo
            false_pos_1 = mem.buscar_errores_similares("A", "MOJADO (9h 8h 7s)", "NIT (Muy Pasivo)")
            self.assertEqual(len(false_pos_1), 0)

            # Mismo arquetipo pero distinto hand_tier -> NO debe dar falso positivo
            false_pos_2 = mem.buscar_errores_similares("D", "SECO", "MANIAC")
            self.assertEqual(len(false_pos_2), 0)

    def test_03_formatear_perfiles_multijugador_y_unico(self):
        dom_multi = {
            "big_blind": 20,
            "perfiles_oponentes": [
                {
                    "asiento": 1,
                    "nickname": "SharkMX",
                    "estado": "ACTIVO",
                    "stack": 600,
                    "estilo": "TAG",
                    "manos_jugadas": 42,
                    "vpip_pct": 24,
                    "pfr_pct": 19,
                    "agresividad": 2.4,
                    "recomendacion_tactica": "Respetar 3-bets",
                },
                {
                    "asiento": 2,
                    "nickname": "Fish99",
                    "estado": "FOLD",
                    "stack_bb": 18.5,
                    "estilo": "CALLING STATION",
                    "manos_jugadas": 35,
                    "vpip_pct": 62,
                    "pfr_pct": 8,
                    "agresividad": 0.6,
                    "recomendacion_tactica": "Apostar por valor, no farolear",
                },
            ],
        }
        txt_multi = formatear_perfiles_para_prompt(dom_multi)
        self.assertIn("Asiento 1 (SharkMX) [ACTIVO]", txt_multi)
        self.assertIn("30.0 BB", txt_multi)
        self.assertIn("n=42", txt_multi)
        self.assertIn("Asiento 2 (Fish99) [FOLD]", txt_multi)
        self.assertIn("18.5 BB", txt_multi)
        self.assertIn("CALLING STATION", txt_multi)

        dom_single = {
            "perfil_rival": {
                "nickname": "SoloUno",
                "estilo": "NIT",
                "manos_jugadas": 15,
                "vpip_pct": 12,
                "pfr_pct": 9,
                "agresividad": 1.1,
                "recomendacion_tactica": "Robar ciegas",
            }
        }
        txt_single = formatear_perfiles_para_prompt(dom_single)
        self.assertIn("SoloUno", txt_single)
        self.assertIn("NIT", txt_single)
        self.assertIn("n=15", txt_single)

    def test_04_ejecutar_accion_sin_limite_artificial_60(self):
        target = MagicMock()
        target.url = "https://playmoney.codere.mx/twister"
        target.evaluate.return_value = False
        inp = MagicMock()
        inp.is_visible.return_value = True
        btn = MagicMock()
        btn.is_visible.return_value = True

        def locator_side_effect(sel):
            m = MagicMock()
            m.first = inp if "input" in sel else btn
            return m

        target.locator.side_effect = locator_side_effect
        target.wait_for_function.return_value = True

        decision = PokerDecision(
            evaluacion="Open raise 2BB",
            estrategia_competitiva="GTO",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="RAISE",
            monto_apuesta=40,
            confianza=0.9,
        )
        dom_data = {
            "es_mi_turno": True,
            "mis_cartas": "As Kd",
            "mi_stack": "500",
            "big_blind": "20",
            "min_raise": "40",
            "monto_apuesta_input": "0",
            "botones_visibles": "FOLD | CALL | RAISE",
        }
        res = ejecutar_accion(target, decision, dom_data=dom_data)
        self.assertEqual(res, "RAISE")
        inp.fill.assert_called_once_with("40", timeout=300)

    def test_05_validar_accion_legalidad_y_normalizacion_upper(self):
        # CHECK con apuesta activa -> CALL
        acc, _, motivo = validar_accion("CHECK", 0, {"botones_visibles": "fold | call | raise", "a_igualar": "40"})
        self.assertEqual(acc, "CALL")
        self.assertIsNotNone(motivo)

        # CALL sin apuesta cuando hay CHECK -> CHECK
        acc, _, _ = validar_accion("CALL", 0, {"botones_visibles": "check | bet", "a_igualar": "0"})
        self.assertEqual(acc, "CHECK")

        # FOLD cuando CHECK es gratis -> CHECK
        acc, _, _ = validar_accion("FOLD", 0, {"botones_visibles": "fold | check", "a_igualar": "0"})
        self.assertEqual(acc, "CHECK")

        # RAISE fuera de rango -> clamp a [min_raise, stack]
        acc, monto, motivo = validar_accion(
            "RAISE", 2000, {"botones_visibles": "FOLD | CALL | RAISE", "min_raise": "40", "mi_stack": "380"}
        )
        self.assertEqual(acc, "RAISE")
        self.assertEqual(monto, 380)
        self.assertIsNotNone(motivo)

        # START_GAME/REPLAY nunca con mano activa
        acc, _, _ = validar_accion(
            "REPLAY", 0, {"es_mi_turno": True, "mis_cartas": "As Ah", "botones_visibles": "CHECK | BET", "a_igualar": "0"}
        )
        self.assertEqual(acc, "CHECK")

        # ALL_IN como única opción
        acc, _, _ = validar_accion("RAISE", 100, {"botones_visibles": "FOLD | ALL_IN", "mi_stack": "120"})
        self.assertEqual(acc, "ALL_IN")

        # decidir_accion_rapida normaliza botones en minúsculas
        dec = decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "7h 2c",
            "cartas_comunitarias": "",
            "botones_visibles": "fold | check",
            "a_igualar": "0",
        })
        self.assertEqual(dec.accion, "CHECK")

    def test_06_play_money_only_blocks_real_money_tables(self):
        self.assertTrue(config.PLAY_MONEY_ONLY)
        self.assertTrue(es_mesa_dinero_real({"bote": "€12.50", "mi_stack": "€50"}))
        self.assertTrue(es_mesa_dinero_real({"bote": "$100 USD"}))
        self.assertFalse(es_mesa_dinero_real({"bote": "120", "mi_stack": "1,500"}))

        target = MagicMock()
        target.url = "https://games.codere.mx/real-money/table"
        dec = PokerDecision(
            evaluacion="Test",
            estrategia_competitiva="Test",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="RAISE",
            monto_apuesta=40,
            confianza=0.9,
        )
        res = ejecutar_accion(target, dec, dom_data={"bote": "$100", "botones_visibles": "FOLD | CALL | RAISE"})
        self.assertEqual(res, "WAIT")
        target.locator.assert_not_called()

    def test_07_selectors_not_fragile(self):
        self.assertNotIn("div:has-text('All-in')", config.SELECTORS["allin"])
        self.assertNotIn("div:has-text('Allin')", config.SELECTORS["btn_allin"])
        self.assertNotIn("div:has-text('Jugar de nuevo')", config.SELECTORS["replay"])
        self.assertNotIn("span:has-text('Jugar de nuevo')", config.SELECTORS["btn_replay"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
