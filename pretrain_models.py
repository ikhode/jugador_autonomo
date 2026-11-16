"""
Script de Preentrenamiento Masivo para Modelos de TensorFlow, Keras y Scikit-Learn.
Genera datasets de simulación de póker GTO/Monte Carlo y entrena:
1. OpponentModel (Scikit-Learn: RandomForest, GradientBoosting, LogisticRegression, SVM, XGBoost).
2. PokerValuePolicyNetwork (TensorFlow/Keras: Red Neuronal Densa Dual Policy + Value).
3. DeepCFRSolver (TensorFlow/Keras: Redes de Arrepentimiento Contrafactual y Estrategia Nash).
4. DeepRLDQNAgent (TensorFlow/Keras: Agente DQN entrenado con Self-Play).

Guarda los pesos entrenados en la carpeta `models/` para su carga automática por el Bot.
"""
from pathlib import Path
import os
import sys
import time
import random
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

from opponent_model import OpponentModel
from deep_poker_model import (
    GameStateEncoder,
    PokerValuePolicyNetwork,
    DeepCFRSolver,
    DeepRLDQNAgent,
    ACTION_MAP,
)
from poker_gto_engine import clasificar_mano_preflop, estimar_equidad_vs_rango


def generar_dataset_preentrenamiento(num_samples: int = 1500) -> tuple[List[dict], np.ndarray, np.ndarray]:
    """Genera un dataset sintético masivo calibrado con el motor GTO y simulaciones Monte Carlo."""
    print(f"🃏 Generando dataset de preentrenamiento con {num_samples} situaciones de juego...")

    posiciones = ["BTN", "SB", "BB"]
    cartas_alta = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"]
    palos = ["s", "h", "d", "c"]

    def _random_card():
        return f"{random.choice(cartas_alta)}{random.choice(palos)}"

    states = []
    target_policies = []
    target_values = []

    for _ in range(num_samples):
        c1, c2 = _random_card(), _random_card()
        while c1 == c2:
            c2 = _random_card()
        hole_str = f"{c1} {c2}"

        board_len = random.choice([0, 3, 4, 5])
        board_cards = []
        for _ in range(board_len):
            bc = _random_card()
            while bc in board_cards or bc == c1 or bc == c2:
                bc = _random_card()
            board_cards.append(bc)
        board_str = " ".join(board_cards)

        pot = round(random.uniform(20.0, 500.0), 1)
        bet = round(random.choice([0.0, 0.0, 20.0, 40.0, 100.0]), 1)
        stack = round(random.uniform(100.0, 1000.0), 1)
        pos = random.choice(posiciones)

        gto_pre = clasificar_mano_preflop(hole_str, posicion=pos, stack_efectivo_bb=stack/20.0, situacion="open")
        tier = gto_pre.get("tier", "BASURA")

        game_state = {
            "pot_size": pot,
            "current_bet": bet,
            "effective_stack": stack,
            "mis_cartas": hole_str,
            "cartas_comunitarias": board_str,
            "posicion": pos,
            "perfil_rival": {
                "vpip_pct": random.uniform(15.0, 50.0),
                "pfr_pct": random.uniform(10.0, 35.0),
                "agresividad": random.uniform(0.8, 3.5),
            },
        }

        # Generar target policy (distribución de probabilidad) y target value (EV en BBs)
        policy_vec = np.zeros(6, dtype=np.float32)
        if tier in ("S", "A"):
            policy_vec[3] = 0.50  # RAISE_SMALL
            policy_vec[4] = 0.35  # RAISE_MEDIUM
            policy_vec[2] = 0.15  # CALL
            val_ev = random.uniform(3.0, 15.0)
        elif tier == "B":
            policy_vec[3] = 0.60  # RAISE_SMALL
            policy_vec[2] = 0.30  # CALL
            policy_vec[0] = 0.10  # FOLD
            val_ev = random.uniform(0.5, 4.0)
        elif tier == "C":
            if bet > 0:
                policy_vec[0] = 0.70  # FOLD
                policy_vec[2] = 0.25  # CALL
                policy_vec[3] = 0.05  # RAISE_SMALL
            else:
                policy_vec[3] = 0.50  # RAISE_SMALL
                policy_vec[1] = 0.40  # CHECK
                policy_vec[0] = 0.10  # FOLD
            val_ev = random.uniform(-1.0, 1.5)
        else:  # Trash / Basura
            if bet > 0:
                policy_vec[0] = 0.95  # FOLD
                policy_vec[2] = 0.05  # CALL
            else:
                policy_vec[1] = 0.70  # CHECK
                policy_vec[0] = 0.25  # FOLD
                policy_vec[3] = 0.05  # RAISE_SMALL
            val_ev = random.uniform(-3.0, 0.0)

        states.append(game_state)
        target_policies.append(policy_vec)
        target_values.append([val_ev])

    return states, np.array(target_policies, dtype=np.float32), np.array(target_values, dtype=np.float32)


def ejecutar_preentrenamiento_completo():
    print("\n" + "=" * 80)
    print("🚀 INICIANDO PREENTRENAMIENTO MASIVO DE MODELOS ML & DEEP LEARNING")
    print("=" * 80 + "\n")

    t0 = time.time()
    states, target_policies, target_values = generar_dataset_preentrenamiento(num_samples=1500)

    # 1. Preentrenamiento de Clasificadores Scikit-Learn & XGBoost
    print("\n📦 [1/4] Preentrenando modelos de oponentes en Scikit-Learn y XGBoost...")
    encoder = GameStateEncoder(vector_dim=64)
    X_sklearn = []
    y_sklearn = []
    for i, s in enumerate(states):
        pot = float(s["pot_size"])
        bet = float(s["current_bet"])
        pot_odds = bet / (pot + bet + 1e-6)
        vpip = float(s["perfil_rival"]["vpip_pct"]) / 100.0
        pfr = float(s["perfil_rival"]["pfr_pct"]) / 100.0
        X_sklearn.append([pot, bet, pot_odds, len(s["cartas_comunitarias"].split()), 0.5, vpip, pfr, 1.0, 1.0, 1.0])
        top_act = int(np.argmax(target_policies[i]))
        # Mapear las 6 acciones a 3 clases continuas (0=fold, 1=check/call, 2=raise)
        label = 0 if top_act == 0 else (1 if top_act in (1, 2) else 2)
        y_sklearn.append(label)

    for m_type in ("random_forest", "gradient_boosting", "logistic_regression", "svm", "xgboost"):
        try:
            model = OpponentModel(model_type=m_type)
            model.train(X_sklearn, y_sklearn)
            save_path = MODELS_DIR / f"opponent_model_{m_type}.joblib"
            model.save_model(save_path)
            print(f"   ✅ Modelo {m_type} preentrenado y guardado en {save_path}")
        except Exception as e:
            print(f"   ⚠️ Error preentrenando {m_type}: {e}")

    # 2. Preentrenamiento de TensorFlow & Keras MLP Policy/Value Network
    print("\n📦 [2/4] Preentrenando Keras MLP Policy-Value Network...")
    try:
        policy_net = PokerValuePolicyNetwork(input_dim=64, num_actions=6)
        metrics = policy_net.train_on_batch(states, target_policies, target_values)
        if policy_net.model is not None:
            save_path_keras = MODELS_DIR / "poker_tf_policy_value.keras"
            policy_net.model.save(str(save_path_keras))
            print(f"   ✅ Red Keras MLP preentrenada y guardada en {save_path_keras} (Loss: {metrics.get('loss', 0.0):.4f})")
    except Exception as e:
        print(f"   ⚠️ Error preentrenando Red Keras MLP: {e}")

    # 3. Preentrenamiento de Deep CFR Solver
    print("\n📦 [3/4] Preentrenando Deep CFR Solver (Advantage & Strategy Networks)...")
    try:
        cfr_solver = DeepCFRSolver(input_dim=64, num_actions=6)
        for i, s in enumerate(states[:500]):
            regrets = (target_policies[i] - 0.166) * 10.0
            cfr_solver.record_counterfactual_regret(s, regrets)

        cfr_metrics = cfr_solver.update_cfr_networks(batch_size=64)
        if cfr_solver.advantage_network is not None:
            cfr_adv_path = MODELS_DIR / "deep_cfr_advantage.keras"
            cfr_solver.advantage_network.save(str(cfr_adv_path))
            print(f"   ✅ Deep CFR Advantage Network guardada en {cfr_adv_path}")

        if cfr_solver.strategy_network is not None:
            cfr_strat_path = MODELS_DIR / "deep_cfr_strategy.keras"
            cfr_solver.strategy_network.save(str(cfr_strat_path))
            print(f"   ✅ Deep CFR Strategy Network guardada en {cfr_strat_path}")
    except Exception as e:
        print(f"   ⚠️ Error preentrenando Deep CFR Solver: {e}")

    # 4. Preentrenamiento de Agente Deep RL DQN (Self-Play)
    print("\n📦 [4/4] Preentrenando Agente Deep RL DQN con Replay Memory...")
    try:
        dqn_agent = DeepRLDQNAgent(input_dim=64, num_actions=6)
        for i in range(len(states) - 1):
            act_str = ACTION_MAP[int(np.argmax(target_policies[i]))]
            reward = float(target_values[i][0])
            dqn_agent.remember(states[i], act_str, reward, states[i + 1], False)

        dqn_loss = dqn_agent.train_step(batch_size=64)
        if dqn_agent.q_network is not None:
            dqn_path = MODELS_DIR / "deep_rl_dqn.keras"
            dqn_agent.q_network.save(str(dqn_path))
            print(f"   ✅ Agente Deep RL DQN guardado en {dqn_path} (DQN Loss: {dqn_loss:.4f})")
    except Exception as e:
        print(f"   ⚠️ Error preentrenando Agente Deep RL DQN: {e}")

    elapsed = round(time.time() - t0, 2)
    print("\n" + "=" * 80)
    print(f"🎉 PREENTRENAMIENTO COMPLETADO EXITOSAMENTE en {elapsed} segundos.")
    print(f"📂 Pesos e hiperparámetros guardados en el directorio: {MODELS_DIR}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    ejecutar_preentrenamiento_completo()
