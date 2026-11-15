import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
from poker_experience_memory import (
    PokerExperienceMemory,
    _construir_similarity_key,
    _normalizar_textura_token,
)


class TestExperienceMemoryProduction(unittest.TestCase):
    def test_01_semantic_board_texture_keys_no_collision(self):
        self.assertEqual(_normalizar_textura_token("MESA SECA / DISPERSA"), "SECA")
        self.assertEqual(_normalizar_textura_token("MESA CONECTADA / PELIGROSA"), "COORDINADA")
        self.assertEqual(_normalizar_textura_token("MESA EMPAREJADA"), "EMPAREJADA")

        k_seca = _construir_similarity_key("B (Fuerte)", "MESA SECA / DISPERSA", "Nit")
        k_conn = _construir_similarity_key("B (Fuerte)", "MESA CONECTADA / PELIGROSA", "Nit")
        self.assertNotEqual(k_seca, k_conn)
        self.assertEqual(k_seca, "B|SECA|NIT")
        self.assertEqual(k_conn, "B|COORDINADA|NIT")

    def test_02_deduplicated_upsert_and_reinforcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = PokerExperienceMemory(db_path=Path(tmp) / "mem_upsert.db")
            r1 = mem.registrar_error_pasado(
                hand_tier="B",
                board_texture="MESA CONECTADA / PELIGROSA",
                opp_archetype="Calling Station",
                action_taken="RAISE",
                loss_amount=100,
                lesson_learned="No farolear river vs Calling Station",
                street="RIVER",
                position="BTN",
                big_blind=20.0,
                ev_lost_bb=3.0,
            )
            r2 = mem.registrar_error_pasado(
                hand_tier="B",
                board_texture="MESA CONECTADA / PELIGROSA",
                opp_archetype="Calling Station",
                action_taken="RAISE",
                loss_amount=160,
                lesson_learned="No farolear river vs Calling Station (reforzado)",
                street="RIVER",
                position="BTN",
                big_blind=20.0,
                ev_lost_bb=4.5,
            )
            self.assertEqual(r1["status"], "INSERTED")
            self.assertEqual(r2["status"], "UPDATED")
            self.assertEqual(r2["occurrences"], 2)

            hits = mem.buscar_errores_similares("B", "MESA CONECTADA / PELIGROSA", "Calling Station", street="RIVER")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["occurrences"], 2)
            self.assertAlmostEqual(hits[0]["ev_lost_bb"], 7.5, places=2)
            self.assertEqual(hits[0]["loss_amount"], 160)

            stats = mem.obtener_estadisticas_memoria()
            self.assertEqual(stats["total_lessons"], 1)
            self.assertEqual(stats["total_occurrences"], 2)
            self.assertAlmostEqual(stats["total_ev_lost_bb"], 7.5, places=2)
            mem.close()

    def test_03_anti_variance_guard_does_not_poison_good_ev_plays(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = PokerExperienceMemory(db_path=Path(tmp) / "mem_var.db")
            # Buena jugada (+EV / OPTIMA) que perdió por varianza -> NO se registra
            res_var = mem.auto_evaluar_y_registrar_mano_perdedora(
                hand_tier="S",
                board_texture="MESA SECA",
                opp_archetype="Maniac",
                accion_tomada="ALL_IN",
                net_chips=-500.0,
                ev_diff=4.2,
                street="PREFLOP",
                decision_quality="OPTIMA",
            )
            self.assertIsNone(res_var)
            self.assertEqual(mem.obtener_estadisticas_memoria()["total_lessons"], 0)

            # Error real (-EV / BLUNDER_EV) -> SÍ se registra
            res_err = mem.auto_evaluar_y_registrar_mano_perdedora(
                hand_tier="C",
                board_texture="MESA CONECTADA",
                opp_archetype="Nit",
                accion_tomada="ALL_IN",
                net_chips=-300.0,
                ev_diff=-8.5,
                street="TURN",
                decision_quality="BLUNDER_EV",
            )
            self.assertIsNotNone(res_err)
            self.assertEqual(mem.obtener_estadisticas_memoria()["total_lessons"], 1)
            mem.close()

    def test_04_ranked_retrieval_and_legacy_db_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy_db = Path(tmp) / "legacy_mem.db"
            conn = sqlite3.connect(legacy_db)
            conn.execute("""
                CREATE TABLE errores_experiencia (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    hand_tier TEXT,
                    board_texture TEXT,
                    opp_archetype TEXT,
                    action_taken TEXT,
                    loss_amount INTEGER,
                    lesson_learned TEXT,
                    similarity_key TEXT
                )
            """)
            conn.execute(
                "INSERT INTO errores_experiencia (timestamp, hand_tier, board_texture, opp_archetype, action_taken, loss_amount, lesson_learned, similarity_key) "
                "VALUES ('2026-11-15 00:00:00', 'B', 'MESA SECA', 'Nit', 'CALL', 80, 'Lección legacy', 'B|SECA|NIT')"
            )
            conn.commit()
            conn.close()

            mem = PokerExperienceMemory(db_path=legacy_db)
            hits = mem.buscar_errores_similares("B", "MESA SECA", "Nit")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["lesson_learned"], "Lección legacy")

            # Añadir lección específica en FLOP con mayor severidad y verificar orden
            mem.registrar_error_pasado(
                hand_tier="B",
                board_texture="MESA SECA",
                opp_archetype="Nit",
                action_taken="RAISE",
                loss_amount=200,
                lesson_learned="No 3-bet bluff flop vs Nit",
                street="FLOP",
                ev_lost_bb=6.0,
            )
            ranked = mem.buscar_errores_similares("B", "MESA SECA", "Nit", limit=2, street="FLOP")
            self.assertEqual(len(ranked), 2)
            self.assertEqual(ranked[0]["action_taken"], "RAISE")
            mem.close()

    def test_05_game_tracker_records_loss_without_explicit_net_chips(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = PokerExperienceMemory(db_path=Path(tmp) / "tracker_mem.db")
            orig_mem = bot.experience_memory
            try:
                bot.experience_memory = mem
                tr = bot.GameTracker()
                tr.last_hand_tier = "B"
                tr.last_board_texture = "MESA CONECTADA"
                tr.last_opp_archetype = "Calling Station"
                tr.last_action_taken = "RAISE"
                tr.last_street = "FLOP"
                tr.last_invested_chips = 120.0
                tr.last_ev_diff = -3.2
                tr.last_decision_quality = "MISTAKE"
                tr.finalizar_mano_actual("Fin de mano previa")
                stats = mem.obtener_estadisticas_memoria()
                self.assertEqual(stats["total_lessons"], 1)
            finally:
                bot.experience_memory = orig_mem
                mem.close()


if __name__ == "__main__":
    unittest.main()
