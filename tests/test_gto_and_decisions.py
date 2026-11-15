import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
from poker_experience_memory import PokerExperienceMemory
from poker_gto_engine import (
    analizar_textura_mesa_y_blockers,
    evaluar_combinacion_mano_y_mesa,
    parsear_carta,
)


class TestGTOAndDecisions(unittest.TestCase):
    def test_parsear_carta(self):
        self.assertEqual(parsear_carta("A♠"), ("A", "S"))
        self.assertEqual(parsear_carta("Th"), ("T", "H"))
        self.assertEqual(parsear_carta("10♦"), ("T", "D"))
        self.assertEqual(parsear_carta("2♣"), ("2", "C"))
        self.assertEqual(parsear_carta("XX"), ("", ""))

    def test_evaluar_combinacion_mano_y_mesa_variantes_y_proyectos(self):
        # Board pair vs pocket pair (y mano que juega la mesa)
        bp = evaluar_combinacion_mano_y_mesa("4♠ 3♦", "K♣ K♥ Q♠ J♦ 9♣")
        self.assertEqual(bp["categoria"], "PAREJA COMPARTIDA EN MESA (Board Pair)")
        self.assertTrue(bp["pareja_en_mesa"])
        self.assertTrue(bp["mano_en_mesa"])

        pp = evaluar_combinacion_mano_y_mesa("Q♠ Q♦", "K♣ 7♥ 2♠")
        self.assertEqual(pp["categoria"], "PAREJA EN MANO (Pocket Pair)")
        self.assertFalse(pp["pareja_en_mesa"])
        self.assertTrue(pp["es_pareja_en_mano"])

        # Color
        fl = evaluar_combinacion_mano_y_mesa("A♠ 9♠", "K♠ 7♠ 2♠")
        self.assertIn("COLOR", fl["categoria"])
        self.assertTrue(fl["es_color"])

        # Escalera y rueda A-2-3-4-5
        st = evaluar_combinacion_mano_y_mesa("9♠ 8♦", "7♣ 6♥ 5♠")
        self.assertIn("ESCALERA", st["categoria"])
        self.assertTrue(st["es_escalera"])

        wheel = evaluar_combinacion_mano_y_mesa("A♠ 2♦", "3♣ 4♥ 5♠")
        self.assertIn("ESCALERA", wheel["categoria"])
        self.assertTrue(wheel["es_escalera"])

        # Proyectos: combo draw (flush draw + OESD)
        draw = evaluar_combinacion_mano_y_mesa("9♠ 8♠", "7♠ 6♦ 2♠")
        self.assertTrue(draw["proyecto_color"])
        self.assertTrue(draw["proyecto_oesd"])
        self.assertTrue(draw["es_combo_draw"])
        self.assertGreaterEqual(draw["outs_totales"], 12)

    def test_analizar_textura_mesa_y_blockers(self):
        tex = analizar_textura_mesa_y_blockers("A♠ K♦", "Q♠ J♠ 4♠")
        self.assertIn("MESA", tex["textura_mesa"])
        self.assertTrue(tex["posible_color"])
        self.assertEqual(tex["blockers_ases"], 1)
        self.assertEqual(tex["blockers_reyes"], 1)

    def test_decidir_accion_rapida_casos_clave(self):
        # Top pair vs bottom pair
        tp = bot.decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "A♠ K♦",
            "cartas_comunitarias": "A♣ 8♥ 3♦",
            "bote": "60",
            "mi_stack": "500",
            "stack_rival": "500",
            "big_blind": 20,
            "apuestas_rivales": "",
            "botones_visibles": "CHECK, BET, RAISE",
            "posicion": "BTN",
        })
        self.assertIn(tp.accion, ("RAISE", "BET", "CHECK"))

        dom_bp = {
            "es_mi_turno": True,
            "mis_cartas": "3♠ 2♦",
            "cartas_comunitarias": "A♣ K♥ 3♦",
            "bote": "100",
            "mi_stack": "500",
            "stack_rival": "500",
            "big_blind": 20,
            "a_igualar": "250",
            "apuestas_rivales": "250",
            "botones_visibles": "FOLD, CALL (250), RAISE",
            "opciones_disponibles": "FOLD, CALL (250), RAISE",
            "posicion": "BB",
        }
        bot.actualizar_dom_data_conservando_contexto(dom_bp, None)
        bp = bot.decidir_accion_rapida(dom_bp)
        self.assertEqual(bp.accion, "FOLD")

        # Check gratis vs fold
        free_check = bot.decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "7♠ 2♦",
            "cartas_comunitarias": "A♣ K♥ Q♦",
            "bote": "60",
            "mi_stack": "500",
            "stack_rival": "500",
            "big_blind": 20,
            "apuestas_rivales": "",
            "botones_visibles": "FOLD, CHECK",
            "posicion": "BB",
        })
        self.assertEqual(free_check.accion, "CHECK")

        # Push/fold < 10 BB con mano jugable
        pf = bot.decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "A♠ Q♦",
            "cartas_comunitarias": "",
            "bote": "30",
            "mi_stack": "160",
            "stack_rival": "160",
            "big_blind": 20,
            "apuestas_rivales": "",
            "botones_visibles": "FOLD, CALL (20), RAISE, ALLIN",
            "posicion": "BTN",
        })
        self.assertIn(pf.accion, ("ALL_IN", "RAISE"))

        # Sizing > 60 sin límite artificial (ej. bote grande -> raise > 60)
        big_sizing = bot.decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "A♠ A♦",
            "cartas_comunitarias": "A♣ 7♥ 2♠",
            "bote": "400",
            "mi_stack": "1200",
            "stack_rival": "1200",
            "big_blind": 20,
            "apuestas_rivales": "",
            "botones_visibles": "CHECK, RAISE",
            "posicion": "BTN",
        })
        self.assertEqual(big_sizing.accion, "RAISE")
        self.assertGreater(big_sizing.monto_apuesta, 60)

    def test_validar_accion_y_memoria_y_dom_contexto(self):
        # validar_accion devuelve (accion_legal, monto_legal, motivo)
        acc1, _, motivo1 = bot.validar_accion("CHECK", 0, {"botones_visibles": "FOLD, CALL (20), RAISE", "apuestas_rivales": "20"})
        self.assertNotEqual(acc1, "CHECK")
        self.assertTrue(bool(motivo1))

        acc2, monto2, _ = bot.validar_accion("RAISE", 5000, {"botones_visibles": "FOLD, CALL (20), RAISE", "mi_stack": "300", "big_blind": 20})
        self.assertEqual(acc2, "RAISE")
        self.assertLessEqual(monto2, 300)

        # PokerExperienceMemory filtrado por similarity_key
        with tempfile.TemporaryDirectory() as tmp:
            mem = PokerExperienceMemory(db_path=str(Path(tmp) / "mem.db"))
            mem.registrar_error_pasado(
                hand_tier="PREMIUM (Tier S)",
                board_texture="MESA SECA",
                opp_archetype="Calling Station",
                action_taken="RAISE",
                loss_amount=240,
                lesson_learned="No farolear vs Calling Station",
            )
            hits = mem.buscar_errores_similares(
                hand_tier="PREMIUM (Tier S)",
                board_texture="MESA SECA",
                opp_archetype="Calling Station",
            )
            self.assertEqual(len(hits), 1)
            misses = mem.buscar_errores_similares(
                hand_tier="BASURA (Fold)",
                board_texture="MESA COORDINADA",
                opp_archetype="Nit",
            )
            self.assertEqual(len(misses), 0)
            mem.close()

        # actualizar_dom_data_conservando_contexto
        dom = {
            "mis_cartas": "",
            "historial_mano": ["H1"],
            "perfil_rival": {"estilo": "Nit"},
            "perfiles_oponentes": [{"seat": 1, "estilo": "Nit"}],
            "gto_frecuencia": {"BET": 50},
        }
        bot.actualizar_dom_data_conservando_contexto(dom, {"mis_cartas": "A♠ K♦", "bote": "60", "big_blind": 20})
        self.assertEqual(dom["mis_cartas"], "A♠ K♦")
        self.assertEqual(dom["historial_mano"], ["H1"])
        self.assertEqual(dom["perfil_rival"], {"estilo": "Nit"})
        self.assertIn("gto_frecuencia", dom)

    def test_resiliencia_y_logging_estructurado(self):
        # Backoff exponencial
        self.assertEqual(bot.calcular_backoff_segundos(1, base=0.5, cap=16.0), 0.5)
        self.assertEqual(bot.calcular_backoff_segundos(2, base=0.5, cap=16.0), 1.0)
        self.assertEqual(bot.calcular_backoff_segundos(6, base=0.5, cap=4.0), 4.0)

        # Detección de desconexión
        self.assertTrue(bot.detectar_desconexion({"modales_visibles": "Connection lost"}))
        self.assertTrue(bot.detectar_desconexion(err=RuntimeError("net::ERR_INTERNET_DISCONNECTED")))
        self.assertFalse(bot.detectar_desconexion({"modales_visibles": ""}))

        # ControladorResiliencia: parada segura y watchdog
        ctrl = bot.ControladorResiliencia(max_consecutive_errors=3, hand_watchdog_timeout_sec=10.0)
        self.assertEqual(ctrl.registrar_error()[0], False)
        self.assertEqual(ctrl.registrar_error()[0], False)
        self.assertEqual(ctrl.registrar_error()[0], True)
        ctrl.registrar_exito()
        self.assertEqual(ctrl.consecutive_errors, 0)

        ctrl.actualizar_mano_watchdog("1:AsKd", now=100.0)
        self.assertFalse(ctrl.verificar_watchdog_mano(now=105.0))
        self.assertTrue(ctrl.verificar_watchdog_mano(now=115.0))

        # Logging JSON estructurado
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "bot_decisions.jsonl"
            rec = bot.log_json_estructurado(
                "decision",
                hand_id="hand_42",
                decision_id="dec_42_1",
                payload={"executed_action": "CALL"},
                log_path=str(log_file),
            )
            self.assertEqual(rec["hand_id"], "hand_42")
            self.assertEqual(rec["decision_id"], "dec_42_1")
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["executed_action"], "CALL")

    def test_fixes_post_log_tier_allin_fold_parse_num_y_db_models(self):
        # 1. modo_allin_or_fold no debe lanzar UnboundLocalError por tier
        dom_aof = {
            "es_mi_turno": True,
            "mis_cartas": "A♦ K♣",
            "cartas_comunitarias": "J♥ 10♥ 9♣",
            "bote": "930",
            "mi_stack": "400",
            "big_blind": 20,
            "botones_visibles": "FOLD, ALLIN",
            "posicion": "BTN",
        }
        bot.actualizar_dom_data_conservando_contexto(dom_aof, None)
        dec_aof = bot.decidir_accion_rapida(dom_aof)
        self.assertIn(dec_aof.accion, ("ALL_IN", "FOLD"))

        # 2. _parse_num_dom no concatena dígitos separados ("40 | 20" -> 40.0, no 4020)
        self.assertEqual(bot._parse_num_dom("40 | 20"), 40.0)
        self.assertEqual(bot._parse_num_dom("500 | 500"), 500.0)

        # 3. db_models incluye SchemaMigration alineado con analytics_db
        import db_models
        self.assertIn("schema_migrations", db_models.Base.metadata.tables)


if __name__ == "__main__":
    unittest.main()
