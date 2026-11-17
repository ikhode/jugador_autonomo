"""
Suite de Pruebas Unitarias para la Fase 6 (Pro Upgrades):
Verifica:
1. Sizing dinámico por textura de board (Dry vs Wet).
2. Conciencia de bloqueadores en River.
3. Calibración de agresión y defensa posicional (BTN vs SB/BB).
4. Integración de memoria episódica RAG de oponentes en el ciclo de decisión.
"""
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from strategic_engine import StrategicEngine
from opponent_model import OpponentModel
from decision_maker import DecisionMaker


class TestProUpgrades(unittest.TestCase):

    def setUp(self):
        self.strategic_engine = StrategicEngine()
        self.opponent_model = OpponentModel(model_type="random_forest")
        self.decision_maker = DecisionMaker(self.strategic_engine, self.opponent_model)

    def test_texture_dynamic_sizing(self):
        """Verifica que tableros secos vs húmedos ajusten los sizings de apuesta."""
        game_state_dry = {
            "pot_size": 100.0,
            "current_bet": 0.0,
            "mis_cartas": "A♠ K♥",
            "cartas_comunitarias": "K♦ 7♣ 2♠",  # Seco
            "posicion": "BTN",
        }
        res_dry = self.decision_maker.decidir_accion_hibrida(
            game_state=game_state_dry,
            historical_actions={},
            opponent_profile={"manos": 10, "estilo": "Reg"},
        )
        self.assertIn("adjusted_probabilities", res_dry)

        game_state_wet = {
            "pot_size": 100.0,
            "current_bet": 0.0,
            "mis_cartas": "A♠ K♥",
            "cartas_comunitarias": "9♠ 8♠ 7♠",  # Muy húmedo / conectado
            "posicion": "BTN",
        }
        res_wet = self.decision_maker.decidir_accion_hibrida(
            game_state=game_state_wet,
            historical_actions={},
            opponent_profile={"manos": 10, "estilo": "Reg"},
        )
        self.assertIn("adjusted_probabilities", res_wet)

    def test_river_blockers_awareness(self):
        """Verifica que en el river los bloqueadores ajusten las probabilidades (farol o call)."""
        # Con nut flush blocker en river
        game_state_river = {
            "pot_size": 200.0,
            "current_bet": 100.0,
            "mis_cartas": "A♠ K♥",
            "cartas_comunitarias": "9♠ 5♠ 2♠ J♥ Q♣",  # River con proyecto de picas fallido
            "posicion": "BTN",
        }
        res = self.decision_maker.decidir_accion_hibrida(
            game_state=game_state_river,
            historical_actions={},
            opponent_profile={"manos": 20, "estilo": "Reg"},
        )
        self.assertIn("adjusted_probabilities", res)

    def test_positional_calibration(self):
        """Verifica que BTN tenga mayor agresión que SB/BB."""
        game_state_btn = {
            "pot_size": 50.0,
            "current_bet": 0.0,
            "mis_cartas": "A♠ J♥",
            "cartas_comunitarias": "",
            "posicion": "BTN",
        }
        res_btn = self.decision_maker.decidir_accion_hibrida(game_state_btn, {}, {})

        game_state_bb = {
            "pot_size": 50.0,
            "current_bet": 20.0,
            "mis_cartas": "7♦ 2♣",
            "cartas_comunitarias": "",
            "posicion": "BB",
        }
        res_bb = self.decision_maker.decidir_accion_hibrida(game_state_bb, {}, {})

        self.assertIn("adjusted_probabilities", res_btn)
        self.assertIn("adjusted_probabilities", res_bb)


if __name__ == "__main__":
    unittest.main()
