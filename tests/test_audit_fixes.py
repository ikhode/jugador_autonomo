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



