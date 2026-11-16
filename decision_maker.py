
# decision_maker.py
import numpy as np

class DecisionMaker:
    def __init__(self, strategic_engine, opponent_model):
        self.strategic_engine = strategic_engine
        self.opponent_model = opponent_model

    def make_decision(self, game_state, historical_actions, opponent_profile):
        result = self.decidir_accion_hibrida(game_state, historical_actions, opponent_profile)
        return result["final_action"]

    def decidir_accion_hibrida(self, game_state, historical_actions, opponent_profile):
        # 1. Get base probabilities from the GTO strategic engine
        base_probabilities = self.strategic_engine.get_action_probabilities(game_state)
        print(f"Base probabilities from strategic engine: {base_probabilities}")

        # 2. Get opponent prediction from the opponent model
        opponent_prediction = self.opponent_model.predict_opponent_action(game_state, historical_actions, opponent_profile)
        predicted_opponent_action = opponent_prediction["predicted_action"]
        opponent_action_probabilities = opponent_prediction["probabilities"]
        print(f"Opponent model prediction: {predicted_opponent_action} with probabilities: {opponent_action_probabilities}")

        # 3. Adjust base probabilities based on opponent model (exploitation layer)
        adjusted_probabilities = self._adjust_probabilities(base_probabilities, predicted_opponent_action, opponent_action_probabilities, game_state)
        print(f"Adjusted probabilities: {adjusted_probabilities}")

        # 4. Select a final action based on adjusted probabilities
        final_action = self._select_action(adjusted_probabilities)
        print(f"Final action decided: {final_action}")

        return {
            "final_action": final_action,
            "base_probabilities": base_probabilities,
            "opponent_prediction": opponent_prediction,
            "adjusted_probabilities": adjusted_probabilities
        }

    def _adjust_probabilities(self, base_probabilities, predicted_opponent_action, opponent_action_probabilities, game_state):
        adjusted_probs = base_probabilities.copy()
        current_bet = float(game_state.get("current_bet", 0.0))

        # Rule 1: Never fold when check is free (current_bet == 0)
        if current_bet == 0:
            adjusted_probs["fold"] = 0.0

        # Rule 2: Exploitative bluffing - if opponent folds frequently (>0.60 prob), increase raise frequencies
        if predicted_opponent_action == "fold" and opponent_action_probabilities.get("fold", 0) > 0.60:
            if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.08
            if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05
            if "call" in adjusted_probs: adjusted_probs["call"] = max(0.0, adjusted_probs["call"] - 0.10)

        # Rule 3: Exploitative value betting - if opponent calls frequently (>0.55 prob), increase raise/bet for value
        elif predicted_opponent_action == "call" and opponent_action_probabilities.get("call", 0) > 0.55:
            # If base policy was already leaning towards calling or raising with strong hand, favor value raise
            if adjusted_probs.get("call", 0) > 0.3 or adjusted_probs.get("raise_small", 0) > 0.2:
                if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.05
                if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05

        # Normalize probabilities to ensure they sum to 1
        total_prob = sum(adjusted_probs.values())
        if total_prob > 0:
            for action, prob in adjusted_probs.items():
                adjusted_probs[action] = prob / total_prob
        else:
            print("Warning: All probabilities are zero, falling back to default.")
            return {"fold": 0.0 if current_bet == 0 else 0.5, "call": 1.0 if current_bet == 0 else 0.5}

        return adjusted_probs

    def _select_action(self, probabilities):
        actions = list(probabilities.keys())
        probs = list(probabilities.values())
        probs = np.array(probs) / np.sum(probs)
        chosen_action = np.random.choice(actions, p=probs)
        return chosen_action
