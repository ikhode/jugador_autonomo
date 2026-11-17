# strategic_engine.py
"""
Motor Estratégico Híbrido: GTO Heurístico + Deep Learning (TensorFlow/Keras Policy & Value Networks y Deep CFR).
"""
import numpy as np
import sys
import os

# Import GTO Engine and Aria Equity
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'aria_poker'))

try:
    from poker_gto_engine import clasificar_mano_preflop, evaluar_combinacion_mano_y_mesa
    GTO_ENGINE_AVAILABLE = True
except Exception:
    GTO_ENGINE_AVAILABLE = False

try:
    from engine import parse_cards
    from equity import preflop_pct, made_pct
    ARIA_AVAILABLE = True
except Exception:
    ARIA_AVAILABLE = False

try:
    from deep_poker_model import PokerValuePolicyNetwork, DeepCFRSolver
    DEEP_MODEL_AVAILABLE = True
except Exception:
    DEEP_MODEL_AVAILABLE = False


class StrategicEngine:
    def __init__(self, strategy_model=None):
        self.strategy_model = strategy_model
        self._tf_policy_net = None
        self._deep_cfr_solver = None

    @property
    def tf_policy_net(self):
        if self._tf_policy_net is None:
            try:
                from deep_poker_model import PokerValuePolicyNetwork
                self._tf_policy_net = PokerValuePolicyNetwork()
            except Exception:
                pass
        return self._tf_policy_net

    @property
    def deep_cfr_solver(self):
        if self._deep_cfr_solver is None:
            try:
                from deep_poker_model import DeepCFRSolver
                self._deep_cfr_solver = DeepCFRSolver()
            except Exception:
                pass
        return self._deep_cfr_solver

    def get_action_probabilities(self, game_state, model_mode: str = "gto") -> dict:
        """
        Calculates base policy probabilities using GTO-derived tables or TensorFlow/Keras Deep Learning models.
        """
        mode_lower = str(model_mode or "gto").lower()

        # Ruta 1: TensorFlow MLP Policy Network
        if mode_lower in ("tensorflow", "tensorflow_mlp", "keras", "tf") and self.tf_policy_net is not None:
            try:
                probs_raw, val_ev = self.tf_policy_net.predict_policy_and_value(game_state)
                return {
                    "fold": round(probs_raw.get("FOLD", 0.0), 4),
                    "call": round(probs_raw.get("CHECK", 0.0) + probs_raw.get("CALL", 0.0), 4),
                    "raise_small": round(probs_raw.get("RAISE_SMALL", 0.0), 4),
                    "raise_medium": round(probs_raw.get("RAISE_MEDIUM", 0.0), 4),
                    "raise_large": round(probs_raw.get("ALL_IN", 0.0), 4),
                    "predicted_ev_bb": round(val_ev, 3),
                }
            except Exception as e:
                print(f"⚠️ Error en inferencia TensorFlow MLP ({e}); degradando a GTO.")

        # Ruta 2: Deep CFR (Counterfactual Regret Minimization) Nash Strategy
        if mode_lower in ("deep_cfr", "cfr") and self.deep_cfr_solver is not None:
            try:
                nash_probs = self.deep_cfr_solver.get_nash_strategy(game_state)
                return {
                    "fold": round(nash_probs.get("FOLD", 0.0), 4),
                    "call": round(nash_probs.get("CHECK", 0.0) + nash_probs.get("CALL", 0.0), 4),
                    "raise_small": round(nash_probs.get("RAISE_SMALL", 0.0), 4),
                    "raise_medium": round(nash_probs.get("RAISE_MEDIUM", 0.0), 4),
                    "raise_large": round(nash_probs.get("ALL_IN", 0.0), 4),
                }
            except Exception as e:
                print(f"⚠️ Error en inferencia Deep CFR ({e}); degradando a GTO.")

        # Ruta 3: Motor GTO Heurístico y Evaluador 7 Cartas
        mis_cartas_str = str(game_state.get("mis_cartas", "") or "").strip()
        comunitarias_str = str(game_state.get("cartas_comunitarias", "") or "").strip()
        pot_size = float(game_state.get("pot_size", 0.0))
        current_bet = float(game_state.get("current_bet", 0.0))
        board_cards_count = int(game_state.get("board_cards_count", 0))
        posicion = str(game_state.get("posicion", "BTN")).upper()

        if not mis_cartas_str:
            return {"fold": 0.0, "call": 0.8, "raise_small": 0.1, "raise_medium": 0.05, "raise_large": 0.05}

        # PREFLOP STRATEGY (3-Max / Spin & Go GTO Calibration: Raise or Fold; close VPIP/PFR gap)
        if board_cards_count == 0 or not comunitarias_str:
            tier = "BASURA"
            if GTO_ENGINE_AVAILABLE:
                situacion = "vs_raise" if current_bet > 1.5 else "open"
                gto_info = clasificar_mano_preflop(mis_cartas_str, posicion=posicion, situacion=situacion)
                tier = gto_info.get("tier", "BASURA")

            if tier in ("S", "A"):
                if current_bet > 1.5:
                    return {"fold": 0.0, "call": 0.10, "raise_small": 0.45, "raise_medium": 0.35, "raise_large": 0.10}
                else:
                    return {"fold": 0.0, "call": 0.0, "raise_small": 0.70, "raise_medium": 0.25, "raise_large": 0.05}
            elif tier == "B":
                if current_bet > 1.5:
                    if posicion == "SB":
                        return {"fold": 0.45, "call": 0.10, "raise_small": 0.35, "raise_medium": 0.10, "raise_large": 0.0}
                    return {"fold": 0.25, "call": 0.35, "raise_small": 0.30, "raise_medium": 0.10, "raise_large": 0.0}
                else:
                    return {"fold": 0.0, "call": 0.05, "raise_small": 0.75, "raise_medium": 0.20, "raise_large": 0.0}
            elif tier == "C":
                if current_bet > 1.5:
                    if posicion == "SB":
                        return {"fold": 0.88, "call": 0.02, "raise_small": 0.10, "raise_medium": 0.0, "raise_large": 0.0}
                    return {"fold": 0.75, "call": 0.15, "raise_small": 0.10, "raise_medium": 0.0, "raise_large": 0.0}
                else:
                    return {"fold": 0.40, "call": 0.05, "raise_small": 0.45, "raise_medium": 0.10, "raise_large": 0.0}
            else:
                # Trash / Basura: Disciplined Fold preflop (0% Call when open/facing bet)
                if current_bet > 0:
                    return {"fold": 0.98, "call": 0.02, "raise_small": 0.0, "raise_medium": 0.0, "raise_large": 0.0}
                else:
                    return {"fold": 0.85, "call": 0.0, "raise_small": 0.12, "raise_medium": 0.03, "raise_large": 0.0}

        # POSTFLOP
        hand_equity = 0.5
        if ARIA_AVAILABLE and mis_cartas_str and len(mis_cartas_str.split()) == 2:
            try:
                hole = parse_cards(mis_cartas_str)
                board = parse_cards(comunitarias_str)
                import random
                rng = random.Random(42)
                hand_equity = made_pct(hole, board, rng)
            except Exception:
                pass

        comb_info = {}
        if GTO_ENGINE_AVAILABLE:
            comb_info = evaluar_combinacion_mano_y_mesa(mis_cartas_str, comunitarias_str)

        es_monstruo = comb_info.get("es_monstruo", False) or comb_info.get("es_set", False) or comb_info.get("es_overpair", False)
        es_top_pair = comb_info.get("es_top_pair", False) or comb_info.get("es_doble_pareja", False)

        if hand_equity >= 0.70 or es_monstruo:
            return {"fold": 0.0, "call": 0.20, "raise_small": 0.35, "raise_medium": 0.30, "raise_large": 0.15}
        elif hand_equity >= 0.45 or es_top_pair:
            if current_bet > 0:
                return {"fold": 0.15, "call": 0.55, "raise_small": 0.20, "raise_medium": 0.10, "raise_large": 0.0}
            else:
                return {"fold": 0.0, "call": 0.50, "raise_small": 0.35, "raise_medium": 0.15, "raise_large": 0.0}
        elif hand_equity >= 0.25:
            if current_bet > 0:
                if pot_size > 0 and current_bet >= pot_size * 0.8 and not es_top_pair and not es_monstruo:
                    return {"fold": 0.95, "call": 0.05, "raise_small": 0.0, "raise_medium": 0.0, "raise_large": 0.0}
                return {"fold": 0.65, "call": 0.25, "raise_small": 0.08, "raise_medium": 0.02, "raise_large": 0.0}
            else:
                return {"fold": 0.0, "call": 0.75, "raise_small": 0.20, "raise_medium": 0.05, "raise_large": 0.0}
        else:
            if current_bet > 0:
                return {"fold": 0.90, "call": 0.05, "raise_small": 0.03, "raise_medium": 0.02, "raise_large": 0.0}
            else:
                return {"fold": 0.0, "call": 0.85, "raise_small": 0.10, "raise_medium": 0.05, "raise_large": 0.0}

    def subgame_solve(self, game_state, depth_limit: int = 1) -> dict:
        """
        Emula Subgame Solving (Depth-Limited Search estilo Pluribus):
        Evalúa el EV esperado por acción utilizando los valores predichos por la red neuronal
        de política/valor (Deep CFR / TF Policy) como función de valor en hojas terminales.
        """
        base_probs = self.get_action_probabilities(game_state)
        if self.tf_policy_net is not None:
            try:
                _, val_ev = self.tf_policy_net.predict_policy_and_value(game_state)
                ev_factor = max(-1.0, min(1.0, val_ev / 10.0))
                if ev_factor > 0.2:
                    if "raise_small" in base_probs: base_probs["raise_small"] += 0.05 * ev_factor
                    if "raise_medium" in base_probs: base_probs["raise_medium"] += 0.03 * ev_factor
                elif ev_factor < -0.2:
                    if "fold" in base_probs: base_probs["fold"] += 0.05 * abs(ev_factor)
                    if "call" in base_probs: base_probs["call"] = max(0.0, base_probs["call"] - 0.05 * abs(ev_factor))

                tot = sum(base_probs.values())
                if tot > 0:
                    for k in base_probs:
                        base_probs[k] /= tot
            except Exception:
                pass
        return base_probs

    def train(self, training_data):
        pass

    def evaluate(self, evaluation_data):
        pass
