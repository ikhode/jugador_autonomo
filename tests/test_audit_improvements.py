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


def test_wait_recovery_via_fallback_layer():
    from ai_layer import AILayer, ModelPolicy
    from bot import decidir_accion_rapida

    # 1. LLM propuso WAIT pero había CHECK gratis disponible
    layer = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: ('{"evaluacion":"esperar","estrategia_competitiva":"","estado_pantalla":"EN_MESA_JUEGO","es_mi_turno":true,"accion":"WAIT","monto_apuesta":0,"confianza":0.9}', 20, 10),
        fallback_fn=decidir_accion_rapida,
        use_genkit=False,
    )
    dom_check = {
        "es_mi_turno": True,
        "mis_cartas": "7♦ 3♥",
        "cartas_comunitarias": "4♠ 6♦ A♦",
        "bote": 40,
        "a_igualar": 0,
        "opciones_disponibles": "CHECK | BET | RAISE",
        "botones_visibles": "CHECK | BET | RAISE",
    }
    res_check = layer.decidir(dom_data=dom_check)
    assert res_check.decision.accion == "CHECK"

    # 2. LLM propuso WAIT pero enfrentaba apuesta con basura (72o en mesa seca sin draw): fallback debe foldear, nunca pagar
    dom_bet = {
        "es_mi_turno": True,
        "mis_cartas": "7♣ 2♥",
        "cartas_comunitarias": "J♠ 8♦ 4♣",
        "bote": 80,
        "a_igualar": 40,
        "opciones_disponibles": "FOLD | CALL | RAISE",
        "botones_visibles": "FOLD | CALL | RAISE",
        "situacion": "vs_raise",
    }
    res_bet = layer.decidir(dom_data=dom_bet)
    assert res_bet.decision.accion == "FOLD"


def test_validate_legality_spanish_and_bet_raise_synonyms():
    from ai_layer import validate_legality, PokerDecision, build_table_input

    # Botones en español con SUBIR (40)
    dom_es = {
        "es_mi_turno": True,
        "opciones_disponibles": "FOLD | CHECK | SUBIR (40)",
        "a_igualar": 0,
    }
    t_input = build_table_input(dom_es)
    assert "RAISE" in t_input.opciones_legales

    dec_raise = PokerDecision(
        evaluacion="Subir por valor",
        estrategia_competitiva="Agresiva",
        estado_pantalla="EN_MESA_JUEGO",
        es_mi_turno=True,
        accion="RAISE",
        monto_apuesta=40,
        confianza=0.9,
    )
    is_legal, res_dec, note = validate_legality(dec_raise, t_input)
    assert is_legal is True
    assert res_dec.accion == "RAISE"


def test_decidir_accion_rapida_weak_hand_facing_bet_folds():
    # Mano basura preflop (73o) enfrentando apuesta sin opción de check
    dom_weak = {
        "es_mi_turno": True,
        "mis_cartas": "7♣ 3♥",
        "cartas_comunitarias": "",
        "bote": 90,
        "a_igualar": 30,
        "mi_stack": 470,
        "stack_rival": 500,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE",
        "botones_visibles": "FOLD | CALL | RAISE",
        "situacion": "vs_raise",
    }
    dec = decidir_accion_rapida(dom_weak)
    # Debe foldear obligatoriamente para proteger stack, NUNCA pagar
    assert dec.accion == "FOLD"


def test_human_timing_delay_calculation():
    import os
    from bot import aplicar_delay_humanizado

    # Con PYTEST_CURRENT_TEST activo retorna 0.01s inmediatamente
    fast_delay = aplicar_delay_humanizado("FOLD", dom_data={"street": "PREFLOP"}, tiempo_ia_seg=1.0)
    assert fast_delay == 0.01

    # Desactivamos temporalmente el flag para probar la lógica matemática real sin dormir
    orig = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        import unittest.mock as mock
        with mock.patch("time.sleep") as mock_sleep:
            # Caso 1: Preflop Fold con IA rápida (0.5s) -> delay debe compensar hasta ~2.5-4s
            d1 = aplicar_delay_humanizado("FOLD", dom_data={"street": "PREFLOP", "gto_preflop": {"tier": "BASURA"}}, tiempo_ia_seg=0.5)
            assert d1 >= 0.65
            mock_sleep.assert_called()

            # Caso 2: River Bet/Call con IA que tardó 2.0s -> delay humano profundo
            d2 = aplicar_delay_humanizado("CALL", dom_data={"street": "RIVER", "gto_math": {"bote_bb": 10.0, "a_igualar_bb": 5.0}}, tiempo_ia_seg=2.0)
            assert d2 >= 1.5
            total_turn_time = 2.0 + d2
            assert 3.5 <= total_turn_time <= 8.5
    finally:
        if orig is not None:
            os.environ["PYTEST_CURRENT_TEST"] = orig


def test_human_pre_click_pause_and_config():
    import os
    import unittest.mock as mock
    from bot import aplicar_pausa_pre_clic
    import config

    assert config.HUMAN_TIMING_ENABLED is True
    assert config.HUMAN_MIN_DELAY_SEC == 1.5
    assert config.HUMAN_PRE_CLICK_PAUSE_MS == 220
    assert config.HUMAN_CLICK_DURATION_MS == 85

    # Con PYTEST_CURRENT_TEST retorna 0.0 inmediatamente
    assert aplicar_pausa_pre_clic() == 0.0

    # Sin PYTEST_CURRENT_TEST calcula pausa pre-clic neuromuscular entre 80ms y 450ms
    orig = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        with mock.patch("time.sleep") as mock_sleep:
            mock_elem = mock.MagicMock()
            d = aplicar_pausa_pre_clic(mock_elem, "Boton Call")
            assert 0.08 <= d <= 0.45
            mock_elem.hover.assert_called_once()
            mock_sleep.assert_called_once()
    finally:
        if orig is not None:
            os.environ["PYTEST_CURRENT_TEST"] = orig


def test_6max_position_resolution_all_positions():
    tracker = GameTracker()

    # 1. Mesa completa de 6 jugadores: Dealer en asiento 1, Hero en asiento 4 -> UTG
    dom_6p_utg = {
        "es_mi_turno": True,
        "mis_cartas": "A♣ K♦",
        "cartas_comunitarias": "",
        "bote": 30,
        "a_igualar": 20,
        "big_blind": 20,
        "opciones_disponibles": "FOLD | CALL | RAISE",
        "jugadores_mesa": [
            {"seat": 1, "is_dealer": True, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 2, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 3, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 4, "is_dealer": False, "is_hero": True, "estado": "ACTIVO"},
            {"seat": 5, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 6, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
        ],
    }
    actualizar_dom_data_conservando_contexto(dom_6p_utg, nuevo_dom=None, tracker_obj=tracker)
    assert dom_6p_utg["posicion"] == "UTG"
    assert tracker.position_name == "UTG"

    # 2. Hero en asiento 5 -> MP
    tracker.reset_mano()
    dom_6p_mp = {
        "es_mi_turno": True,
        "mis_cartas": "A♣ K♦",
        "big_blind": 20,
        "jugadores_mesa": [
            {"seat": 1, "is_dealer": True, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 2, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 3, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 4, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 5, "is_dealer": False, "is_hero": True, "estado": "ACTIVO"},
            {"seat": 6, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
        ],
    }
    actualizar_dom_data_conservando_contexto(dom_6p_mp, nuevo_dom=None, tracker_obj=tracker)
    assert dom_6p_mp["posicion"] == "MP"

    # 3. Hero en asiento 6 -> CO
    tracker.reset_mano()
    dom_6p_co = {
        "es_mi_turno": True,
        "mis_cartas": "A♣ K♦",
        "big_blind": 20,
        "jugadores_mesa": [
            {"seat": 1, "is_dealer": True, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 2, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 3, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 4, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 5, "is_dealer": False, "is_hero": False, "estado": "ACTIVO"},
            {"seat": 6, "is_dealer": False, "is_hero": True, "estado": "ACTIVO"},
        ],
    }
    actualizar_dom_data_conservando_contexto(dom_6p_co, nuevo_dom=None, tracker_obj=tracker)
    assert dom_6p_co["posicion"] == "CO"


def test_6max_preflop_classification_discipline():
    from poker_gto_engine import clasificar_mano_preflop

    # Mano marginal K9o
    # En UTG debe ser clasificada como BASURA para evitar pérdidas fuera de posición
    res_utg = clasificar_mano_preflop("K♠ 9♦", posicion="UTG", situacion="open")
    assert res_utg["tier"] == "BASURA"
    assert res_utg["score"] <= 50

    # La misma mano en el Botón (BTN) tiene bonus posicional para robo
    res_btn = clasificar_mano_preflop("K♠ 9♦", posicion="BTN", situacion="open")
    assert res_btn["score"] > res_utg["score"]

    # Mano Premium AA en UTG debe tener score máximo
    res_aa_utg = clasificar_mano_preflop("A♠ A♥", posicion="UTG", situacion="open")
    assert res_aa_utg["tier"] == "S"
    assert res_aa_utg["score"] >= 95


def test_texas_holdem_config_and_selectors():
    assert config.POKER_MODE == "Cash Game Texas Hold'em"
    assert config.MAX_TABLE_SEATS == 6
    assert config.MAX_SEATS == 6
    assert "Texas Hold'em" in config.SELECTORS["select_game_card"]
    assert ".game-category-tile.cash-game" in config.SELECTORS["select_game_card"]
    assert "twister" not in config.SELECTORS["select_game_card"].lower()
