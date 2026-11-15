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
