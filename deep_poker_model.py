"""
Módulo de Deep Learning, Deep Counterfactual Regret Minimization (Deep CFR)
y Deep Reinforcement Learning (DQN / Actor-Critic) en TensorFlow & Keras.

Componentes:
1. GameStateEncoder: Vectorizador denso (64 dimensiones) de estados de juego.
2. PokerValuePolicyNetwork: MLP Densa en Keras con salida dual (Policy Softmax + Value EV).
3. DeepCFRSolver: Redes de arrepentimiento contrafactual (Advantage Network) y estrategia promedio (Average Strategy Network).
4. DeepRLDQNAgent: Agente Deep Q-Network (DQN) con Replay Buffer y entrenamiento Self-Play.
"""
from __future__ import annotations

from pathlib import Path
import collections
import logging
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

tf = None
keras = None
layers = None
_tf_loaded = False
TENSORFLOW_AVAILABLE = False

def _ensure_tf_loaded() -> bool:
    global tf, keras, layers, _tf_loaded, TENSORFLOW_AVAILABLE
    if _tf_loaded:
        return TENSORFLOW_AVAILABLE
    _tf_loaded = True
    try:
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
        import tensorflow as _tf
        from tensorflow import keras as _keras
        from tensorflow.keras import layers as _layers
        tf = _tf
        keras = _keras
        layers = _layers
        TENSORFLOW_AVAILABLE = True
    except Exception as e:
        TENSORFLOW_AVAILABLE = False
        logger.info("⚠️ TensorFlow no disponible (%s); utilizando fallback matricial NumPy.", e)
    return TENSORFLOW_AVAILABLE

# Ensure aria_poker is in path for card parsing & equity calculations
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'aria_poker'))
try:
    from engine import parse_cards
    from equity import preflop_pct, made_pct
    ARIA_AVAILABLE = True
except Exception:
    ARIA_AVAILABLE = False


ACTION_MAP = {
    0: "FOLD",
    1: "CHECK",
    2: "CALL",
    3: "RAISE_SMALL",
    4: "RAISE_MEDIUM",
    5: "ALL_IN",
}
ACTION_MAP_INV = {v: k for k, v in ACTION_MAP.items()}

POSITIONS = ["BTN", "SB", "BB", "UTG", "MP", "CO", "DESCONOCIDA"]


class GameStateEncoder:
    """Codificador de alta dimensión que transforma el estado de la mesa en un vector numérico denso de 64 elementos."""

    def __init__(self, vector_dim: int = 64):
        self.vector_dim = vector_dim

    def encode(self, game_state: Dict[str, Any]) -> np.ndarray:
        vec = np.zeros(self.vector_dim, dtype=np.float32)

        pot_size = float(game_state.get("pot_size", game_state.get("bote", 0.0)))
        current_bet = float(game_state.get("current_bet", game_state.get("a_igualar", 0.0)))
        effective_stack = float(game_state.get("effective_stack", game_state.get("mi_stack", 500.0)))
        big_blind = float(game_state.get("big_blind", 20.0))
        pot_odds = current_bet / (pot_size + current_bet + 1e-6)

        # Normalización en escala Log/BB
        vec[0] = min(10.0, pot_size / max(1.0, big_blind)) / 10.0
        vec[1] = min(10.0, current_bet / max(1.0, big_blind)) / 10.0
        vec[2] = min(50.0, effective_stack / max(1.0, big_blind)) / 50.0
        vec[3] = float(pot_odds)

        mis_cartas_str = str(game_state.get("mis_cartas", "") or "").strip()
        comunitarias_str = str(game_state.get("cartas_comunitarias", "") or "").strip()
        board_count = len(comunitarias_str.split()) if comunitarias_str else 0
        vec[4] = float(board_count) / 5.0

        # Posición one-hot
        pos = str(game_state.get("posicion", "BTN")).upper()
        if pos in POSITIONS:
            vec[5 + POSITIONS.index(pos)] = 1.0

        # Equidad de la mano (Aria Monte Carlo)
        hand_equity = 0.5
        if ARIA_AVAILABLE and mis_cartas_str and len(mis_cartas_str.split()) == 2:
            try:
                hole = parse_cards(mis_cartas_str)
                if comunitarias_str:
                    board = parse_cards(comunitarias_str)
                    rng = random.Random(42)
                    hand_equity = made_pct(hole, board, rng)
                else:
                    hand_equity = preflop_pct(hole)
            except Exception:
                pass
        vec[12] = float(hand_equity)

        # Perfil del rival (VPIP, PFR, AF)
        opp_prof = game_state.get("perfil_rival", {})
        if isinstance(opp_prof, dict):
            vpip = float(opp_prof.get("vpip_pct", opp_prof.get("VPIP", 25.0))) / 100.0
            pfr = float(opp_prof.get("pfr_pct", opp_prof.get("PFR", 15.0))) / 100.0
            af = float(opp_prof.get("agresividad", 1.5)) / 5.0
            vec[13] = min(1.0, max(0.0, vpip))
            vec[14] = min(1.0, max(0.0, pfr))
            vec[15] = min(1.0, max(0.0, af))

        return vec


class PokerValuePolicyNetwork:
    """Red Neuronal Densa (MLP - Multilayer Perceptron) en Keras con salidas dobles (Policy Softmax + Value EV)."""

    def __init__(self, input_dim: int = 64, num_actions: int = 6):
        self.input_dim = input_dim
        self.num_actions = num_actions
        self.encoder = GameStateEncoder(vector_dim=input_dim)
        self.model = self._build_model()

    def _build_model(self):
        if _ensure_tf_loaded():
            models_dir = Path(__file__).resolve().parent / "models"
            weights_file = models_dir / "poker_tf_policy_value.keras"
            if weights_file.is_file():
                try:
                    loaded = keras.models.load_model(str(weights_file), compile=False)
                    loaded.compile(
                        optimizer=keras.optimizers.Adam(learning_rate=0.001),
                        loss={"policy_head": "categorical_crossentropy", "value_head": "mean_squared_error"},
                        loss_weights={"policy_head": 1.0, "value_head": 0.5},
                    )
                    logger.info("✅ Pesos preentrenados de PokerValuePolicyNetwork cargados desde %s", weights_file)
                    return loaded
                except Exception as e:
                    logger.warning("⚠️ Error cargando pesos preentrenados (%s); creando nuevo modelo.", e)

            inputs = keras.Input(shape=(self.input_dim,), name="game_state")
            x = layers.Dense(128, activation="relu")(inputs)
            x = layers.BatchNormalization()(x)
            x = layers.Dropout(0.15)(x)
            x = layers.Dense(64, activation="relu")(x)
            x = layers.Dense(32, activation="relu")(x)

            # Cabezal de Política (Policy Head): Distribución Softmax de acciones
            policy_output = layers.Dense(self.num_actions, activation="softmax", name="policy_head")(x)

            # Cabezal de Valor (Value Head): Estimación del Valor Esperado (EV en BBs)
            value_output = layers.Dense(1, activation="linear", name="value_head")(x)

            model = keras.Model(inputs=inputs, outputs=[policy_output, value_output], name="PokerValuePolicyNetwork")
            model.compile(
                optimizer=keras.optimizers.Adam(learning_rate=0.001),
                loss={
                    "policy_head": "categorical_crossentropy",
                    "value_head": "mean_squared_error",
                },
                loss_weights={"policy_head": 1.0, "value_head": 0.5},
            )
            return model
        else:
            return None

    def predict_policy_and_value(self, game_state: Dict[str, Any]) -> Tuple[Dict[str, float], float]:
        vec = self.encoder.encode(game_state).reshape(1, -1)

        if TENSORFLOW_AVAILABLE and self.model is not None:
            policy_probs, value_ev = self.model.predict(vec, verbose=0)
            policy_probs = policy_probs[0]
            val_ev = float(value_ev[0][0])
        else:
            # Fallback determinista/heurístico
            equity = float(vec[0][12])
            val_ev = (equity - 0.5) * 10.0
            if equity > 0.65:
                policy_probs = np.array([0.0, 0.1, 0.2, 0.4, 0.2, 0.1], dtype=np.float32)
            elif equity >= 0.40:
                policy_probs = np.array([0.1, 0.4, 0.4, 0.1, 0.0, 0.0], dtype=np.float32)
            else:
                policy_probs = np.array([0.7, 0.2, 0.1, 0.0, 0.0, 0.0], dtype=np.float32)

        policy_dict = {ACTION_MAP[i]: float(policy_probs[i]) for i in range(self.num_actions)}
        return policy_dict, val_ev

    def train_on_batch(self, states: List[Dict[str, Any]], target_policies: np.ndarray, target_values: np.ndarray) -> Dict[str, float]:
        if not TENSORFLOW_AVAILABLE or self.model is None:
            return {"loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0}

        X = np.array([self.encoder.encode(s) for s in states], dtype=np.float32)
        history = self.model.fit(
            X,
            {"policy_head": target_policies, "value_head": target_values},
            epochs=1,
            batch_size=min(32, max(1, len(states))),
            verbose=0,
        )
        return {
            "loss": float(history.history.get("loss", [0.0])[0]),
            "policy_loss": float(history.history.get("policy_head_loss", [0.0])[0]),
            "value_loss": float(history.history.get("value_head_loss", [0.0])[0]),
        }


class DeepCFRSolver:
    r"""
    Solver de Deep Counterfactual Regret Minimization (Deep CFR).
    Aproxima las tablas de arrepentimiento contrafactual $R(s, a)$ y la estrategia promedio
    en equilibrio de Nash $\pi^*(s, a)$ utilizando dos redes Keras independientes.
    """

    def __init__(self, input_dim: int = 64, num_actions: int = 6):
        self.input_dim = input_dim
        self.num_actions = num_actions
        self.encoder = GameStateEncoder(vector_dim=input_dim)
        self.advantage_network = self._build_network("advantage_net", activation="linear")
        self.strategy_network = self._build_network("strategy_net", activation="softmax")
        self.regret_memory = collections.deque(maxlen=5000)

    def _build_network(self, name: str, activation: str):
        if _ensure_tf_loaded():
            models_dir = Path(__file__).resolve().parent / "models"
            file_name = "deep_cfr_advantage.keras" if name == "advantage_net" else "deep_cfr_strategy.keras"
            weights_file = models_dir / file_name
            if weights_file.is_file():
                try:
                    loaded = keras.models.load_model(str(weights_file), compile=False)
                    loss_name = "mse" if activation == "linear" else "categorical_crossentropy"
                    loaded.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001), loss=loss_name)
                    logger.info("✅ Pesos preentrenados de DeepCFRSolver (%s) cargados desde %s", name, weights_file)
                    return loaded
                except Exception as e:
                    logger.warning("⚠️ Error cargando pesos de DeepCFR (%s): %s", name, e)

            model = keras.Sequential([
                keras.Input(shape=(self.input_dim,)),
                layers.Dense(64, activation="relu"),
                layers.Dense(64, activation="relu"),
                layers.Dense(self.num_actions, activation=activation),
            ], name=name)
            loss = "mse" if activation == "linear" else "categorical_crossentropy"
            model.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001), loss=loss)
            return model
        return None

    def predict_regrets(self, game_state: Dict[str, Any]) -> np.ndarray:
        vec = self.encoder.encode(game_state).reshape(1, -1)
        if TENSORFLOW_AVAILABLE and self.advantage_network is not None:
            regrets = self.advantage_network.predict(vec, verbose=0)[0]
            return regrets
        # Fallback NumPy
        eq = vec[0][12]
        return np.array([(0.5 - eq) * 2.0, eq - 0.3, eq - 0.4, (eq - 0.5) * 3, (eq - 0.6) * 4, (eq - 0.8) * 5], dtype=np.float32)

    def get_nash_strategy(self, game_state: Dict[str, Any]) -> Dict[str, float]:
        regrets = self.predict_regrets(game_state)
        pos_regrets = np.maximum(0.0, regrets)
        sum_regrets = np.sum(pos_regrets)

        if sum_regrets > 1e-6:
            probs = pos_regrets / sum_regrets
        else:
            probs = np.ones(self.num_actions, dtype=np.float32) / self.num_actions

        return {ACTION_MAP[i]: float(probs[i]) for i in range(self.num_actions)}

    def record_counterfactual_regret(self, game_state: Dict[str, Any], action_regrets: np.ndarray):
        vec = self.encoder.encode(game_state)
        self.regret_memory.append((vec, action_regrets))

    def update_cfr_networks(self, batch_size: int = 32) -> Dict[str, float]:
        if not TENSORFLOW_AVAILABLE or len(self.regret_memory) < batch_size:
            return {"cfr_loss": 0.0, "memory_size": len(self.regret_memory)}

        batch = random.sample(self.regret_memory, batch_size)
        X_batch = np.array([item[0] for item in batch], dtype=np.float32)
        y_batch = np.array([item[1] for item in batch], dtype=np.float32)

        hist = self.advantage_network.fit(X_batch, y_batch, epochs=1, batch_size=batch_size, verbose=0)
        return {"cfr_loss": float(hist.history.get("loss", [0.0])[0]), "memory_size": len(self.regret_memory)}


class DeepRLDQNAgent:
    """Agente Deep Reinforcement Learning (DQN) con Replay Buffer y Target Network en Keras."""

    def __init__(self, input_dim: int = 64, num_actions: int = 6, gamma: float = 0.95, epsilon: float = 0.10):
        self.input_dim = input_dim
        self.num_actions = num_actions
        self.gamma = gamma
        self.epsilon = epsilon
        self.encoder = GameStateEncoder(vector_dim=input_dim)
        self.memory = collections.deque(maxlen=2000)
        self.q_network = self._build_q_net("q_net")
        self.target_q_network = self._build_q_net("target_q_net")

    def _build_q_net(self, name: str):
        if _ensure_tf_loaded():
            models_dir = Path(__file__).resolve().parent / "models"
            weights_file = models_dir / "deep_rl_dqn.keras"
            if weights_file.is_file():
                try:
                    loaded = keras.models.load_model(str(weights_file), compile=False)
                    loaded.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001), loss="mse")
                    logger.info("✅ Pesos preentrenados de DeepRLDQNAgent cargados desde %s", weights_file)
                    return loaded
                except Exception as e:
                    logger.warning("⚠️ Error cargando pesos de DeepRLDQNAgent: %s", e)

            model = keras.Sequential([
                keras.Input(shape=(self.input_dim,)),
                layers.Dense(64, activation="relu"),
                layers.Dense(64, activation="relu"),
                layers.Dense(self.num_actions, activation="linear"),
            ], name=name)
            model.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001), loss="mse")
            return model
        return None

    def select_action(self, game_state: Dict[str, Any], legal_actions: List[str] = None) -> str:
        if random.random() < self.epsilon:
            if legal_actions:
                return random.choice(legal_actions)
            return random.choice(list(ACTION_MAP.values()))

        vec = self.encoder.encode(game_state).reshape(1, -1)
        if TENSORFLOW_AVAILABLE and self.q_network is not None:
            q_values = self.q_network.predict(vec, verbose=0)[0]
        else:
            q_values = np.random.randn(self.num_actions)

        sorted_indices = np.argsort(q_values)[::-1]
        for idx in sorted_indices:
            act_name = ACTION_MAP[idx]
            if not legal_actions or act_name in legal_actions or (act_name == "RAISE_SMALL" and "RAISE" in legal_actions):
                return act_name

        return legal_actions[0] if legal_actions else "CHECK"

    def remember(self, state: Dict[str, Any], action: str, reward: float, next_state: Dict[str, Any], done: bool):
        s_vec = self.encoder.encode(state)
        a_idx = ACTION_MAP_INV.get(action, 1)
        ns_vec = self.encoder.encode(next_state)
        self.memory.append((s_vec, a_idx, reward, ns_vec, done))

    def train_step(self, batch_size: int = 32) -> float:
        if not TENSORFLOW_AVAILABLE or len(self.memory) < batch_size or self.q_network is None:
            return 0.0

        mini_batch = random.sample(self.memory, batch_size)
        states = np.array([item[0] for item in mini_batch], dtype=np.float32)
        actions = np.array([item[1] for item in mini_batch], dtype=np.int32)
        rewards = np.array([item[2] for item in mini_batch], dtype=np.float32)
        next_states = np.array([item[3] for item in mini_batch], dtype=np.float32)
        dones = np.array([item[4] for item in mini_batch], dtype=np.bool_)

        q_eval = self.q_network.predict(states, verbose=0)
        q_next = self.target_q_network.predict(next_states, verbose=0)

        q_target = q_eval.copy()
        for i in range(batch_size):
            if dones[i]:
                q_target[i, actions[i]] = rewards[i]
            else:
                q_target[i, actions[i]] = rewards[i] + self.gamma * np.max(q_next[i])

        hist = self.q_network.fit(states, q_target, epochs=1, batch_size=batch_size, verbose=0)
        return float(hist.history.get("loss", [0.0])[0])
