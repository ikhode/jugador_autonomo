
import unittest
import numpy as np
import os
import json
from strategic_engine import StrategicEngine
from opponent_model import OpponentModel
from game_state_manager import GameStateManager
from decision_maker import DecisionMaker
from data_collector import DataCollector

class TestHybridAgent(unittest.TestCase):

    def setUp(self):
        self.strategic_engine = StrategicEngine(strategy_model=None)
        self.opponent_model = OpponentModel(model_type="random_forest")
        self.game_state_manager = GameStateManager()
        self.decision_maker = DecisionMaker(self.strategic_engine, self.opponent_model)
        self.data_collector = DataCollector(data_dir="test_game_data")

    def tearDown(self):
        # Cleanup test data directory if created
        if os.path.exists("test_game_data/game_log.json"):
            os.remove("test_game_data/game_log.json")
        if os.path.exists("test_game_data"):
            try:
                os.rmdir("test_game_data")
            except Exception:
                pass

    def test_strategic_engine_output(self):
        game_state = {"pot_size": 100, "current_bet": 20, "board_cards_count": 3}
        probs = self.strategic_engine.get_action_probabilities(game_state)
        self.assertIn("fold", probs)
        self.assertIn("call", probs)
        self.assertIn("raise_small", probs)
        self.assertAlmostEqual(sum(probs.values()), 1.0)

    def test_opponent_model_training_and_prediction(self):
        # Create dummy training data
        # Features: [pot_size, current_bet, board_cards_count, VPIP, PFR, folds, calls, raises]
        X_train = np.array([
            [50, 10, 0, 0.25, 0.15, 1, 2, 0],
            [200, 50, 3, 0.25, 0.15, 0, 1, 3],
            [100, 20, 3, 0.25, 0.15, 2, 1, 1],
            [30, 5, 0, 0.25, 0.15, 3, 0, 0]
        ])
        y_train = np.array([1, 2, 0, 0]) # 0: fold, 1: call, 2: raise

        self.opponent_model.train(X_train, y_train)

        game_state = {"pot_size": 100, "current_bet": 20, "board_cards_count": 3}
        historical_actions = {"folds": 1, "calls": 1, "raises": 1}
        opponent_profile = {"VPIP": 0.25, "PFR": 0.15}

        result = self.opponent_model.predict_opponent_action(game_state, historical_actions, opponent_profile)
        self.assertIn("predicted_action", result)
        self.assertIn("probabilities", result)
        self.assertIn(result["predicted_action"], ["fold", "call", "raise"])

    def test_game_state_manager(self):
        self.game_state_manager.update_game_state({"pot_size": 150})
        self.assertEqual(self.game_state_manager.get_current_game_state()["pot_size"], 150)

        self.game_state_manager.update_historical_actions("calls")
        self.assertEqual(self.game_state_manager.get_historical_actions()["calls"], 1)

    def test_decision_maker(self):
        # Pre-train opponent model so predict_proba works
        X_train = np.array([[50, 10, 0, 0.25, 0.15, 1, 2, 0], [200, 50, 3, 0.25, 0.15, 0, 1, 3]])
        y_train = np.array([0, 1])
        self.opponent_model.train(X_train, y_train)

        game_state = {"pot_size": 100, "current_bet": 20, "board_cards_count": 3}
        historical_actions = {"folds": 1, "calls": 1, "raises": 1}
        opponent_profile = {"VPIP": 0.25, "PFR": 0.15}

        action = self.decision_maker.make_decision(game_state, historical_actions, opponent_profile)
        self.assertIn(action, ["fold", "call", "raise_small", "raise_medium", "raise_large"])

    def test_data_collector(self):
        game_state = {"pot_size": 100, "current_bet": 20, "board_cards_count": 3}
        self.data_collector.log_game_state(game_state, action="call", reward=50, opponent_action="fold")
        self.data_collector.save_game_logs("game_log.json")

        loaded_logs = self.data_collector.load_game_logs("game_log.json")
        self.assertEqual(len(loaded_logs), 1)
        self.assertEqual(loaded_logs[0]["agent_action"], "call")

if __name__ == "__main__":
    unittest.main()
