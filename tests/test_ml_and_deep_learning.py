import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from opponent_model import OpponentModel
from opponents_profiler import _clasificar_rival
from deep_poker_model import (
    GameStateEncoder,
    PokerValuePolicyNetwork,
    DeepCFRSolver,
    DeepRLDQNAgent,
    ACTION_MAP,
)
from strategic_engine import StrategicEngine
from decision_maker import DecisionMaker


class TestMLAndDeepLearning(unittest.TestCase):

    def setUp(self):
        self.game_state = {
            "pot_size": 100.0,
            "current_bet": 20.0,
            "effective_stack": 500.0,
            "board_cards_count": 3,
            "mis_cartas": "A♠ K♦",
            "cartas_comunitarias": "A♣ 8♥ 3♦",
            "posicion": "BTN",
            "perfil_rival": {"vpip_pct": 35.0, "pfr_pct": 22.0, "agresividad": 2.1},
        }
        self.historical_actions = {"folds": 1, "calls": 2, "raises": 1}
        self.opponent_profile = {"vpip_pct": 35.0, "pfr_pct": 22.0, "agresividad": 2.1}

    def test_01_sklearn_opponent_model_types_and_bluff_prediction(self):
        for m_type in ("random_forest", "gradient_boosting", "logistic_regression", "svm", "xgboost"):
            model = OpponentModel(model_type=m_type)
            X_dummy = [
                [50, 10, 0.16, 0, 0.5, 0.25, 0.15, 1, 2, 0],
                [200, 50, 0.20, 3, 0.7, 0.25, 0.15, 0, 1, 3],
                [100, 20, 0.16, 4, 0.4, 0.40, 0.25, 2, 1, 4],
                [30, 5, 0.14, 0, 0.3, 0.10, 0.05, 4, 0, 0],
            ]
            y_dummy = [0, 1, 2, 0]
            model.train(X_dummy, y_dummy)

            res = model.predict_opponent_action(self.game_state, self.historical_actions, self.opponent_profile)
            self.assertIn(res["predicted_action"], ("fold", "call", "raise"))
            self.assertIsInstance(res["probabilities"], dict)

            bluff_p = model.predict_bluff_probability(self.game_state, self.historical_actions, self.opponent_profile)
            self.assertTrue(0.0 <= bluff_p <= 1.0)

    def test_02_train_from_database(self):
        model = OpponentModel(model_type="random_forest")
        summary = model.train_from_database()
        self.assertIn("samples_trained", summary)
        self.assertGreater(summary["samples_trained"], 0)

    def test_03_game_state_encoder(self):
        encoder = GameStateEncoder(vector_dim=64)
        vec = encoder.encode(self.game_state)
        self.assertEqual(vec.shape, (64,))
        self.assertEqual(vec.dtype, np.float32)
        # Verify button position encoding
        self.assertEqual(vec[5], 1.0)

    def test_04_tf_poker_value_policy_network(self):
        policy_net = PokerValuePolicyNetwork(input_dim=64, num_actions=6)
        probs, val_ev = policy_net.predict_policy_and_value(self.game_state)
        self.assertIn("CALL", probs)
        self.assertIn("RAISE_SMALL", probs)
        self.assertIsInstance(val_ev, float)

        loss_dict = policy_net.train_on_batch(
            [self.game_state],
            np.array([[0.0, 0.2, 0.5, 0.2, 0.1, 0.0]], dtype=np.float32),
            np.array([[2.5]], dtype=np.float32),
        )
        self.assertIn("loss", loss_dict)

    def test_05_deep_cfr_solver(self):
        solver = DeepCFRSolver(input_dim=64, num_actions=6)
        regrets = solver.predict_regrets(self.game_state)
        self.assertEqual(len(regrets), 6)

        nash_strat = solver.get_nash_strategy(self.game_state)
        self.assertIn("FOLD", nash_strat)

        solver.record_counterfactual_regret(self.game_state, regrets)
        cfr_update = solver.update_cfr_networks(batch_size=1)
        self.assertIn("cfr_loss", cfr_update)

    def test_06_deep_rl_dqn_agent(self):
        agent = DeepRLDQNAgent(input_dim=64, num_actions=6)
        action = agent.select_action(self.game_state, legal_actions=["FOLD", "CALL", "RAISE_SMALL"])
        self.assertIn(action, ("FOLD", "CALL", "RAISE_SMALL"))

        agent.remember(self.game_state, "CALL", 1.5, self.game_state, False)
        loss = agent.train_step(batch_size=1)
        self.assertIsInstance(loss, float)

    def test_07_strategic_engine_deep_learning_integration(self):
        strat_engine = StrategicEngine()
        probs_gto = strat_engine.get_action_probabilities(self.game_state, model_mode="gto")
        self.assertIn("call", probs_gto)

        probs_tf = strat_engine.get_action_probabilities(self.game_state, model_mode="tensorflow")
        self.assertIn("call", probs_tf)

        probs_cfr = strat_engine.get_action_probabilities(self.game_state, model_mode="deep_cfr")
        self.assertIn("call", probs_cfr)

    def test_08_decision_maker_hybrid_integration(self):
        strat_engine = StrategicEngine()
        opp_model = OpponentModel(model_type="random_forest")
        opp_model.train_from_database()
        dec_maker = DecisionMaker(strat_engine, opp_model)

        res = dec_maker.decidir_accion_hibrida(
            self.game_state, self.historical_actions, self.opponent_profile, model_mode="gto"
        )
        self.assertIn("final_action", res)
        self.assertIn("bluff_probability", res)

    def test_09_opponents_profiler_sklearn_archetype_classification(self):
        estilo, tendencia = _clasificar_rival(manos=50, vpip=45.0, pfr=32.0, af=2.8, three_bet=12.0)
        self.assertIn(estilo, ("Maniac", "LAG", "Calling Station", "NIT", "TAG", "Regular equilibrado"))
        self.assertIn("[Scikit-Learn ML]", tendencia)

    def test_10_refined_opponent_profiling_and_sanitizer(self):
        from opponents_profiler import OpponentProfiler, sanitizar_nickname, es_nickname_invalido
        import tempfile

        # Sanitización de nicknames y filtrado de artefactos DOM
        self.assertTrue(es_nickname_invalido("Time: 12"))
        self.assertTrue(es_nickname_invalido("00:15"))
        self.assertTrue(es_nickname_invalido("Thinking..."))
        self.assertEqual(sanitizar_nickname("Jugador_01 [BTN] $1,500.00"), "Jugador_01")

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = os.path.join(tmp_dir, "test_poker_stats.db")
            profiler = OpponentProfiler(db_path=db_file)

            # Registrar acciones preflop y postflop (Check-Raise, Limp, C-Bet por calle)
            profiler.registrar_accion_rival(
                player_id="P123",
                nickname="ProGamer [BTN]",
                accion="CALL",
                calle="PREFLOP",
                hand_id="h1",
            )
            profiler.registrar_accion_rival(
                player_id="P123",
                nickname="ProGamer",
                accion="RAISE",
                calle="FLOP",
                is_cbet=True,
                is_check_raise=True,
                check_raise_opportunity=True,
                hand_id="h1",
            )

            perfil = profiler.obtener_perfil_explotativo("P123")
            self.assertEqual(perfil["nickname"], "ProGamer")
            self.assertIn("check_raise_pct", perfil)
            self.assertIn("limp_fold_pct", perfil)
            self.assertIn("cbet_flop_pct", perfil)
            self.assertIn("af_flop", perfil)
            profiler.close()
            profiler.dispose()
            del profiler


if __name__ == "__main__":
    unittest.main(verbosity=2)
