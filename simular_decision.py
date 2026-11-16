"""
Script de Simulación Completa en Tiempo Real (Sin Navegador/Juego)
Prueba la interacción de IA (Gemini & Cloudflare Workers AI), la recuperación de memoria en tiempo real desde SQLite,
la validación de decisiones y la evaluación estratégica GTO.
"""
import time
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import config
from poker_agent import PokerAgent
from ai_layer import AILayer, PokerDecision, render_prompt
from poker_gto_engine import (
    clasificar_mano_preflop,
    calcular_pot_odds_y_sizing,
    analizar_textura_mesa_y_blockers,
    generar_frecuencia_mixta_gto,
    evaluar_combinacion_mano_y_mesa,
    decidir_por_ev,
)
from analytics_db import AnalyticsStorage, DB_PATH
from poker_experience_memory import PokerExperienceMemory
from analytics_engine import evaluar_decision_estrategica


def preparar_dom_simulado(
    mis_cartas: str,
    cartas_comunitarias: str = "",
    bote: float = 60.0,
    a_igualar: float = 0.0,
    mi_stack: float = 500.0,
    posicion: str = "BTN",
    opciones: str = "CHECK, BET, RAISE",
    perfil_rival: str = "MANIAC",
) -> dict:
    """Construye una estructura de datos de mesa (dom_data) idéntica a la extraída del navegador."""
    bb = float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))
    gto_math = calcular_pot_odds_y_sizing(bote, a_igualar, mi_stack, big_blind=bb)

    dom_data = {
        "es_mi_turno": True,
        "mis_cartas": mis_cartas,
        "cartas_comunitarias": cartas_comunitarias,
        "bote": bote,
        "a_igualar": a_igualar,
        "mi_stack": mi_stack,
        "stack_rival": mi_stack,
        "big_blind": bb,
        "posicion": posicion,
        "botones_visibles": opciones,
        "opciones_disponibles": opciones,
        "perfil_rival": {
            "nickname": "Rival_Simulado",
            "estilo": perfil_rival,
            "manos_jugadas": 45,
            "vpip_pct": 52,
            "pfr_pct": 34,
            "agresividad": 2.5,
            "recomendacion_tactica": "Atacar con valor, foldear manos marginales ante resubidas",
        },
        "perfiles_oponentes": [
            {
                "asiento": 2,
                "nickname": "Rival_Simulado",
                "estado": "ACTIVO",
                "stack_bb": round(mi_stack / bb, 1),
                "estilo": perfil_rival,
                "manos": 45,
                "vpip_pct": 52,
                "pfr_pct": 34,
                "agresividad": 2.5,
                "recomendacion_tactica": "Atacar con valor",
            }
        ],
        "historial_mano": [
            f"Preflop: Hero ({posicion}) recibe {mis_cartas}.",
            f"Flop ({cartas_comunitarias}): Bote es {bote}.",
        ] if cartas_comunitarias else [f"Preflop: Hero ({posicion}) recibe {mis_cartas}."],
    }

    dom_data["gto_math"] = gto_math
    dom_data["gto_preflop"] = clasificar_mano_preflop(mis_cartas, posicion=posicion, stack_efectivo_bb=mi_stack/bb)
    dom_data["gto_textura"] = analizar_textura_mesa_y_blockers(mis_cartas, cartas_comunitarias)
    dom_data["gto_frecuencia"] = generar_frecuencia_mixta_gto()
    dom_data["gto_combinacion"] = evaluar_combinacion_mano_y_mesa(mis_cartas, cartas_comunitarias)

    # Retrieval en tiempo real desde SQLite (analytics_decisions + poker_experience_memory)
    calle_act = "PREFLOP" if not cartas_comunitarias else ("FLOP" if len(cartas_comunitarias.split()) == 3 else "TURN")
    try:
        storage = AnalyticsStorage(db_path=DB_PATH)
        dom_data["patrones_decisiones_live"] = storage.consultar_patrones_decisiones_live(
            street=calle_act,
            position=posicion,
            limit=3,
        )
    except Exception as e:
        dom_data["patrones_decisiones_live"] = {}

    try:
        exp_mem = PokerExperienceMemory()
        dom_data["memoria_errores"] = exp_mem.buscar_errores_similares(
            hand_tier=dom_data["gto_preflop"].get("tier", "DESCONOCIDO"),
            board_texture=dom_data["gto_textura"].get("textura_mesa", "DESCONOCIDA"),
            opp_archetype=perfil_rival,
            limit=2,
            street=calle_act,
            position=posicion,
        )
        exp_mem.close()
    except Exception:
        dom_data["memoria_errores"] = []

    return dom_data


def ejecutar_simulacion_modelo(nombre_escenario: str, dom_data: dict, modelo_objetivo: str) -> None:
    """Ejecuta una prueba de inferencia real contra el modelo especificado."""
    print("\n" + "=" * 80)
    print(f"🎮 ESCENARIO: {nombre_escenario}")
    print(f"📌 Modelo a Probar: {modelo_objetivo}")
    print(f"🃏 Mis Cartas: {dom_data['mis_cartas']} | Mesa: {dom_data['cartas_comunitarias'] or 'Ninguna'} | Bote: {dom_data['bote']} BB")
    print(f"📍 Posición: {dom_data['posicion']} | Opciones Legales: {dom_data['botones_visibles']}")
    print("-" * 80)

    # 1. Configurar temporalmente el modelo activo y la lista de prioridad
    config.ACTIVE_MODEL = modelo_objetivo
    config.GEMINI_MODELS_PRIORITY = list(dict.fromkeys([modelo_objetivo, "cf-llama-3.1-8b", "cf-deepseek-r1", "gemini-3.1-flash-lite"]))
    agent = PokerAgent(ai_layer=AILayer())

    # 2. Renderizar el prompt generado para verificar el retrieval de la BD
    _, p_hash, prompt_text = render_prompt(dom_data, "v1", "")
    print("📝 [PROMPT GENERADO (Extracto)]: ")
    lineas_prompt = [line.strip() for line in prompt_text.splitlines() if line.strip()]
    for line in lineas_prompt[:12]:
        print(f"   | {line}")
    if len(lineas_prompt) > 12:
        print("   | ... [Retrieval DB e historial inyectados]")

    # 3. Inferencia Real en Tiempo Real
    t0 = time.perf_counter()
    decision: PokerDecision = agent.analizar_pantalla(
        dom_data=dom_data,
        decision_uid=f"sim_{int(time.time())}",
        hand_uid="sim_hand_001",
    )
    t_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    trace = getattr(agent, "last_trace", None)
    modelo_usado = getattr(trace, "model_used", modelo_objetivo) or modelo_objetivo

    print("\n⚡ [RESPUESTA DE IA RECIBIDA]:")
    print(f"   • Modelo Utilizado Real : {modelo_usado}")
    print(f"   • Tiempo de Respuesta   : {t_ms} ms")
    print(f"   • Acción Propuesta      : {decision.accion}")
    print(f"   • Monto Apuesta         : {decision.monto_apuesta}")
    print(f"   • Confianza             : {decision.confianza}")
    print(f"   • Evaluación Táctica    : {decision.evaluacion}")
    print(f"   • Estrategia            : {decision.estrategia_competitiva}")

    # 4. Evaluación GTO y Cálculo de EV contrafactual
    calle = "PREFLOP" if not dom_data["cartas_comunitarias"] else "FLOP"
    legal_list = [a.strip() for a in dom_data["botones_visibles"].split(",")]
    gto_eval = evaluar_decision_estrategica(
        hole_cards=dom_data["mis_cartas"],
        board=dom_data["cartas_comunitarias"],
        pot_size=dom_data["bote"],
        to_call=dom_data["a_igualar"],
        effective_stack=dom_data["mi_stack"],
        action_taken=decision.accion,
        bet_amount=decision.monto_apuesta,
        position=dom_data["posicion"],
        street=calle,
        opponent_profile=dom_data["perfil_rival"]["estilo"],
        legal_actions=legal_list,
        big_blind=dom_data["big_blind"],
    )

    print("\n📊 [EVALUACIÓN GTO Y REGRET CONTRAFACTUAL]:")
    print(f"   • Acción GTO Óptima      : {gto_eval.get('gto_recommended_action')}")
    print(f"   • EV Opción Elegida      : {gto_eval.get('ev_taken_chips'):+.2f} fichas")
    print(f"   • EV Mejor Opción Posible: {gto_eval.get('ev_best_chips'):+.2f} fichas")
    print(f"   • Regret (ΔEV Perdido)   : {gto_eval.get('ev_lost_bb'):+.2f} BB")
    print(f"   • Matriz EV Contrafactual: {json.dumps(gto_eval.get('ev_by_action'), ensure_ascii=False)}")
    print(f"   • Calidad de Decisión    : {gto_eval.get('decision_quality')}")
    print("=" * 80 + "\n")


def probar_modelos_ml_y_deep_learning(dom_data: dict) -> None:
    """Demuestra la inferencia real de Scikit-Learn y TensorFlow / Keras (Policy/Value Net, Deep CFR, DQN)."""
    print("\n" + "🧠 " * 20)
    print("🔬 PRUEBA DE MODELOS MACHINE LEARNING & DEEP LEARNING (TF / KERAS / SKLEARN)")
    print("🧠 " * 20)

    from opponent_model import OpponentModel
    from deep_poker_model import PokerValuePolicyNetwork, DeepCFRSolver, DeepRLDQNAgent
    from bot import decidir_accion_local

    game_state = {
        "pot_size": dom_data["bote"],
        "current_bet": dom_data["a_igualar"],
        "effective_stack": dom_data["mi_stack"],
        "mis_cartas": dom_data["mis_cartas"],
        "cartas_comunitarias": dom_data["cartas_comunitarias"],
        "posicion": dom_data["posicion"],
        "perfil_rival": dom_data["perfil_rival"],
    }

    # 1. Probar Scikit-Learn & XGBoost con Detección de Farol
    for m_type in ("random_forest", "gradient_boosting", "logistic_regression", "svm", "xgboost"):
        try:
            opp_model = OpponentModel(model_type=m_type)
            opp_model.train_from_database()
            act_pred = opp_model.predict_opponent_action(game_state, {}, dom_data["perfil_rival"])
            bluff_p = opp_model.predict_bluff_probability(game_state, {}, dom_data["perfil_rival"])
            print(f"   • [Scikit-Learn: {m_type}] Predicción Rival: {act_pred['predicted_action']} | Prob Farol: {bluff_p:.2%}")
        except Exception as e:
            print(f"   ⚠️ [Scikit-Learn: {m_type}] Warning: {e}")

    # 2. Probar TensorFlow & Keras MLP Policy/Value Network
    try:
        policy_net = PokerValuePolicyNetwork()
        pol_probs, val_ev = policy_net.predict_policy_and_value(game_state)
        print(f"   • [TensorFlow Keras MLP] EV Predicho: {val_ev:+.2f} BB | Política: {pol_probs}")
    except Exception as e:
        print(f"   ⚠️ [TensorFlow Keras MLP] Warning: {e}")

    # 3. Probar Deep CFR (Counterfactual Regret Minimization)
    try:
        cfr_solver = DeepCFRSolver()
        nash_strat = cfr_solver.get_nash_strategy(game_state)
        print(f"   • [Deep CFR Solver] Estrategia Nash Aproximada: {nash_strat}")
    except Exception as e:
        print(f"   ⚠️ [Deep CFR Solver] Warning: {e}")

    # 4. Probar Deep RL DQN Agent
    try:
        dqn_agent = DeepRLDQNAgent()
        action_dqn = dqn_agent.select_action(game_state, legal_actions=["FOLD", "CALL", "RAISE"])
        print(f"   • [Deep RL DQN Agent] Acción Seleccionada: {action_dqn}")
    except Exception as e:
        print(f"   ⚠️ [Deep RL DQN Agent] Warning: {e}")

    # 5. Probar Decisor Local Híbrido en Modo TensorFlow
    config.ACTIVE_MODEL = "tensorflow-mlp"
    config.MODO = "tensorflow"
    local_dec = decidir_accion_local(dom_data)
    print(f"   • [Decisor Híbrido Local TF] Acción Final: {local_dec.accion} | Evaluación: {local_dec.evaluacion}")
    print("=" * 80 + "\n")


def main():
    print("\n🚀 INICIANDO SIMULACIÓN DE INFERENCIA DE IA & RETRIEVAL EN TIEMPO REAL\n")

    # Escenario A: Preflop con mano premium (A♠ K♦)
    dom_preflop = preparar_dom_simulado(
        mis_cartas="A♠ K♦",
        cartas_comunitarias="",
        bote=30.0,
        a_igualar=0.0,
        mi_stack=500.0,
        posicion="BTN",
        opciones="FOLD, CHECK, CALL, RAISE",
        perfil_rival="Maniac",
    )

    # Escenario B: Flop con Top Pair (A♠ K♦ en tablero A♣ 8♥ 3♦)
    dom_flop = preparar_dom_simulado(
        mis_cartas="A♠ K♦",
        cartas_comunitarias="A♣ 8♥ 3♦",
        bote=120.0,
        a_igualar=40.0,
        mi_stack=460.0,
        posicion="BTN",
        opciones="FOLD, CALL (40), RAISE",
        perfil_rival="Calling Station",
    )

    # 1. Probar Modelos de Machine Learning y Deep Learning
    probar_modelos_ml_y_deep_learning(dom_flop)

    # 2. Probar Gemini 3.1 Flash Lite
    ejecutar_simulacion_modelo("1. Preflop Premium vs Maniac", dom_preflop, "gemini-3.1-flash-lite")

    # 3. Probar Cloudflare Workers AI (Llama 3.1 8B)
    ejecutar_simulacion_modelo("2. Flop Top Pair vs Calling Station (Cloudflare Workers AI)", dom_flop, "cf-llama-3.1-8b")

    # 3. Probar Cloudflare Workers AI (DeepSeek R1)
    ejecutar_simulacion_modelo("3. Preflop Premium vs Maniac (Cloudflare DeepSeek R1)", dom_preflop, "cf-deepseek-r1")


if __name__ == "__main__":
    main()
