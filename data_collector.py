# data_collector.py
import json
import os
import time
from datetime import datetime
import numpy as np

try:
    from analytics_db import AnalyticsStorage
except ImportError:
    AnalyticsStorage = None

class DataCollector:
    def __init__(self, data_dir="game_data", db_path=None):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.game_logs = []
        self.storage = AnalyticsStorage(db_path=db_path) if AnalyticsStorage and db_path else (AnalyticsStorage() if AnalyticsStorage else None)

    def log_game_state(self, game_state, action, reward, opponent_action=None):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = {
            "game_state": game_state,
            "agent_action": action,
            "reward": reward,
            "opponent_action": opponent_action,
            "timestamp": ts
        }
        self.game_logs.append(log_entry)
        print(f"Logged game state and action: {action}")

        # Also persist to SQLite realtime events table if available
        if self.storage:
            try:
                self.storage.registrar_evento_tiempo_real({
                    "event_type": "DATA_COLLECTOR_LOG",
                    "timestamp": ts,
                    "pot_size": float((game_state or {}).get("pot_size") or 0.0),
                    "current_bet": float((game_state or {}).get("current_bet") or 0.0),
                    "hero_cards": str((game_state or {}).get("mis_cartas") or ""),
                    "board_cards": str((game_state or {}).get("cartas_comunitarias") or ""),
                    "chosen_action": str(action),
                    "extra_data": {
                        "reward": reward,
                        "opponent_action": opponent_action
                    }
                })
            except Exception as e:
                print(f"⚠️ Error logging game state to SQLite: {e}")

    def log_realtime_event(
        self,
        event_type: str,
        game_state: dict = None,
        chosen_action: str = "",
        action_amount: float = 0.0,
        player_predictions: dict = None,
        gto_probs: dict = None,
        exploitative_probs: dict = None,
        latency_ms: float = 0.0,
        extra_data: dict = None
    ) -> str:
        """
        Registra un evento en tiempo real (WS, predicción de rival, decisión del agente) en SQLite.
        """
        gs = game_state or {}
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        event_payload = {
            "timestamp": ts,
            "event_type": event_type,
            "street": str(gs.get("street") or "PREFLOP"),
            "pot_size": float(gs.get("pot_size") or 0.0),
            "current_bet": float(gs.get("current_bet") or 0.0),
            "hero_cards": str(gs.get("mis_cartas") or ""),
            "board_cards": str(gs.get("cartas_comunitarias") or ""),
            "player_predictions": player_predictions or {},
            "gto_probs": gto_probs or {},
            "exploitative_probs": exploitative_probs or {},
            "chosen_action": chosen_action,
            "action_amount": float(action_amount or 0.0),
            "latency_ms": float(latency_ms or 0.0),
            "extra_data": extra_data or {}
        }
        if self.storage:
            try:
                return self.storage.registrar_evento_tiempo_real(event_payload)
            except Exception as e:
                print(f"⚠️ Error al registrar evento tiempo real en SQLite: {e}")
        return ""

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
        X = []
        y = []
        for entry in self.game_logs:
            game_state = entry["game_state"]
            opponent_action = entry["opponent_action"]

            features = [
                game_state.get("pot_size", 0),
                game_state.get("current_bet", 0),
                game_state.get("board_cards_count", 0)
            ]

            if opponent_action is not None:
                X.append(features)
                action_map = {"fold": 0, "call": 1, "raise_small": 2, "raise_medium": 3, "raise_large": 4}
                y.append(action_map.get(opponent_action, -1))

        X_filtered = [x for i, x in enumerate(X) if y[i] != -1]
        y_filtered = [val for val in y if val != -1]

        print(f"Prepared {len(X_filtered)} samples for training data.")
        return np.array(X_filtered), np.array(y_filtered)
