"""
Suite de Pruebas Unitarias para la Fase 7 (Data Integrity & Hardening):
Verifica:
1. Rechazo de placeholders de asientos y temporizadores por es_nickname_invalido.
2. Integridad de UIDs deterministas en analytics_logger.
3. Estabilidad y unicidad de mapeo seat <-> playerCode en LiveTableMonitor.
4. Clasificación correcta para jugadores nuevos (JUGADOR NUEVO vs Calling Station).
"""
import unittest
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from opponents_profiler import OpponentProfiler, es_nickname_invalido, sanitizar_nickname
from live_table_tracker import LiveTableMonitor
from analytics_logger import generar_uid_determinista


class TestDataIntegrityFase7(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmpdir.name) / "test_integrity.db"
        self.profiler = OpponentProfiler(db_path=self.db_path)

    def tearDown(self):
        try:
            self.profiler._engine.dispose()
        except Exception:
            pass
        self.tmpdir.cleanup()

    def test_01_es_nickname_invalido_placeholders(self):
        """Verifica que Asiento N, Seat N y temporizadores sean rechazados."""
        self.assertTrue(es_nickname_invalido("Asiento 8"))
        self.assertTrue(es_nickname_invalido("Seat 10"))
        self.assertTrue(es_nickname_invalido("Rival_Asiento_2"))
        self.assertTrue(es_nickname_invalido("Time: 14"))
        self.assertFalse(es_nickname_invalido("pantherdream12"))
        self.assertFalse(es_nickname_invalido("Belnebu"))

    def test_02_new_player_classification(self):
        """Verifica que un jugador sin manos o con 0 manos no sea clasificado como Calling Station."""
        perfil = self.profiler.obtener_perfil_explotativo("new_player_01", "NovatoTest")
        self.assertEqual(perfil["estilo"], "JUGADOR NUEVO")

    def test_03_deterministic_uids(self):
        """Verifica que los UIDs de analíticas sean deterministas y únicos."""
        uid1 = generar_uid_determinista("sess_01", "v1.6", 1, "sc_01")
        uid2 = generar_uid_determinista("sess_01", "v1.6", 1, "sc_01")
        uid3 = generar_uid_determinista("sess_01", "v1.6", 2, "sc_01")

        self.assertEqual(uid1, uid2)
        self.assertNotEqual(uid1, uid3)
        self.assertEqual(len(uid1), 24)

    def test_04_live_table_monitor_seat_mapping(self):
        """Verifica la estabilidad del mapeo seat <-> playerCode en LiveTableMonitor."""
        monitor = LiveTableMonitor(self.profiler, max_asientos=6, persist_live_file=False)

        # Simular mensaje 10008
        d_10008 = {
            "player": {
                "seatIndex": 3,
                "playerCode": 9876543,
                "nickname": "VillanoPro",
                "chips": 15.0,
                "removeSeatIndex": -1
            }
        }
        monitor.procesar_ws_10008(d_10008)

        self.assertEqual(monitor.resolver_code_por_asiento(3), "9876543")
        self.assertEqual(monitor.code_to_seat.get("9876543"), 3)


if __name__ == "__main__":
    unittest.main()
