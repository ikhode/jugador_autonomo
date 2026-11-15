
# opponent_model.py
from sklearn.ensemble import RandomForestClassifier
import xgboost as xgb
import numpy as np

class OpponentModel:
    def __init__(self, model_type="random_forest"):
        if model_type == "random_forest":
            self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        elif model_type == "xgboost":
            self.model = xgb.XGBClassifier(n_estimators=100, use_label_encoder=False, eval_metric='logloss', random_state=42)
        else:
            raise ValueError("model_type must be 'random_forest' or 'xgboost'")
        self.feature_names = [] # To store the names of features during training

    def _engineer_features(self, game_state, historical_actions, opponent_profile):
        # This is a critical step: convert raw game state into features for the model.
        # Example features:
        # - pot_odds (current_pot / (current_pot + to_call))
        # - stack_to_pot_ratio (effective_stack / current_pot)
        # - position (0 for out of position, 1 for in position)
        # - last_opponent_action (e.g., encoded as 0:fold, 1:call, 2:raise)
        # - number_of_raises_this_street
        # - opponent_tightness_aggressiveness_profile

        features = []

        # Dummy features for now
        features.append(game_state.get("pot_size", 0))
        features.append(game_state.get("current_bet", 0))
        features.append(game_state.get("board_cards_count", 0))

        # Example of how opponent profile might be used
        features.append(opponent_profile.get("VPIP", 0.25)) # Voluntarily Put In Pot
        features.append(opponent_profile.get("PFR", 0.15)) # Pre-flop Raise

        # Add historical actions (e.g., count of folds, calls, raises)
        folds = historical_actions.get("folds", 0)
        calls = historical_actions.get("calls", 0)
        raises = historical_actions.get("raises", 0)
        features.append(folds)
        features.append(calls)
        features.append(raises)

        return np.array(features).reshape(1, -1)

    def train(self, X_train, y_train, feature_names=None):
        # X_train: features, y_train: target (e.g., opponent's next action)
        print(f"Training opponent model with {len(X_train)} samples...")
        self.model.fit(X_train, y_train)
        if feature_names:
            self.feature_names = feature_names

    def predict_opponent_action(self, game_state, historical_actions, opponent_profile):
        features = self._engineer_features(game_state, historical_actions, opponent_profile)
        if not hasattr(self.model, 'predict_proba'):
            # Some models (e.g., raw SVC) might not have predict_proba
            # In such cases, a direct prediction or transformation might be needed
            predicted_action = self.model.predict(features)
            # Return a dummy probability distribution or handle as needed
            return {"predicted_action": predicted_action[0], "probabilities": {}}

        # Predict probabilities for each possible opponent action
        # Assuming target labels are integers corresponding to actions
        probabilities = self.model.predict_proba(features)[0]

        # Map probabilities back to meaningful action labels
        # This mapping depends on how 'y_train' was encoded during training
        # For example, if 0: fold, 1: call, 2: raise
        action_labels = ["fold", "call", "raise"]
        action_probs = {action_labels[i]: prob for i, prob in enumerate(probabilities)}

        # Get the action with the highest probability
        predicted_action_label = action_labels[np.argmax(probabilities)]

        print(f"Opponent Model prediction for game state {game_state}: {action_probs}")
        return {"predicted_action": predicted_action_label, "probabilities": action_probs}

    def save_model(self, path):
        import joblib
        joblib.dump(self.model, path)
        print(f"Opponent model saved to {path}")

    def load_model(self, path):
        import joblib
        self.model = joblib.load(path)
        print(f"Opponent model loaded from {path}")

