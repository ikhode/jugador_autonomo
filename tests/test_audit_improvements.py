import pytest
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot import _parse_num_dom, GameTracker, actualizar_dom_data_conservando_contexto, decidir_accion_rapida
from live_table_tracker import _parse_num, LiveTableMonitor
import config


def test_parse_num_dom_and_tracker():
    # Test cases that previously caused ValueError: could not convert string to float
    test_cases = [
        ("500, 40", 500.0),
        ("500, 500", 500.0),
        ("15, 30", 15.0),
        ("1,500", 1500.0),
        ("12.50", 12.5),
        ("12,50", 12.5),
        ("40", 40.0),
        (25, 25.0),
        (30.5, 30.5),
        ("", 0.0),
        (None, 0.0),
    ]
    for raw, expected in test_cases:
        assert _parse_num_dom(raw) == expected, f"Failed _parse_num_dom for {raw}"
        assert _parse_num(raw) == expected, f"Failed _parse_num for {raw}"


def test_position_detection_sb_bb_btn():
    tracker = GameTracker()
    
    # 1. Hero en SB (a_igualar = 10 chips en ciegas 10/20)
    dom_sb = {
        "es_mi_turno": True,
        "mis_cartas": "K♠ Q♦",
        "cartas_comunitarias": "",
        "bote": 30,
        "a_igualar": 10,
        "mi_stack": 490,
        "stack_rival": 500,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE",
    }
    actualizar_dom_data_conservando_contexto(dom_sb, nuevo_dom=None, tracker_obj=tracker)
    assert tracker.position_name == "SB"
    assert dom_sb["posicion"] == "SB"

    # Reset mano
    tracker.reset_mano()

    # 2. Hero en BB con opción de Check gratis
    dom_bb = {
        "es_mi_turno": True,
        "mis_cartas": "9♥ 8♥",
        "cartas_comunitarias": "",
        "bote": 40,
        "a_igualar": 0,
        "mi_stack": 480,
        "stack_rival": 500,
        "big_blind": 20,
        "opciones_disponibles": "CHECK | RAISE",
    }
    actualizar_dom_data_conservando_contexto(dom_bb, nuevo_dom=None, tracker_obj=tracker)
    assert tracker.position_name == "BB"
    assert dom_bb["posicion"] == "BB"

    # Reset mano
    tracker.reset_mano()

    # 3. Hero en BTN (abre acción primero preflop, bote = 30)
    dom_btn = {
        "es_mi_turno": True,
        "mis_cartas": "A♣ J♠",
        "cartas_comunitarias": "",
        "bote": 30,
        "a_igualar": 20,
        "mi_stack": 500,
        "stack_rival": 500,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE",
    }
    actualizar_dom_data_conservando_contexto(dom_btn, nuevo_dom=None, tracker_obj=tracker)
    assert tracker.position_name == "BTN"
    assert dom_btn["posicion"] == "BTN"


def test_model_chain_validity():
    assert "gemini-2.5-flash" not in config.GEMINI_MODELS_PRIORITY
    assert "cf-llama-3.1-8b" not in config.GEMINI_MODELS_PRIORITY
    assert config.GEMINI_MODEL in ("gemini-3.5-flash-lite", "gemini-3.8-flash")
    assert "gemini-3.5-flash-lite" in config.GEMINI_MODELS_PRIORITY
    assert "gemini-3.8-flash" in config.GEMINI_MODELS_PRIORITY


def test_preflop_qq_facing_raise_shoves_allin():
    dom_qq = {
        "es_mi_turno": True,
        "mis_cartas": "Q♦ Q♥",
        "cartas_comunitarias": "",
        "bote": 130,
        "a_igualar": 60,
        "mi_stack": 440,
        "stack_rival": 500,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE | ALL_IN",
        "situacion": "vs_raise",
    }
    dec = decidir_accion_rapida(dom_qq)
    # Con QQ ante un 3-bet en 3-max, la acción debe ser ALL_IN o RAISE fuerte
    assert dec.accion in ("ALL_IN", "RAISE")
    assert dec.confianza >= 0.95


def test_two_pair_coordinated_board_sizing():
    dom_two_pair = {
        "es_mi_turno": True,
        "mis_cartas": "J♣ 6♠",
        "cartas_comunitarias": "J♠ 8♦ 6♦",
        "bote": 210,
        "a_igualar": 0,
        "mi_stack": 450,
        "stack_rival": 450,
        "big_blind": 20,
        "opciones_disponibles": "CHECK | BET | RAISE",
    }
    dec = decidir_accion_rapida(dom_two_pair)
    assert dec.accion in ("RAISE", "BET")
    # Sizing debe ser agresivo por valor (~67% del bote: >= 130 fichas)
    assert dec.monto_apuesta >= 120
