
# opponent_model.py
from sklearn.ensemble import RandomForestClassifier
import xgboost as xgb
import numpy as np
import sys
import os

# Ensure aria_poker is in path for equity/engine integration
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'aria_poker'))
try:
    from engine import parse_cards
    from equity import preflop_pct, made_pct
    ARIA_AVAILABLE = True
except Exception:
    ARIA_AVAILABLE = False

class OpponentModel:
    def __init__(self, model_type="random_forest"):
        if model_type == "random_forest":
            self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        elif model_type == "xgboost":
            self.model = xgb.XGBClassifier(n_estimators=100, use_label_encoder=False, eval_metric='logloss', random_state=42)
        else:
            raise ValueError("model_type must be 'random_forest' or 'xgboost'")
        self.feature_names = []

    def _engineer_features(self, game_state, historical_actions, opponent_profile):
        """
        Engineered features incorporating raw game state, opponent profile,
        historical actions, and advanced Aria poker equity / hand strength metrics.
        """
        pot_size = float(game_state.get("pot_size", 0))
        current_bet = float(game_state.get("current_bet", 0))
        board_cards_count = int(game_state.get("board_cards_count", 0))

        # Advanced feature: Pot Odds
        pot_odds = current_bet / (pot_size + current_bet + 1e-6)

        # Advanced feature: Aria Equity / Hand Strength Percentile
        hand_strength = 0.5 # Default fallback
        if ARIA_AVAILABLE:
            try:
                mis_cartas_str = game_state.get("mis_cartas", "")
                comunitarias_str = game_state.get("cartas_comunitarias", "")
                if mis_cartas_str and len(mis_cartas_str.split()) == 2:
                    hole = parse_cards(mis_cartas_str)
                    if comunitarias_str:
                        board = parse_cards(comunitarias_str)
                        import random
                        rng = random.Random(42)
                        hand_strength = made_pct(hole, board, rng)
                    else:
                        hand_strength = preflop_pct(hole)
            except Exception:
                pass

        features = [
            pot_size,
            current_bet,
            pot_odds,
            board_cards_count,
            hand_strength,
            float(opponent_profile.get("VPIP", 0.25)),
            float(opponent_profile.get("PFR", 0.15)),
            float(historical_actions.get("folds", 0)),
            float(historical_actions.get("calls", 0)),
            float(historical_actions.get("raises", 0))
        ]

        return np.array(features).reshape(1, -1)

    def train(self, X_train, y_train, feature_names=None):
        print(f"Training opponent model with {len(X_train)} samples (advanced Aria features enabled)...")
        self.model.fit(X_train, y_train)
        if feature_names:
            self.feature_names = feature_names

    def predict_opponent_action(self, game_state, historical_actions, opponent_profile):
        features = self._engineer_features(game_state, historical_actions, opponent_profile)
        if not hasattr(self.model, 'predict_proba'):
            predicted_action = self.model.predict(features)
            return {"predicted_action": predicted_action[0], "probabilities": {}}

        probabilities = self.model.predict_proba(features)[0]
        action_labels = ["fold", "call", "raise"]
        action_probs = {action_labels[i]: prob for i, prob in enumerate(probabilities)}
        predicted_action_label = action_labels[np.argmax(probabilities)]

        return {"predicted_action": predicted_action_label, "probabilities": action_probs}

    def save_model(self, path):
        import joblib
        joblib.dump(self.model, path)
        print(f"Opponent model saved to {path}")

    def load_model(self, path):
        import joblib
        self.model = joblib.load(path)
        print(f"Opponent model loaded from {path}")
