
# strategic_engine.py
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

class StrategicEngine:
    def __init__(self, strategy_model=None):
        self.strategy_model = strategy_model

    def get_action_probabilities(self, game_state):
        """
        Calculates GTO-derived base policy probabilities using real preflop classification,
        hand combination evaluation, and Monte Carlo equity metrics.
        """
        mis_cartas_str = str(game_state.get("mis_cartas", "") or "").strip()
        comunitarias_str = str(game_state.get("cartas_comunitarias", "") or "").strip()
        pot_size = float(game_state.get("pot_size", 0.0))
        current_bet = float(game_state.get("current_bet", 0.0))
        board_cards_count = int(game_state.get("board_cards_count", 0))
        posicion = str(game_state.get("posicion", "BTN")).upper()

        print(f"Strategic Engine evaluating: cards='{mis_cartas_str}', board='{comunitarias_str}', bet={current_bet}")

        # Fallback distribution if no cards are available
        if not mis_cartas_str:
            return {"fold": 0.0, "call": 0.8, "raise_small": 0.1, "raise_medium": 0.05, "raise_large": 0.05}

        # ---------------------------------------------------------
        # PREFLOP STRATEGY (board_cards_count == 0)
        # ---------------------------------------------------------
        if board_cards_count == 0 or not comunitarias_str:
            tier = "BASURA"
            if GTO_ENGINE_AVAILABLE:
                situacion = "vs_raise" if current_bet > 1.5 else "open"
                gto_info = clasificar_mano_preflop(mis_cartas_str, posicion=posicion, situacion=situacion)
                tier = gto_info.get("tier", "BASURA")

            if tier == "A":
                # Monster preflop hands (AA, KK, QQ, JJ, AKs)
                return {"fold": 0.0, "call": 0.1, "raise_small": 0.3, "raise_medium": 0.4, "raise_large": 0.2}
            elif tier == "B":
                # Strong preflop hands (TT-88, AQ, AJ, ATs, KQs)
                return {"fold": 0.0, "call": 0.3, "raise_small": 0.4, "raise_medium": 0.2, "raise_large": 0.1}
            elif tier == "C":
                # Marginal preflop hands
                if current_bet > 0:
                    return {"fold": 0.35, "call": 0.45, "raise_small": 0.15, "raise_medium": 0.05, "raise_large": 0.0}
                else:
                    return {"fold": 0.0, "call": 0.55, "raise_small": 0.30, "raise_medium": 0.15, "raise_large": 0.0}
            else:
                # Trash / Basura hands (72o, 95o, K2o, 52o, 34o, etc.)
                if current_bet > 0:
                    return {"fold": 0.85, "call": 0.10, "raise_small": 0.03, "raise_medium": 0.02, "raise_large": 0.0}
                else:
                    return {"fold": 0.0, "call": 0.80, "raise_small": 0.15, "raise_medium": 0.05, "raise_large": 0.0}

        # ---------------------------------------------------------
        # POSTFLOP STRATEGY (board_cards_count > 0)
        # ---------------------------------------------------------
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

        # High Equity / Strong Made Hand (>0.70 equity or monster combo)
        if hand_equity >= 0.70 or es_monstruo:
            return {"fold": 0.0, "call": 0.20, "raise_small": 0.35, "raise_medium": 0.30, "raise_large": 0.15}

        # Medium Equity / Solid Pair / Good Draw (0.45 - 0.70 equity)
        elif hand_equity >= 0.45 or es_top_pair:
            if current_bet > 0:
                return {"fold": 0.15, "call": 0.55, "raise_small": 0.20, "raise_medium": 0.10, "raise_large": 0.0}
            else:
                return {"fold": 0.0, "call": 0.50, "raise_small": 0.35, "raise_medium": 0.15, "raise_large": 0.0}

        # Weak Equity / Weak Pair / Gutshot (0.25 - 0.45 equity)
        elif hand_equity >= 0.25:
            if current_bet > 0:
                return {"fold": 0.65, "call": 0.25, "raise_small": 0.08, "raise_medium": 0.02, "raise_large": 0.0}
            else:
                return {"fold": 0.0, "call": 0.75, "raise_small": 0.20, "raise_medium": 0.05, "raise_large": 0.0}

        # Low Equity / Trash High Card (<0.25 equity)
        else:
            if current_bet > 0:
                return {"fold": 0.90, "call": 0.05, "raise_small": 0.03, "raise_medium": 0.02, "raise_large": 0.0}
            else:
                return {"fold": 0.0, "call": 0.85, "raise_small": 0.10, "raise_medium": 0.05, "raise_large": 0.0}

    def train(self, training_data):
        pass

    def evaluate(self, evaluation_data):
        pass
