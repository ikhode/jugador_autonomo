# decision_maker.py
"""
Módulo Decisor Híbrido: Integra las probabilidades base (GTO o TensorFlow/Keras)
con el modelo de oponentes (Scikit-Learn/XGBoost) y detección de farol.
"""
import numpy as np


class DecisionMaker:
    def __init__(self, strategic_engine, opponent_model):
        self.strategic_engine = strategic_engine
        self.opponent_model = opponent_model

    def make_decision(self, game_state, historical_actions, opponent_profile, model_mode="gto"):
        result = self.decidir_accion_hibrida(game_state, historical_actions, opponent_profile, model_mode=model_mode)
        return result["final_action"]

    def decidir_accion_hibrida(self, game_state, historical_actions, opponent_profile, model_mode="gto"):
        # 1. Obtener probabilidades base desde el motor estratégico (GTO, TensorFlow MLP o Deep CFR)
        base_probabilities = self.strategic_engine.get_action_probabilities(game_state, model_mode=model_mode)

        # 2. Obtener predicciones y detección de farol desde el modelo de oponentes de Scikit-Learn
        opponent_prediction = self.opponent_model.predict_opponent_action(game_state, historical_actions, opponent_profile)
        bluff_prob = self.opponent_model.predict_bluff_probability(game_state, historical_actions, opponent_profile)
        opponent_prediction["bluff_probability"] = bluff_prob

        predicted_opponent_action = opponent_prediction["predicted_action"]
        opponent_action_probabilities = opponent_prediction["probabilities"]

        # 3. Ajustar probabilidades base aplicando reglas explotativas
        adjusted_probabilities = self._adjust_probabilities(
            base_probabilities,
            predicted_opponent_action,
            opponent_action_probabilities,
            bluff_prob,
            game_state,
        )

        # 4. Seleccionar la acción final basándose en la distribución calibrada
        final_action = self._select_action(adjusted_probabilities)

        return {
            "final_action": final_action,
            "base_probabilities": base_probabilities,
            "opponent_prediction": opponent_prediction,
            "bluff_probability": bluff_prob,
            "adjusted_probabilities": adjusted_probabilities
        }

    def _adjust_probabilities(self, base_probabilities, predicted_opponent_action, opponent_action_probabilities, bluff_prob, game_state):
        adjusted_probs = base_probabilities.copy()

        # Filtrar claves no de acción como 'predicted_ev_bb'
        eval_keys = [k for k in list(adjusted_probs.keys()) if k not in ("fold", "call", "raise_small", "raise_medium", "raise_large")]
        for ek in eval_keys:
            adjusted_probs.pop(ek, None)

        current_bet = float(game_state.get("current_bet", 0.0))

        # Regla 1: Nunca foldear si la opción de Check es gratis (current_bet == 0)
        if current_bet == 0:
            adjusted_probs["fold"] = 0.0

        # Regla 2: Farol explotativo - si el oponente foldea con frecuencia (>0.60 prob), aumentar subidas
        if predicted_opponent_action == "fold" and opponent_action_probabilities.get("fold", 0) > 0.60:
            if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.08
            if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05
            if "call" in adjusted_probs: adjusted_probs["call"] = max(0.0, adjusted_probs["call"] - 0.10)

        # Regla 3: Value bet explotativo - si el oponente paga con frecuencia (>0.55 prob), favorecer apuetas/subidas de valor
        elif predicted_opponent_action == "call" and opponent_action_probabilities.get("call", 0) > 0.55:
            if adjusted_probs.get("call", 0) > 0.3 or adjusted_probs.get("raise_small", 0) > 0.2:
                if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.05
                if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05

        # Regla 4: Inferencia de Farol de Scikit-Learn - si la probabilidad de farol del rival es alta (>0.60), aumentar call/bluff-catch
        if current_bet > 0 and bluff_prob > 0.60:
            if "call" in adjusted_probs: adjusted_probs["call"] += 0.12
            if "fold" in adjusted_probs: adjusted_probs["fold"] = max(0.0, adjusted_probs["fold"] - 0.12)

        # Normalizar probabilidades para que sumen 1.0
        total_prob = sum(adjusted_probs.values())
        if total_prob > 0:
            for action, prob in adjusted_probs.items():
                adjusted_probs[action] = prob / total_prob
        else:
            return {"fold": 0.0 if current_bet == 0 else 0.5, "call": 1.0 if current_bet == 0 else 0.5}

        return adjusted_probs

    def _select_action(self, probabilities):
        actions = list(probabilities.keys())
        probs = list(probabilities.values())
        probs = np.array(probs) / np.sum(probs)
        chosen_action = np.random.choice(actions, p=probs)
        return chosen_action
