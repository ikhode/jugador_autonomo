"""
Módulo de Verificación e Inicialización Automática del Sistema.
Comprueba todos los componentes críticos antes de iniciar Playwright y la automatización web:
1. Evaluador de Cartas y Equidad Aria Poker / GTO Engine (parsear_carta, equity).
2. Codificador Denso de Estado 64-dim (GameStateEncoder).
3. Existencia y Carga de Modelos Preentrenados (Scikit-Learn, XGBoost, Keras MLP, Deep CFR, Deep RL DQN).
4. Auto-ejecución de Preentrenamiento si faltan modelos.
5. Motor Estratégico Híbrido (StrategicEngine & DecisionMaker).
6. Profiler de Oponentes y Base de Datos SQLite (logs/poker_stats.db).
"""
from pathlib import Path
import os
import sys
import time
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "aria_poker"))

MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODELOS_REQUERIDOS = [
    "poker_tf_policy_value.keras",
    "deep_cfr_advantage.keras",
    "deep_cfr_strategy.keras",
    "deep_rl_dqn.keras",
    "opponent_model_random_forest.joblib",
    "opponent_model_gradient_boosting.joblib",
    "opponent_model_xgboost.joblib",
]


def verificar_aria_poker_y_gto() -> dict:
    """Verifica la librería aria_poker y las funciones de parsing y evaluación de poker_gto_engine."""
    res = {"ok": False, "msg": "", "aria_available": False}
    try:
        from poker_gto_engine import (
            parsear_carta,
            formatear_cartas_canonicas,
            clasificar_mano_preflop,
            evaluar_combinacion_mano_y_mesa,
        )

        r, s = parsear_carta("A♠")
        if not r or not s:
            r, s = parsear_carta("AS")

        cards_canon = formatear_cartas_canonicas(["AS", "KH"])
        preflop_info = clasificar_mano_preflop("A♠ K♥", posicion="BTN", situacion="open")

        try:
            from aria_poker.engine import parse_cards, evaluate
            c_cards = parse_cards("As Kh")
            score = evaluate(c_cards)
            res["aria_available"] = True
        except Exception:
            res["aria_available"] = False

        res["ok"] = True
        res["msg"] = f"Parseo de cartas OK ({cards_canon}) | Preflop Tier: {preflop_info.get('tier', 'N/A')} | Aria Engine: {'C++ Speed' if res['aria_available'] else 'Python Fallback'}"
    except Exception as e:
        res["ok"] = False
        res["msg"] = f"Error en evaluador de cartas/GTO: {e}"
    return res


def verificar_game_state_encoder() -> dict:
    """Verifica la codificación del estado de juego a vector numérico de 64 dimensiones."""
    res = {"ok": False, "msg": ""}
    try:
        from deep_poker_model import GameStateEncoder

        encoder = GameStateEncoder(vector_dim=64)
        sample_state = {
            "pot_size": 100.0,
            "current_bet": 20.0,
            "effective_stack": 500.0,
            "mis_cartas": "A♠ K♥",
            "cartas_comunitarias": "Q♠ J♦ 10♣",
            "posicion": "BTN",
            "perfil_rival": {"vpip_pct": 25.0, "pfr_pct": 18.0, "agresividad": 2.0},
        }
        vec = encoder.encode(sample_state)
        if isinstance(vec, np.ndarray) and vec.shape == (64,):
            res["ok"] = True
            res["msg"] = f"Vector denso 64-dim generado correctamente (Min: {vec.min():.2f}, Max: {vec.max():.2f})"
        else:
            res["ok"] = False
            res["msg"] = f"Vector de forma inválida: {getattr(vec, 'shape', type(vec))}"
    except Exception as e:
        res["ok"] = False
        res["msg"] = f"Error verificando GameStateEncoder: {e}"
    return res


def verificar_o_ejecutar_preentrenamiento() -> dict:
    """Verifica si existen los modelos preentrenados y si falta alguno, ejecuta el preentrenamiento automático."""
    res = {"ok": False, "msg": "", "auto_trained": False}
    faltantes = [m for m in MODELOS_REQUERIDOS if not (MODELS_DIR / m).exists()]

    if faltantes:
        print(f"\n⚠️ MODELOS PREENTRENADOS FALTANTES DETECTADOS ({len(faltantes)}/{len(MODELOS_REQUERIDOS)}): {faltantes}")
        print("⚙️ Ejecutando PREENTRENAMIENTO AUTOMÁTICO de modelos ML & Deep Learning...")
        try:
            from pretrain_models import ejecutar_preentrenamiento_completo
            ejecutar_preentrenamiento_completo()
            res["auto_trained"] = True
        except Exception as e:
            res["ok"] = False
            res["msg"] = f"Error ejecutando preentrenamiento automático: {e}"
            return res

    # Comprobación final tras posible preentrenamiento
    aun_faltantes = [m for m in MODELOS_REQUERIDOS if not (MODELS_DIR / m).exists()]
    if not aun_faltantes:
        res["ok"] = True
        res["msg"] = f"Todos los modelos ({len(MODELOS_REQUERIDOS)}) están listos y cargados en {MODELS_DIR}"
        if res["auto_trained"]:
            res["msg"] += " (Preentrenados automáticamente)"
    else:
        res["ok"] = False
        res["msg"] = f"Faltan {len(aun_faltantes)} modelos tras preentrenamiento: {aun_faltantes}"

    return res


def verificar_motor_estrategico_y_decision_maker() -> dict:
    """Verifica la instanciación e inferencia híbrida con StrategicEngine y DecisionMaker."""
    res = {"ok": False, "msg": ""}
    try:
        from strategic_engine import StrategicEngine
        from opponent_model import OpponentModel
        from decision_maker import DecisionMaker

        engine = StrategicEngine()
        opp_model = OpponentModel(model_type="random_forest")

        rf_path = MODELS_DIR / "opponent_model_random_forest.joblib"
        if rf_path.exists():
            opp_model.load_model(rf_path)

        decisor = DecisionMaker(strategic_engine=engine, opponent_model=opp_model)

        sample_state = {
            "pot_size": 80.0,
            "current_bet": 0.0,
            "effective_stack": 400.0,
            "mis_cartas": "A♠ Q♥",
            "cartas_comunitarias": "K♠ J♦ 10♣",
            "posicion": "BTN",
        }
        res_hibrido = decisor.decidir_accion_hibrida(
            game_state=sample_state,
            historical_actions=[],
            opponent_profile={"vpip_pct": 30.0, "pfr_pct": 20.0, "agresividad": 1.8},
            model_mode="gto",
        )

        accion_final = res_hibrido.get("final_action", "CHECK")
        res["ok"] = True
        res["msg"] = f"Motor Híbrido operativo | Decisión de prueba: '{accion_final}'"
    except Exception as e:
        res["ok"] = False
        res["msg"] = f"Error en Motor Estratégico / DecisionMaker: {e}"
    return res


def verificar_opponents_profiler_y_db() -> dict:
    """Verifica el profiler de rivales y la conexión SQLite WAL (logs/poker_stats.db)."""
    res = {"ok": False, "msg": ""}
    try:
        from opponents_profiler import OpponentProfiler
        from analytics_db import DB_PATH

        profiler = OpponentProfiler(db_path=str(DB_PATH))
        perf = profiler.obtener_perfil_explotativo("test_opp_01", "TestOpponent")

        res["ok"] = True
        res["msg"] = f"Base de datos SQLite WAL accesible ({DB_PATH.name}) | Profiler listo (Estilo rival test: {perf.get('estilo', 'N/A')})"
    except Exception as e:
        res["ok"] = False
        res["msg"] = f"Error en Profiler / Base de datos: {e}"
    return res


def verificar_y_preparar_entorno() -> bool:
    """
    Ejecuta el diagnóstico completo del sistema.
    Si todo está listo (o tras inicializar modelos automáticamente), imprime el tablero de control
    y retorna True para permitir el arranque de bot.py.
    """
    print("\n" + "=" * 80)
    print("🚀 SISTEMA AUTÓNOMO DE PÓKER: VERIFICACIÓN E INICIALIZACIÓN PRE-VUELO")
    print("=" * 80)

    # 1. Evaluador de Cartas y GTO Engine
    v_aria = verificar_aria_poker_y_gto()
    st_aria = "✅ OK" if v_aria["ok"] else "❌ ERROR"
    print(f"[{st_aria}] Evaluador de Cartas & GTO Engine: {v_aria['msg']}")

    # 2. Codificador Denso 64-dim GameStateEncoder
    v_encoder = verificar_game_state_encoder()
    st_encoder = "✅ OK" if v_encoder["ok"] else "❌ ERROR"
    print(f"[{st_encoder}] Codificador Denso 64-dim (GameStateEncoder): {v_encoder['msg']}")

    # 3. Modelos Preentrenados (ML & Deep Learning)
    v_models = verificar_o_ejecutar_preentrenamiento()
    st_models = "✅ OK" if v_models["ok"] else "❌ ERROR"
    print(f"[{st_models}] Pesos y Modelos Preentrenados ML / Deep CFR / DQN: {v_models['msg']}")

    # 4. Motor Estratégico Híbrido & DecisionMaker
    v_engine = verificar_motor_estrategico_y_decision_maker()
    st_engine = "✅ OK" if v_engine["ok"] else "❌ ERROR"
    print(f"[{st_engine}] Motor Estratégico & Decision Maker Híbrido: {v_engine['msg']}")

    # 5. Opponent Profiler & Base de Datos SQLite WAL
    v_db = verificar_opponents_profiler_y_db()
    st_db = "✅ OK" if v_db["ok"] else "❌ ERROR"
    print(f"[{st_db}] Profiler de Rival & Base de Datos SQLite WAL: {v_db['msg']}")

    print("=" * 80)

    todo_ok = all([v_aria["ok"], v_encoder["ok"], v_models["ok"], v_engine["ok"], v_db["ok"]])
    if todo_ok:
        print("🎉 ¡TODOS LOS COMPONENTES ESTÁN VERIFICADOS Y LISTOS!")
        print("🤖 Procediendo con el inicio del navegador Playwright y la automatización web...\n")
    else:
        print("⚠️ ADVERTENCIA: Algunos componentes reportaron errores. El bot continuará con fallbacks automáticos.\n")

    return todo_ok


if __name__ == "__main__":
    verificar_y_preparar_entorno()
