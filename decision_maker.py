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
            opponent_profile=opponent_profile,
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

    def _adjust_probabilities(self, base_probabilities, predicted_opponent_action, opponent_action_probabilities, bluff_prob, game_state, opponent_profile=None):
        adjusted_probs = base_probabilities.copy()
        opp_prof = opponent_profile if isinstance(opponent_profile, dict) else (game_state.get("perfil_rival", {}) or {})

        # Filtrar claves no de acción como 'predicted_ev_bb'
        eval_keys = [k for k in list(adjusted_probs.keys()) if k not in ("fold", "call", "raise_small", "raise_medium", "raise_large")]
        for ek in eval_keys:
            adjusted_probs.pop(ek, None)

        current_bet = float(game_state.get("current_bet", 0.0))
        comunitarias_str = str(game_state.get("cartas_comunitarias", "") or "").strip()
        mis_cartas_str = str(game_state.get("mis_cartas", "") or "").strip()
        board_cards_count = len(comunitarias_str.split()) if comunitarias_str else 0
        posicion = str(game_state.get("posicion") or opp_prof.get("posicion") or "BTN").upper()

        # Regla 1: Nunca foldear si la opción de Check es gratis (current_bet == 0)
        if current_bet == 0:
            adjusted_probs["fold"] = 0.0

        # =============================================================
        # PRO UPGRADE 1: Sizing Dinámico por Textura de Board (Dry vs Wet)
        # =============================================================
        if board_cards_count in (3, 4, 5):
            try:
                from poker_gto_engine import analizar_textura_mesa_y_blockers
                textura_info = analizar_textura_mesa_y_blockers(mis_cartas_str, comunitarias_str)
                t_str = str(textura_info.get("textura_mesa", "")).upper()
                if "SECA" in t_str:
                    # Tablero seco: Sizing pequeño eficiente para valor y c-bets económicas
                    if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.08
                elif "CONECTADA" in t_str or "PELIGROSA" in t_str:
                    # Tablero húmedo/coordinado: Sizing grande para protección contra draws
                    if "raise_large" in adjusted_probs: adjusted_probs["raise_large"] += 0.10
            except Exception:
                pass

        # =============================================================
        # PRO UPGRADE 2: Conciencia Avanzada de Bloqueadores en River
        # =============================================================
        if board_cards_count == 5:
            try:
                from poker_gto_engine import analizar_blockers_y_nut_effect
                nut_blockers = analizar_blockers_y_nut_effect(mis_cartas_str, comunitarias_str)
                blocker_score = float(nut_blockers.get("blocker_nut_score", 0.0))
                if current_bet > 0:
                    if blocker_score >= 0.4 or nut_blockers.get("tiene_key_blocker", False):
                        # Con blockers fuertes, aumentamos call (bluff-catch) o raise
                        if "call" in adjusted_probs: adjusted_probs["call"] += 0.15
                    elif blocker_score == 0.0:
                        # Sin blockers en river frente a apuesta, suprimimos faroles y favorecemos fold o showvalue
                        if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] = max(0.0, adjusted_probs["raise_small"] - 0.10)
                        if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] = 0.0
                        if "fold" in adjusted_probs: adjusted_probs["fold"] += 0.08
                else:
                    if blocker_score >= 0.4:
                        if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.10
            except Exception:
                pass

        # =============================================================
        # PRO UPGRADE 3: Calibración Agresiva y Posicional (BTN / CO vs SB / BB)
        # =============================================================
        if posicion in ("BTN", "CO"):
            # En posición de iniciativa, amplificar agresión/robo
            if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.10
            if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05
        elif posicion in ("SB", "BB") and current_bet > 0:
            # Fuera de posición en ciegas, endurecer defensa contra raises
            if "fold" in adjusted_probs: adjusted_probs["fold"] += 0.08
            if "call" in adjusted_probs: adjusted_probs["call"] = max(0.0, adjusted_probs["call"] - 0.05)

        # =============================================================
        # PRO UPGRADE 4: Memoria Episódica RAG de Oponentes / Arquetipos
        # =============================================================
        try:
            from poker_experience_memory import PokerExperienceMemory
            rag_mem = PokerExperienceMemory()
            opp_archetype = str(opp_prof.get("estilo", "Reg"))
            board_tex_key = "SECA" if board_cards_count > 0 else "PREFLOP"
            lessons = rag_mem.buscar_errores_similares(
                hand_tier="B",
                board_texture=board_tex_key,
                opp_archetype=opp_archetype,
                calle="RIVER" if board_cards_count == 5 else ("FLOP" if board_cards_count == 3 else "PREFLOP"),
                posicion=posicion,
                limit=1
            )
            if lessons:
                # Si la memoria RAG registra errores pasados en este spot, ajustamos hacia una línea más conservadora / sólida
                if "fold" in adjusted_probs: adjusted_probs["fold"] += 0.05
                if "call" in adjusted_probs: adjusted_probs["call"] += 0.05
                if "raise_large" in adjusted_probs: adjusted_probs["raise_large"] = max(0.0, adjusted_probs["raise_large"] - 0.05)
            rag_mem.close()
        except Exception:
            pass

        # Regla 2: Farol explotativo - si el oponente foldea con frecuencia (>0.60 prob), aumentar subidas
        if predicted_opponent_action == "fold" and opponent_action_probabilities.get("fold", 0) > 0.60:
            if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.08
            if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05
            if "call" in adjusted_probs: adjusted_probs["call"] = max(0.0, adjusted_probs["call"] - 0.10)

        # Regla 3: Value bet explotativo - si el oponente paga con frecuencia (>0.55 prob), favorecer apuestas/subidas de valor
        elif predicted_opponent_action == "call" and opponent_action_probabilities.get("call", 0) > 0.55:
            if adjusted_probs.get("call", 0) > 0.3 or adjusted_probs.get("raise_small", 0) > 0.2:
                if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.05
                if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.05

        # Regla 4: Inferencia de Farol de Scikit-Learn - si la probabilidad de farol del rival es alta (>0.60), aumentar call/bluff-catch
        if current_bet > 0 and bluff_prob > 0.60:
            if "call" in adjusted_probs: adjusted_probs["call"] += 0.12
            if "fold" in adjusted_probs: adjusted_probs["fold"] = max(0.0, adjusted_probs["fold"] - 0.12)

        # -------------------------------------------------------------
        # REGLAS AVANZADAS EXPLOTATIVAS PARA JUGADORES REGULARES (REG LEAKS)
        # -------------------------------------------------------------
        manos_obs = int(opp_prof.get("manos", 0) or 0)
        if manos_obs >= 15:
            fold_to_3bet = float(opp_prof.get("fold_to_3bet_pct", 0.0))
            cbet_flop = float(opp_prof.get("cbet_flop_pct", opp_prof.get("cbet_pct", 0.0)))
            fold_to_cr = float(opp_prof.get("fold_to_check_raise_pct", 0.0))
            river_fold_barrel = float(opp_prof.get("river_fold_to_barrel_pct", 0.0))

            # Explotación Preflop: 3-Bet Light contra regulares que foldean >60% a 3-bets
            if board_cards_count == 0 and fold_to_3bet >= 60.0:
                if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] += 0.12
                if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.08

            # Explotación Flop: Check-Raise bluff contra regulares que abusan de C-Bet (>70%) y foldean a Check-Raise (>50%)
            if board_cards_count == 3 and cbet_flop >= 70.0 and (fold_to_cr >= 50.0 or fold_to_cr == 0.0):
                if current_bet > 0 and "raise_small" in adjusted_probs:
                    adjusted_probs["raise_small"] += 0.15

            # Explotación River: Triple Barrel Bluff cuando foldean de más en River (>55%)
            if board_cards_count == 5 and river_fold_barrel >= 55.0:
                try:
                    from poker_gto_engine import analizar_blockers_y_nut_effect
                    nut_blockers = analizar_blockers_y_nut_effect(mis_cartas_str, comunitarias_str)
                    if nut_blockers.get("tiene_key_blocker", False):
                        if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] += 0.12
                        if "raise_large" in adjusted_probs: adjusted_probs["raise_large"] += 0.08
                except Exception:
                    pass

        # Regla 5: Explotación de Calling Station (Evitar faroles / C-Bets inútiles contra rivales pasivos que nunca foldean)
        estilo_rival = str(opp_prof.get("estilo", "") or "").upper()
        if "CALLING STATION" in estilo_rival or "ESTACION" in estilo_rival:
            if current_bet == 0:
                if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] = max(0.0, adjusted_probs["raise_small"] * 0.3)
                if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] = max(0.0, adjusted_probs["raise_medium"] * 0.2)
            else:
                if "raise_small" in adjusted_probs: adjusted_probs["raise_small"] = max(0.0, adjusted_probs["raise_small"] * 0.1)
                if "raise_medium" in adjusted_probs: adjusted_probs["raise_medium"] = 0.0
                if "call" in adjusted_probs: adjusted_probs["call"] += 0.15

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
