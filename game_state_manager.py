
# game_state_manager.py

class GameStateManager:
    def __init__(self):
        self.current_game_state = {}
        self.historical_actions = {"folds": 0, "calls": 0, "raises": 0}
        self.opponent_profile = {"VPIP": 0.25, "PFR": 0.15} # Example opponent profile

    def update_game_state(self, new_state_data):
        self.current_game_state.update(new_state_data)
        print(f"Game state updated: {self.current_game_state}")

    def update_historical_actions(self, action_type):
        if action_type in self.historical_actions:
            self.historical_actions[action_type] += 1
        print(f"Historical actions updated: {self.historical_actions}")

    def get_current_game_state(self):
        return self.current_game_state

    def get_historical_actions(self):
        return self.historical_actions

    def get_opponent_profile(self):
        return self.opponent_profile

    def reset_game_state(self):
        self.current_game_state = {}
        self.historical_actions = {"folds": 0, "calls": 0, "raises": 0}
        print("Game state and historical actions reset.")

