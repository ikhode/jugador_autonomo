import unittest
import os
import time
import tempfile
from pathlib import Path
import numpy as np

from analytics_db import AnalyticsStorage, inicializar_esquema_analiticas
from analytics_logger import PokerAnalyticsLogger
from data_collector import DataCollector
from strategic_engine import StrategicEngine
from opponent_model import OpponentModel
from decision_maker import DecisionMaker
import config

class TestRealtimeLoggingAndCashGame(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_poker_stats.db"
        self.storage = AnalyticsStorage(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cash_game_config_defaults(self):
        self.assertEqual(config.POKER_MODE, "Cash Game Texas Hold'em")
        self.assertEqual(config.MAX_TABLE_SEATS, 6)
        self.assertIn(".game-category-tile.cash-game", config.SELECTORS["select_game_card"])

    def test_realtime_sqlite_event_logging(self):
        event_data = {
            "event_type": "LOCAL_AGENT_DECISION",
            "street": "FLOP",
            "pot_size": 150.0,
            "current_bet": 30.0,
            "hero_cards": "Ah Kh",
            "board_cards": "2h 7h Qs",
            "player_predictions": {"predicted_action": "call", "probabilities": {"fold": 0.2, "call": 0.7, "raise": 0.1}},
            "gto_probs": {"fold": 0.0, "call": 0.6, "raise_small": 0.4},
            "exploitative_probs": {"fold": 0.0, "call": 0.5, "raise_small": 0.5},
            "chosen_action": "RAISE",
            "action_amount": 60.0,
            "latency_ms": 1.25,
            "extra_data": {"sugerencia_raw": "raise_small"}
        }

        event_uid = self.storage.registrar_evento_tiempo_real(event_data)
        self.assertTrue(event_uid.startswith("evt_"))

        events = self.storage.obtener_eventos_tiempo_real(limit=10, event_type="LOCAL_AGENT_DECISION")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["chosen_action"], "RAISE")
        self.assertEqual(events[0]["pot_size"], 150.0)
        self.assertEqual(events[0]["latency_ms"], 1.25)

    def test_async_logger_realtime_dispatch(self):
        logger = PokerAnalyticsLogger(
            version_id="test_v1",
            db_path=self.db_path,
            async_mode=True
        )

        logger.registrar_evento_tiempo_real({
            "event_type": "WS_CLASS_10017",
            "street": "PREFLOP",
            "pot_size": 40.0,
            "chosen_action": "WAIT"
        })

        logger.flush(timeout=3.0)
        logger.shutdown(wait=True)

        events = self.storage.obtener_eventos_tiempo_real(limit=10, event_type="WS_CLASS_10017")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["pot_size"], 40.0)

    def test_data_collector_sqlite_integration(self):
        collector = DataCollector(data_dir=self.temp_dir.name, db_path=self.db_path)
        collector.log_realtime_event(
            event_type="OPPONENT_PREDICTION",
            game_state={"pot_size": 100, "current_bet": 20, "mis_cartas": "Ac Kd", "street": "TURN"},
            player_predictions={"predicted_action": "fold", "probabilities": {"fold": 0.65, "call": 0.3, "raise": 0.05}},
            latency_ms=0.85
        )

        events = self.storage.obtener_eventos_tiempo_real(limit=10, event_type="OPPONENT_PREDICTION")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["street"], "TURN")

    def test_decision_maker_decidir_accion_hibrida(self):
        strategic_engine = StrategicEngine()
        opponent_model = OpponentModel(model_type="random_forest")

        X_train = np.array([
            [50, 10, 0.166, 0, 0.5, 0.25, 0.15, 1, 2, 0],
            [200, 50, 0.2, 3, 0.7, 0.25, 0.15, 0, 1, 3]
        ])
        y_train = np.array([0, 1])
        opponent_model.train(X_train, y_train)

        decision_maker = DecisionMaker(strategic_engine, opponent_model)

        game_state = {"pot_size": 100, "current_bet": 0, "board_cards_count": 3, "mis_cartas": "As Ks", "cartas_comunitarias": "Js 10s 2d"}
        historical_actions = {"folds": 1, "calls": 1, "raises": 1}
        opponent_profile = {"VPIP": 0.25, "PFR": 0.15}

        res = decision_maker.decidir_accion_hibrida(game_state, historical_actions, opponent_profile)

        self.assertIn("final_action", res)
        self.assertIn("base_probabilities", res)
        self.assertIn("opponent_prediction", res)
        self.assertIn("adjusted_probabilities", res)
        self.assertEqual(res["adjusted_probabilities"].get("fold", 0.0), 0.0) # Check rule: zero fold when current_bet == 0

if __name__ == "__main__":
    unittest.main()
