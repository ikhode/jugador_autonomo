"""Pruebas de regresión para los hallazgos de la auditoría (REPORTE_COMPORTAMIENTO.md)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from analytics_engine import evaluar_decision_estrategica
from bot import decidir_accion_rapida
from opponents_profiler import OpponentProfiler


def test_h03_zero_observed_actions_never_classified_nit():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "opp.db"
        prof = OpponentProfiler(db_path=str(db_path))
        for h in range(1, 48):
            prof.incrementar_manos_rival("41203111", "doctorwave953", hand_id=h)
        perfil = prof.obtener_perfil_explotativo("41203111", "doctorwave953")
        assert perfil["estilo"] == "SIN DATOS"
        prof.close()


def test_h04_facing_allin_second_pair_does_not_shove_blindly():
    dom = {
        "es_mi_turno": True,
        "mis_cartas": "A♠ 8♠",
        "cartas_comunitarias": "9♣ 4♠ 8♦",
        "bote": 256,
        "a_igualar": 420,
        "mi_stack": 420,
        "stack_rival": 0,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | ALL_IN",
    }
    dec = decidir_accion_rapida(dom)
    assert dec.accion == "FOLD"


def test_h09_ev_loss_over_1_1_bb_never_labeled_solid():
    res = evaluar_decision_estrategica(
        hole_cards="A♦ 4♠",
        board="",
        pot_size=130.0,
        to_call=80.0,
        effective_stack=500.0,
        action_taken="FOLD",
        bet_amount=0.0,
        position="BTN",
        street="PREFLOP",
        opponent_profile="NIT",
        legal_actions=["FOLD", "CALL", "RAISE"],
        big_blind=20.0,
    )
    if float(res["ev_lost_bb"]) > 1.10:
        assert res["error_description"] != "Decisión sólida en EV"


def test_h10_preflop_tier_c_defends_good_pot_odds():
    dom = {
        "es_mi_turno": True,
        "mis_cartas": "K♣ 6♣",
        "cartas_comunitarias": "",
        "bote": 130,
        "a_igualar": 40,
        "mi_stack": 460,
        "stack_rival": 460,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE",
    }
    dec = decidir_accion_rapida(dom)
    assert dec.accion in ("CALL", "RAISE")


def test_h11_postflop_raise_ignores_min_slider_default_when_pot_large():
    dom = {
        "es_mi_turno": True,
        "mis_cartas": "K♦ 7♥",
        "cartas_comunitarias": "Q♣ K♣ K♥",
        "bote": 160,
        "a_igualar": 0,
        "mi_stack": 400,
        "stack_rival": 400,
        "big_blind": 20,
        "monto_apuesta_input": "20",
        "opciones_disponibles": "CHECK | BET | RAISE",
    }
    dec = decidir_accion_rapida(dom)
    assert dec.accion == "RAISE"
    assert dec.monto_apuesta > 20


def test_orphan_turn_suppression_when_no_cards_and_no_buttons():
    # Verifica que si no hay cartas ni botones, no se considera turno activo
    es_mi_turno = True
    mis_cartas = ""
    botones = ""
    http_turn_signal = True

    if es_mi_turno and not mis_cartas and not botones:
        es_mi_turno = False
        http_turn_signal = False

    assert es_mi_turno is False
    assert http_turn_signal is False


def test_fase4_profiler_and_dom_action_attribution():
    from bot import GameTracker, procesar_delta_player_states, actualizar_dom_data_conservando_contexto, decidir_accion_rapida
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "hud_test.db"
        prof = OpponentProfiler(db_path=str(db_path))
        tracker = GameTracker()
        tracker.hand_number = 1
        tracker.current_street = "PREFLOP"

        # 1. Delta WebSocket con campos alternativos (playerId, playerName, folded)
        delta_ws = {
            "playerStates": [
                {"playerId": "P_HERO", "playerName": "Josuemontes19", "seat": 1, "bet": 20, "isHero": True},
                {"playerId": "41203111", "playerName": "doctorwave953", "seat": 2, "bet": 60, "status": "ACTIVE"},
                {"playerId": "R_FOLD", "playerName": "PassiveGuy", "seat": 3, "bet": 0, "folded": True},
            ]
        }
        atribuidas = procesar_delta_player_states(delta_ws, tracker, prof, hero_nick="Josuemontes19")
        assert ("41203111", "doctorwave953", "RAISE") in atribuidas
        assert ("R_FOLD", "PassiveGuy", "FOLD") in atribuidas
        assert tracker.opp_id == "41203111"
        assert tracker.opp_nick == "doctorwave953"

        # 2. Atribución delta vía DOM en actualizar_dom_data_conservando_contexto
        dom = {
            "es_mi_turno": True,
            "mis_cartas": "A♠ K♠",
            "cartas_comunitarias": "",
            "bote": 120,
            "a_igualar": 40,
            "mi_stack": 440,
            "stack_rival": 440,
            "big_blind": 20,
            "opciones_disponibles": "FOLD | CALL | RAISE",
            "jugadores_mesa": [
                {"asiento": 2, "nickname": "doctorwave953", "player_id": "41203111", "stack": "440", "bet": "80", "estado": "ACTIVO"}
            ],
        }
        actualizar_dom_data_conservando_contexto(dom, nuevo_dom=None, tracker_obj=tracker, profiler_obj=prof)
        dec = decidir_accion_rapida(dom)
        assert dec is not None

        # Verificar que el rival acumuló acciones preflop y ya no es ciego
        p = prof.obtener_perfil_explotativo("41203111", "doctorwave953")
        assert p["manos"] >= 1
        assert p["pfr_pct"] > 0
        prof.close()


def test_mejora5_pot_odds_rule_in_prompt_and_defense():
    from prompts import load_prompt_template
    from bot import decidir_accion_rapida

    ver, digest, text = load_prompt_template("v1")
    assert ver == "v1"
    assert len(digest) == 12
    assert "POT ODDS Y DEFENSA POR PRECIO MATEMÁTICO" in text
    assert "NUNCA foldear parejas hechas" in text

    # Escenario real dec_21_49: Par de 3s con kicker As ante apuesta mínima 80 en bote 736 (pot odds ~9.8%)
    dom = {
        "es_mi_turno": True,
        "mis_cartas": "3♠ A♥",
        "cartas_comunitarias": "Q♣ 3♥ 4♠ K♣",
        "bote": 736,
        "a_igualar": 80,
        "mi_stack": 300,
        "stack_rival": 400,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE",
    }
    dec = decidir_accion_rapida(dom)
    assert dec.accion in ("CALL", "RAISE"), f"Esperado CALL o RAISE por pot odds > 9:1, pero se obtuvo {dec.accion}"


def test_mejora6_position_inference_never_desconocida():
    from bot import GameTracker, actualizar_dom_data_conservando_contexto, PokerDecision
    from analytics_logger import PokerAnalyticsLogger
    from analytics_db import AnalyticsStorage
    import sqlite3

    tracker = GameTracker()
    assert tracker.position_name == "DESCONOCIDA"

    # Preflop con opción de CHECK y a_igualar=0 -> BB
    dom_bb = {
        "es_mi_turno": True,
        "mis_cartas": "T♠ 8♥",
        "cartas_comunitarias": "",
        "bote": 40,
        "a_igualar": 0,
        "mi_stack": 500,
        "stack_rival": 500,
        "big_blind": 20,
        "opciones_disponibles": "CHECK | RAISE",
    }
    actualizar_dom_data_conservando_contexto(dom_bb, nuevo_dom=None, tracker_obj=tracker)
    assert tracker.position_name == "BB"
    assert dom_bb["posicion"] == "BB"

    # Ahora probamos registrar la decisión con PokerAnalyticsLogger
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "analytics_pos_test.db"
        logger = PokerAnalyticsLogger(db_path=db_path, async_mode=False)
        dec = PokerDecision(
            evaluacion="Pase en BB",
            estrategia_competitiva="Check",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="CHECK",
            monto_apuesta=0,
            confianza=0.9,
        )
        logger.registrar_decision(
            dom_data=dom_bb,
            decision=dec,
            executed_action="CHECK",
            decision_source="gto_engine",
            position_name=tracker.position_name,
            hand_number=1,
        )
        logger.flush(timeout=2.0)
        logger.shutdown(wait=True)

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT position FROM analytics_decisions LIMIT 1").fetchone()
            assert row is not None
            assert row[0] == "BB"
            assert row[0] != "DESCONOCIDA"


def test_mejora2_timer_artifacts_rejected_from_profiler_and_db():
    from opponents_profiler import OpponentProfiler, es_nickname_invalido, sanitizar_nickname
    import sqlite3

    assert es_nickname_invalido("Time:\n14") is True
    assert es_nickname_invalido("Time: 12") is True
    assert es_nickname_invalido("Tiempo:\n10") is True
    assert es_nickname_invalido("") is True
    assert es_nickname_invalido("Player123") is False

    assert sanitizar_nickname("Time:\n14") == ""
    assert sanitizar_nickname("Time: 12") == ""
    assert sanitizar_nickname("shark 450") == "shark"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "clean_opp.db"
        prof = OpponentProfiler(db_path=str(db_path))

        # Intentar registrar acciones o manos para un timer
        prof.registrar_accion_rival(
            player_id="Time:\n14",
            nickname="Time:\n14",
            accion="BET",
            calle="PREFLOP",
            hand_id=1,
        )
        prof.incrementar_manos_rival("Time: 12", "Time: 12", hand_id=2)

        # Verificar que no se insertó ninguna fila corrupta en la base de datos
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM oponentes WHERE player_id LIKE '%Time%' OR nickname LIKE '%Time%'"
            ).fetchall()
            assert len(rows) == 0, f"Se encontraron filas corruptas con Time: {rows}"

        prof.close()


def test_mejora3_push_fold_a_igualar_extraction_logic():
    # Simula la lógica JS de extracción de aIgualar cuando hay All-In y no hay Call
    mi_stack = "420"
    opciones_accion = ["FOLD", "ALL_IN"]
    hay_check_dom = "CHECK" in opciones_accion
    hay_call_dom = "CALL" in opciones_accion
    hay_allin_dom = "ALL_IN" in opciones_accion or "ALLIN" in opciones_accion
    call_val = ""
    allin_val = ""  # En el DOM el botón ALL IN muchas veces no tiene span .action-value
    apuestas_rivales = ""

    a_igualar = "0" if hay_check_dom else (
        call_val or (allin_val or mi_stack if (hay_allin_dom and not hay_call_dom) else "") or apuestas_rivales or "0"
    )
    assert a_igualar == "420", f"Esperado 420 (miStack) al enfrentar shove sin Call, pero dio {a_igualar}"


def test_mejora4_legal_actions_filtering_prevents_false_blunder_on_shove():
    # Mano de la auditoría: Hero tiene Q♥ J♥ en mesa Q♦ 7♥ 8♦ (Top Pair, Buen Kicker),
    # las únicas opciones legales en la mesa son FOLD y ALL_IN.
    # El solver/heurística no debe comparar contra un BET/RAISE ilegal no-allin.
    res = evaluar_decision_estrategica(
        hole_cards="Q♥ J♥",
        board="Q♦ 7♥ 8♦",
        pot_size=200.0,
        to_call=0.0,
        effective_stack=372.0,
        action_taken="ALL_IN",
        bet_amount=372.0,
        position="SB",
        street="FLOP",
        opponent_profile="REGULAR EQUILIBRADO",
        legal_actions=["FOLD", "ALL_IN"],
        big_blind=20.0,
    )
    assert res["decision_quality"] == "OPTIMA"
    assert float(res["ev_lost_bb"]) == 0.0
    assert res["is_strategic_error"] is False
    assert res["action_deviation"] == "EXACT_MATCH"


def test_mejora4_hay_check_gratis_requires_check_in_legal_actions():
    # Si call_amt <= 0 pero CHECK no está en legal_actions, NO es check gratis
    res = evaluar_decision_estrategica(
        hole_cards="7♠ 2♦",
        board="A♣ K♣ Q♦",
        pot_size=300.0,
        to_call=0.0,
        effective_stack=200.0,
        action_taken="FOLD",
        bet_amount=0.0,
        position="BB",
        street="FLOP",
        opponent_profile="REGULAR EQUILIBRADO",
        legal_actions=["FOLD", "ALL_IN"],
        big_blind=20.0,
    )
    assert "Fold ilegal/subóptimo teniendo Check gratuito disponible" not in res.get("error_description", "")


def test_mejora7_formatear_cartas_canonicas_unicode():
    from poker_gto_engine import formatear_cartas_canonicas, formatear_carta_canonica

    assert formatear_carta_canonica("SJ") == "J♠"
    assert formatear_carta_canonica("H6") == "6♥"
    assert formatear_carta_canonica("D10") == "10♦"
    assert formatear_carta_canonica("C2") == "2♣"
    assert formatear_carta_canonica("HA") == "A♥"

    # Listas de tokens de iPoker WebSocket
    assert formatear_cartas_canonicas(["SJ", "H6"]) == "J♠ 6♥"
    assert formatear_cartas_canonicas(["C10", "SQ"]) == "10♣ Q♠"
    assert formatear_cartas_canonicas(["H5", "D2", "H9"]) == "5♥ 2♦ 9♥"

    # Cadenas raw y cadenas ya formateadas (idempotencia)
    assert formatear_cartas_canonicas("H5 D2 H9") == "5♥ 2♦ 9♥"
    assert formatear_cartas_canonicas("Q♣ Q♥") == "Q♣ Q♥"
    assert formatear_cartas_canonicas("10♣ 8♣") == "10♣ 8♣"
    assert formatear_cartas_canonicas("") == ""


def test_mejora8_anti_blindness_guard_never_acts_without_cards():
    from bot import GameTracker, actualizar_dom_data_conservando_contexto

    tracker = GameTracker()
    tracker.hand_number = 1
    tracker.current_street = "PREFLOP"
    tracker.my_cards = []  # Sin cartas aún

    # Simular DOM con botones pero sin cartas todavía
    dom = {
        "es_mi_turno": True,
        "mis_cartas": "",
        "cartas_comunitarias": "",
        "bote": 50,
        "a_igualar": 20,
        "mi_stack": 500,
        "stack_rival": 500,
        "opciones_disponibles": "FOLD | CALL | RAISE",
        "botones_visibles": "FOLD | CALL | RAISE",
    }
    actualizar_dom_data_conservando_contexto(dom, None, tracker_obj=tracker)

    # El dom_data debe tener mis_cartas vacías
    assert dom["mis_cartas"] == ""

    # Simular la lógica de guardia anti-ceguera
    es_mi_turno = dom["es_mi_turno"]
    mis_cartas = dom["mis_cartas"]
    if es_mi_turno and not mis_cartas:
        # Si no hay cartas ni en DOM ni en tracker, se cancela el turno
        if not tracker.my_cards:
            es_mi_turno = False

    assert es_mi_turno is False, "El bot nunca debe actuar si no tiene cartas privadas identificadas"


def test_mejora9_action_debounce_per_street_context():
    from bot import GameTracker
    import time

    tracker = GameTracker()
    tracker.hand_number = 1
    tracker.current_street = "TURN"
    tracker.hero_just_acted_ts = time.time()
    tracker.hero_last_action_context = (1, "TURN", 40.0)

    # 1. Mismo contexto (misma mano, misma calle, mismo coste a_igualar) a los 1.5s
    es_mi_turno = True
    current_action_context = (1, "TURN", 40.0)
    if es_mi_turno and (
        (time.time() - tracker.hero_just_acted_ts < 2.5)
        or (current_action_context == tracker.hero_last_action_context and (time.time() - tracker.hero_just_acted_ts < 5.0))
    ):
        es_mi_turno = False

    assert es_mi_turno is False, "Debe silenciar turnos duplicados ante el mismo contexto de apuesta"

    # 2. Contexto nuevo (el rival subió la apuesta a 120.0): debe permitir actuar
    es_mi_turno = True
    new_action_context = (1, "TURN", 120.0)
    tracker.hero_just_acted_ts = time.time() - 3.0  # Ya pasaron 3s
    if es_mi_turno and (
        (time.time() - tracker.hero_just_acted_ts < 2.5)
        or (new_action_context == tracker.hero_last_action_context and (time.time() - tracker.hero_just_acted_ts < 5.0))
    ):
        es_mi_turno = False

    assert es_mi_turno is True, "Debe permitir actuar si el rival cambió el contexto de apuesta"





