"""
Pruebas automatizadas del Sistema de Detección Multijugador y Monitoreo Dinámico en Vivo.
Cubre los 10 escenarios requeridos: mesas de 2, 3, 6 y 9 oponentes, entradas/salidas,
ausentes, folds en mano, cambios de asiento/ID sin duplicados y persistencia tras reinicio.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from opponents_profiler import OpponentProfiler, MIN_SAMPLE_FOR_CLASSIFICATION
from live_table_tracker import LiveTableMonitor


def _crear_dom_jugadores(hero_seat: int, rivales: list[dict], total_seats: int) -> dict:
    jugadores = []
    rivales_by_seat = {r["seat"]: r for r in rivales}
    for s in range(1, total_seats + 1):
        if s == hero_seat:
            jugadores.append({
                "seat": s,
                "is_hero": True,
                "player_id": "HERO_1",
                "nickname": "Josuemontes19",
                "stack": "1500",
                "bet": "20",
                "status": "ACTIVE",
                "is_turn": False,
            })
        elif s in rivales_by_seat:
            r = rivales_by_seat[s]
            jugadores.append({
                "seat": s,
                "is_hero": False,
                "player_id": r.get("player_id", ""),
                "nickname": r.get("nickname", ""),
                "stack": str(r.get("stack", 1000)),
                "bet": str(r.get("bet", 0)),
                "status": r.get("status", "ACTIVE"),
                "is_turn": bool(r.get("is_turn", False)),
            })
        else:
            jugadores.append({
                "seat": s,
                "is_hero": False,
                "player_id": "",
                "nickname": "",
                "stack": "0",
                "bet": "0",
                "status": "EMPTY",
                "is_turn": False,
            })
    return {"jugadores_mesa": jugadores}


class TestMultiplayerLiveHUD(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmpdir.name) / "test_hud.db"
        self.profiler = OpponentProfiler(db_path=self.db_path)

    def tearDown(self):
        try:
            self.profiler._engine.dispose()
        except Exception:
            pass
        self.tmpdir.cleanup()

    def test_01_mesa_con_dos_oponentes_excluye_hero(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="Twister 3-Max", max_asientos=3, hero_nickname="Josuemontes19", persist_live_file=False)
        dom = _crear_dom_jugadores(
            hero_seat=1,
            rivales=[
                {"seat": 2, "nickname": "RivalA", "stack": 1200},
                {"seat": 3, "nickname": "RivalB", "stack": 800},
            ],
            total_seats=3,
        )
        monitor.actualizar_desde_dom(dom)
        conteos = monitor.obtener_conteos_mesa()
        opps = monitor.obtener_perfiles_oponentes()

        self.assertEqual(conteos["numero_oponentes_mesa"], 2)
        self.assertEqual(conteos["asientos_ocupados"], 3)
        self.assertEqual(len(opps), 2)
        self.assertNotIn("Josuemontes19", [o["nickname"] for o in opps])

    def test_02_mesa_con_tres_oponentes(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="4-Max", max_asientos=4, hero_nickname="Josuemontes19", persist_live_file=False)
        dom = _crear_dom_jugadores(
            hero_seat=1,
            rivales=[
                {"seat": 2, "nickname": "R1"},
                {"seat": 3, "nickname": "R2"},
                {"seat": 4, "nickname": "R3"},
            ],
            total_seats=4,
        )
        monitor.actualizar_desde_dom(dom)
        self.assertEqual(monitor.obtener_conteos_mesa()["numero_oponentes_mesa"], 3)
        self.assertEqual(len(monitor.obtener_perfiles_oponentes()), 3)

    def test_03_mesa_con_seis_oponentes(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="Cash 6-Max", max_asientos=6, hero_nickname="Josuemontes19", persist_live_file=False)
        rivales = [{"seat": s, "nickname": f"Villano{s}"} for s in range(2, 8)]
        dom = _crear_dom_jugadores(hero_seat=1, rivales=rivales, total_seats=7)
        monitor.actualizar_desde_dom(dom)
        conteos = monitor.obtener_conteos_mesa()
        self.assertEqual(conteos["max_asientos"], 7)
        self.assertEqual(conteos["numero_oponentes_mesa"], 6)
        self.assertEqual(len(monitor.obtener_perfiles_oponentes()), 6)

    def test_04_mesa_con_nueve_oponentes(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="Full Ring 10-Max", max_asientos=10, hero_nickname="Josuemontes19", persist_live_file=False)
        rivales = [{"seat": s, "nickname": f"Reg{s}"} for s in range(2, 11)]
        dom = _crear_dom_jugadores(hero_seat=1, rivales=rivales, total_seats=10)
        monitor.actualizar_desde_dom(dom)
        conteos = monitor.obtener_conteos_mesa()
        self.assertEqual(conteos["numero_oponentes_mesa"], 9)
        self.assertEqual(len(monitor.obtener_perfiles_oponentes()), 9)

    def test_05_y_06_entrada_y_salida_de_jugador(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="6-Max", max_asientos=6, hero_nickname="Josuemontes19", persist_live_file=False)
        monitor.actualizar_desde_dom(_crear_dom_jugadores(1, [{"seat": 2, "nickname": "Alpha"}], 6))
        self.assertEqual(monitor.obtener_conteos_mesa()["numero_oponentes_mesa"], 1)

        # Entra nuevo jugador en asiento 5
        monitor.actualizar_desde_dom(_crear_dom_jugadores(1, [{"seat": 2, "nickname": "Alpha"}, {"seat": 5, "nickname": "Beta"}], 6))
        self.assertEqual(monitor.obtener_conteos_mesa()["numero_oponentes_mesa"], 2)

        # Sale Alpha del asiento 2
        monitor.actualizar_desde_dom(_crear_dom_jugadores(1, [{"seat": 5, "nickname": "Beta"}], 6))
        opps = monitor.obtener_perfiles_oponentes()
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["nickname"], "Beta")

    def test_07_y_08_jugador_ausente_y_jugador_que_abandona_mano(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="6-Max", max_asientos=6, hero_nickname="Josuemontes19", persist_live_file=False)
        dom = _crear_dom_jugadores(
            hero_seat=1,
            rivales=[
                {"seat": 2, "nickname": "Activo1", "status": "ACTIVE"},
                {"seat": 3, "nickname": "Ausente2", "status": "SIT_OUT"},
                {"seat": 4, "nickname": "Foldeo3", "status": "FOLDED"},
            ],
            total_seats=6,
        )
        monitor.actualizar_desde_dom(dom)
        c = monitor.obtener_conteos_mesa()
        self.assertEqual(c["asientos_ocupados"], 4)      # Hero + 3 sentados
        self.assertEqual(c["jugadores_activos"], 3)      # Hero + Activo1 + Foldeo3 (Ausente2 excluido)
        self.assertEqual(c["jugadores_en_mano"], 2)      # Hero + Activo1
        self.assertEqual(c["numero_oponentes_mesa"], 3)
        self.assertEqual(c["numero_oponentes_en_mano"], 1)

        # Al iniciar nueva mano, Foldeo3 vuelve a ACTIVE pero Ausente2 sigue SIT_OUT
        monitor.iniciar_nueva_mano()
        c2 = monitor.obtener_conteos_mesa()
        self.assertEqual(c2["numero_oponentes_en_mano"], 2)

    def test_09_cambio_identificador_o_asiento_sin_duplicar_ni_mezclar(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="6-Max", max_asientos=6, hero_nickname="Josuemontes19", persist_live_file=False)
        # Primero llega solo por DOM con nickname en asiento 2
        monitor.actualizar_jugador(nickname="SharkMX", seat=2, status="ACTIVE", stack=1000)
        for _ in range(5):
            self.profiler.incrementar_manos_rival("", "SharkMX", seat=2)
            self.profiler.registrar_accion_rival("", "SharkMX", "RAISE", calle="PREFLOP", seat=2)

        # Luego llega por WebSocket con playerCode + nuevo asiento 4
        monitor.actualizar_jugador(player_id="PC_9988", nickname="SharkMX", seat=4, status="ACTIVE", stack=1400)
        self.profiler.registrar_accion_rival("PC_9988", "SharkMX", "CALL", calle="PREFLOP", seat=4)

        opps = monitor.obtener_perfiles_oponentes()
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["seat"], 4)
        self.assertGreaterEqual(opps[0]["manos"], 5)

        historicos = self.profiler.listar_perfiles_historicos()
        self.assertEqual(len(historicos), 1)

    def test_10_reinicio_bot_y_recuperacion_historica_y_muestra_minima(self):
        # Con pocas manos (< 30), no clasifica definitivamente
        for _ in range(3):
            self.profiler.incrementar_manos_rival("P_LOW", "Novato")
            self.profiler.registrar_accion_rival("P_LOW", "Novato", "CALL", calle="PREFLOP")
        perfil_low = self.profiler.obtener_perfil_explotativo("P_LOW", "Novato")
        self.assertNotEqual(perfil_low["estilo"], "SIN DATOS")
        self.assertEqual(perfil_low["confianza_nivel"], "MUESTRA INSUFICIENTE")

        # Jugador con muestra suficiente (>= 30 manos) estilo Calling Station
        for h in range(1, MIN_SAMPLE_FOR_CLASSIFICATION + 1):
            self.profiler.incrementar_manos_rival("P_CS", "StationPro", hand_id=h)
            self.profiler.registrar_accion_rival("P_CS", "StationPro", "CALL", calle="PREFLOP", hand_id=h)
            self.profiler.registrar_accion_rival("P_CS", "StationPro", "CALL", calle="POSTFLOP", hand_id=h)

        # Simular reinicio creando una nueva instancia de OpponentProfiler apuntando a la misma DB
        profiler_reiniciado = OpponentProfiler(db_path=self.db_path)
        try:
            recuperado = profiler_reiniciado.obtener_perfil_explotativo("P_CS", "StationPro")
            self.assertGreaterEqual(recuperado["manos"], MIN_SAMPLE_FOR_CLASSIFICATION)
            self.assertEqual(recuperado["estilo"], "Calling Station")
            self.assertIn("value bet", recuperado["recomendacion_tactica"].lower())
        finally:
            profiler_reiniciado._engine.dispose()

    def test_11_sanitizacion_nicknames_dom_y_unicidad_por_asiento(self):
        monitor = LiveTableMonitor(self.profiler, modalidad="Twister 3-Max", max_asientos=3, hero_nickname="Josuemontes19", persist_live_file=False)
        # Simular frames consecutivos con stack pegado al nombre y un timer temporal en el mismo asiento
        monitor.actualizar_desde_dom(_crear_dom_jugadores(3, [{"seat": 1, "nickname": "DiorSauvage 470", "stack": 470}, {"seat": 2, "nickname": "modeAtaraxia 440", "stack": 440}], 3))
        monitor.actualizar_desde_dom(_crear_dom_jugadores(3, [{"seat": 1, "nickname": "DiorSauvage 510", "stack": 510}, {"seat": 2, "nickname": "modeAtaraxia 490", "stack": 490}], 3))
        monitor.actualizar_desde_dom(_crear_dom_jugadores(3, [{"seat": 1, "nickname": "Time: 12 490", "stack": 490}, {"seat": 2, "nickname": "modeAtaraxia 490", "stack": 490}], 3))

        opps = monitor.obtener_perfiles_oponentes()
        conteos = monitor.obtener_conteos_mesa()
        self.assertLessEqual(conteos["numero_oponentes_mesa"], 2)
        self.assertNotIn("Time: 12", [o["nickname"] for o in opps])
        self.assertIn("modeAtaraxia", [o["nickname"] for o in opps])


if __name__ == "__main__":
    unittest.main(verbosity=2)
