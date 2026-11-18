import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot import GameTracker, procesar_delta_player_states
from opponents_profiler import (
    MIN_SAMPLE_FOR_CLASSIFICATION,
    OpponentProfiler,
    wilson_ci,
)


class TestFase4ProfilerAndAttribution(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmpdir.name) / "test_fase4.db"
        self.profiler = OpponentProfiler(db_path=self.db_path)

    def tearDown(self):
        try:
            self.profiler.close()
            from sqlalchemy.orm import close_all_sessions
            close_all_sessions()
        except Exception:
            pass
        try:
            self.tmpdir.cleanup()
        except Exception:
            pass

    def test_01_atribucion_3max_por_delta_player_states(self):
        t = GameTracker()
        t.hand_number = 10
        t.current_street = "PREFLOP"

        # Delta 1: RivalA abre a 40, RivalB iguala 40, Hero en BB tiene 20
        msg1 = {
            "playerStates": [
                {"playerCode": "H1", "nickname": "Hero", "seat": 1, "bet": 20, "status": "ACTIVE", "isHero": True},
                {"playerCode": "R1", "nickname": "RivalA", "seat": 2, "bet": 40, "status": "ACTIVE"},
                {"playerCode": "R2", "nickname": "RivalB", "seat": 3, "bet": 40, "status": "ACTIVE"},
            ]
        }
        atr1 = procesar_delta_player_states(msg1, t, self.profiler, hero_nick="Hero")
        self.assertEqual(atr1, [("R1", "RivalA", "RAISE"), ("R2", "RivalB", "CALL")])
        self.assertEqual(len(t.active_opponents), 2)

        # Delta 2: Flop, apuestas de calle reiniciadas a 0; RivalA hace CBet 60, RivalB hace FOLD
        t.current_street = "POSTFLOP"
        for st in t.prev_player_states.values():
            st["bet"] = 0.0
        msg2 = {
            "playerStates": [
                {"playerCode": "R1", "nickname": "RivalA", "seat": 2, "bet": 60, "status": "ACTIVE"},
                {"playerCode": "R2", "nickname": "RivalB", "seat": 3, "bet": 0, "status": "FOLD"},
            ]
        }
        atr2 = procesar_delta_player_states(msg2, t, self.profiler, hero_nick="Hero")
        self.assertEqual(atr2, [("R1", "RivalA", "BET"), ("R2", "RivalB", "FOLD")])

        p_a = self.profiler.obtener_perfil_explotativo("R1", "RivalA")
        p_b = self.profiler.obtener_perfil_explotativo("R2", "RivalB")
        self.assertEqual(p_a["manos"], 1)
        self.assertEqual(p_a["pfr_pct"], 100.0)
        self.assertEqual(p_a["cbet_pct"], 100.0)
        self.assertEqual(p_b["manos"], 1)
        self.assertEqual(p_b["pfr_pct"], 0.0)
        self.assertEqual(p_b["fold_to_cbet_pct"], 100.0)

    def test_02_banderas_por_mano_sin_doble_conteo_y_check_vs_call(self):
        # Misma mano: open raise + call 3-bet + incrementar_manos_rival -> cuenta 1 mano, 1 PFR, 1 VPIP
        self.profiler.registrar_accion_rival("P1", "Shark", "RAISE", calle="PREFLOP", hand_id=55)
        self.profiler.registrar_accion_rival("P1", "Shark", "CALL", calle="PREFLOP", hand_id=55)
        self.profiler.incrementar_manos_rival("P1", "Shark", hand_id=55)

        # Postflop: 1 CHECK y 1 CALL separados correctamente
        self.profiler.registrar_accion_rival("P1", "Shark", "CHECK", calle="POSTFLOP", hand_id=55)
        self.profiler.registrar_accion_rival("P1", "Shark", "CALL", calle="POSTFLOP", hand_id=55)
        self.profiler.registrar_accion_rival("P1", "Shark", "BET", calle="POSTFLOP", hand_id=55)

        p = self.profiler.obtener_perfil_explotativo("P1", "Shark")
        self.assertEqual(p["manos"], 1)
        # AF postflop = bets_post (1) / max(1, calls_post (1)) = 1.0
        self.assertEqual(p["agresividad"], 1.0)
        # AFq postflop = 1 / (1 bet + 1 call + 1 check) = 33.3%
        self.assertAlmostEqual(p["aggression_freq_pct"], 33.3, places=1)

    def test_03_etiquetas_honestas_sin_datos_y_wilson_ci(self):
        self.assertEqual(MIN_SAMPLE_FOR_CLASSIFICATION, 30)
        for h in range(1, 20):
            self.profiler.registrar_accion_rival("P_SHORT", "Shorty", "RAISE", calle="PREFLOP", hand_id=h)
        p_short = self.profiler.obtener_perfil_explotativo("P_SHORT", "Shorty")
        self.assertNotEqual(p_short["estilo"], "SIN DATOS")
        self.assertIn("Muestra", p_short["recomendacion_tactica"])

        low, high = wilson_ci(15, 30)
        self.assertLess(low, 50.0)
        self.assertGreater(high, 50.0)
        self.assertIn("vpip_smoothed_pct", p_short)
        self.assertIn("vpip_ci95", p_short)

    def test_04_sqlite_wal_y_migraciones_idempotentes(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.db_path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            vers = conn.execute("SELECT version FROM schema_migrations").fetchall()
            self.assertTrue(len(vers) >= 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
