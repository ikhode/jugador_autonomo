
import time
import numpy as np
from strategic_engine import StrategicEngine
from opponent_model import OpponentModel
from game_state_manager import GameStateManager
from decision_maker import DecisionMaker
from data_collector import DataCollector

def run_simulation(num_iterations=1000, model_type="random_forest"):
    print(f"\n=========================================")
    print(f" Starting simulation ({model_type}) for {num_iterations} hands")
    print(f"=========================================\n")

    strategic_engine = StrategicEngine(strategy_model=None)
    opponent_model = OpponentModel(model_type=model_type)
    game_state_mgr = GameStateManager()
    decision_maker = DecisionMaker(strategic_engine, opponent_model)
    data_collector = DataCollector(data_dir=f"sim_data_{model_type}")

    # Train opponent model on sample dataset
    X_train = np.array([
        [50, 10, 0, 0.25, 0.15, 1, 2, 0],
        [200, 50, 3, 0.25, 0.15, 0, 1, 3],
        [100, 20, 3, 0.25, 0.15, 2, 1, 1],
        [30, 5, 0, 0.25, 0.15, 3, 0, 0]
    ])
    y_train = np.array([1, 2, 0, 0])
    opponent_model.train(X_train, y_train)

    inference_times = []
    actions_taken = {}

    start_sim_time = time.time()

    for i in range(num_iterations):
        # Generate random game state
        pot_size = np.random.randint(10, 500)
        current_bet = np.random.randint(0, 100)
        board_cards = np.random.choice([0, 3, 4, 5])

        game_state = {
            "pot_size": pot_size,
            "current_bet": current_bet,
            "board_cards_count": board_cards
        }

        historical_actions = {
            "folds": np.random.randint(0, 5),
            "calls": np.random.randint(0, 5),
            "raises": np.random.randint(0, 5)
        }

        opponent_profile = {"VPIP": 0.22, "PFR": 0.18}

        # Measure decision time (inference latency)
        t0 = time.time()
        action = decision_maker.make_decision(game_state, historical_actions, opponent_profile)
        t1 = time.time()

        latency_ms = (t1 - t0) * 1000
        inference_times.append(latency_ms)

        actions_taken[action] = actions_taken.get(action, 0) + 1

        # Log simulated hand
        data_collector.log_game_state(
            game_state=game_state,
            action=action,
            reward=np.random.randint(-50, 100),
            opponent_action=np.random.choice(["fold", "call", "raise"])
        )

    total_sim_time = time.time() - start_sim_time
    avg_latency = np.mean(inference_times)
    p95_latency = np.percentile(inference_times, 95)
    p99_latency = np.percentile(inference_times, 99)

    print(f"\nSimulation Results ({model_type}):")
    print(f"Total hands simulated: {num_iterations}")
    print(f"Total time elapsed: {total_sim_time:.2f} seconds")
    print(f"Average Inference Latency: {avg_latency:.4f} ms")
    print(f"95th Percentile Latency: {p95_latency:.4f} ms")
    print(f"99th Percentile Latency: {p99_latency:.4f} ms")
    print(f"Action Distribution: {actions_taken}\n")

if __name__ == "__main__":
    run_simulation(num_iterations=100, model_type="random_forest")
    run_simulation(num_iterations=100, model_type="xgboost")
