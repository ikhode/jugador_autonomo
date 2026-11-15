
# decision_maker.py
import numpy as np

class DecisionMaker:
    def __init__(self, strategic_engine, opponent_model):
        self.strategic_engine = strategic_engine
        self.opponent_model = opponent_model

    def make_decision(self, game_state, historical_actions, opponent_profile):
        # 1. Get base probabilities from the strategic engine
        base_probabilities = self.strategic_engine.get_action_probabilities(game_state)
        print(f"Base probabilities from strategic engine: {base_probabilities}")

        # 2. Get opponent prediction from the opponent model
        opponent_prediction = self.opponent_model.predict_opponent_action(game_state, historical_actions, opponent_profile)
        predicted_opponent_action = opponent_prediction["predicted_action"]
        opponent_action_probabilities = opponent_prediction["probabilities"]
        print(f"Opponent model prediction: {predicted_opponent_action} with probabilities: {opponent_action_probabilities}")

        # 3. Adjust base probabilities based on opponent model (exploitation layer)
        adjusted_probabilities = self._adjust_probabilities(base_probabilities, predicted_opponent_action, opponent_action_probabilities)
        print(f"Adjusted probabilities: {adjusted_probabilities}")

        # 4. Select a final action based on adjusted probabilities
        final_action = self._select_action(adjusted_probabilities)
        print(f"Final action decided: {final_action}")
        return final_action

    def _adjust_probabilities(self, base_probabilities, predicted_opponent_action, opponent_action_probabilities):
        # This is where the core exploitation logic resides.
        # Example: If opponent is highly likely to fold, increase raise probability.
        # This logic needs to be carefully tuned and potentially learned.

        adjusted_probs = base_probabilities.copy()

        # Simple example: if opponent is predicted to fold with high probability,
        # slightly increase the probability of a raise from our side.
        if predicted_opponent_action == "fold" and opponent_action_probabilities.get("fold", 0) > 0.6:
            if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.05
            if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.03
            if "call" in adjusted_probs: adjusted_probs["call"] = max(0, adjusted_probs["call"] - 0.08) # Reduce call prob

        # Normalize probabilities to ensure they sum to 1
        total_prob = sum(adjusted_probs.values())
        if total_prob > 0:
            for action, prob in adjusted_probs.items():
                adjusted_probs[action] = prob / total_prob
        else:
            # Fallback if somehow all probabilities are zero
            # This should ideally not happen if base_probabilities are well-formed
            print("Warning: All probabilities are zero, falling back to default.")
            return {"fold": 0.33, "call": 0.34, "raise_small": 0.33} # Default fallback

        return adjusted_probs

    def _select_action(self, probabilities):
        actions = list(probabilities.keys())
        probs = list(probabilities.values())
        # Ensure probabilities sum to 1 for np.random.choice
        probs = np.array(probs) / np.sum(probs)
        chosen_action = np.random.choice(actions, p=probs)
        return chosen_action

