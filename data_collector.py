
# data_collector.py
import json
import os

class DataCollector:
    def __init__(self, data_dir="game_data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.game_logs = []

    def log_game_state(self, game_state, action, reward, opponent_action=None):
        log_entry = {
            "game_state": game_state,
            "agent_action": action,
            "reward": reward,
            "opponent_action": opponent_action,
            "timestamp": os.path.getmtime(self.data_dir) # Placeholder for actual timestamp
        }
        self.game_logs.append(log_entry)
        print(f"Logged game state and action: {action}")

    def save_game_logs(self, filename="game_log.json"):
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, 'w') as f:
            json.dump(self.game_logs, f, indent=4)
        print(f"Game logs saved to {filepath}")
        self.game_logs = [] # Clear logs after saving

    def load_game_logs(self, filename="game_log.json"):
        filepath = os.path.join(self.data_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                self.game_logs = json.load(f)
            print(f"Game logs loaded from {filepath}")
            return self.game_logs
        else:
            print("No game logs found to load.")
            return []

    def get_training_data(self):
        # This method would process raw game logs into a format suitable for model training
        # For opponent model, features (X) would be game_state, historical_actions, opponent_profile
        # and target (y) would be opponent_action.
        X = []
        y = []
        for entry in self.game_logs:
            # Example: Extract features and labels for opponent model training
            # This is highly dependent on the actual feature engineering in OpponentModel
            game_state = entry["game_state"]
            opponent_action = entry["opponent_action"]

            # Dummy feature extraction - replace with actual logic
            features = [
                game_state.get("pot_size", 0),
                game_state.get("current_bet", 0),
                game_state.get("board_cards_count", 0)
            ]

            if opponent_action is not None:
                X.append(features)
                # Encode opponent_action to an integer for classification
                # e.g., {"fold": 0, "call": 1, "raise": 2}
                action_map = {"fold": 0, "call": 1, "raise_small": 2, "raise_medium": 3, "raise_large": 4}
                y.append(action_map.get(opponent_action, -1)) # -1 for unknown actions

        # Filter out entries where opponent_action was not relevant or unknown
        X_filtered = [x for i, x in enumerate(X) if y[i] != -1]
        y_filtered = [val for val in y if val != -1]

        print(f"Prepared {len(X_filtered)} samples for training data.")
        return np.array(X_filtered), np.array(y_filtered)

import numpy as np # Import numpy here if not already at top

