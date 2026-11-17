# opponent_model.py
"""
Módulo de Modelado de Oponentes y Clasificación Explotativa con Scikit-Learn.
Soporta RandomForest, GradientBoosting, LogisticRegression, SVM (SVC) y XGBoost,
además de detección binaria de farol y entrenamiento automático desde SQLite.
"""
from pathlib import Path
import os
import sys
import numpy as np

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except Exception:
    XGBOOST_AVAILABLE = False

# Ensure aria_poker is in path for equity/engine integration
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'aria_poker'))
try:
    from engine import parse_cards
    from equity import preflop_pct, made_pct
    ARIA_AVAILABLE = True
except Exception:
    ARIA_AVAILABLE = False


class OpponentModel:
    def __init__(self, model_type="random_forest"):
        self.model_type = str(model_type or "random_forest").lower()
        self.scaler = StandardScaler()
        self.is_scaled = False

        if self.model_type == "random_forest":
            self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        elif self.model_type == "gradient_boosting":
            self.model = GradientBoostingClassifier(n_estimators=100, random_state=42)
        elif self.model_type == "logistic_regression":
            self.model = LogisticRegression(max_iter=1000, random_state=42)
        elif self.model_type in ("svm", "svc"):
            from sklearn.calibration import CalibratedClassifierCV
            self.model = CalibratedClassifierCV(estimator=SVC(kernel="rbf", random_state=42))
        elif self.model_type == "xgboost":
            if XGBOOST_AVAILABLE:
                self.model = xgb.XGBClassifier(
                    n_estimators=100,
                    eval_metric='logloss',
                    random_state=42
                )
            else:
                print("⚠️ XGBoost no está instalado; degradando a RandomForestClassifier.")
                self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        else:
            raise ValueError(
                f"model_type inválido '{model_type}'. Opciones: 'random_forest', "
                "'gradient_boosting', 'logistic_regression', 'svm', 'xgboost'"
            )

        # Clasificador específico binario para detección de farol (Logistic Regression / SVM)
        self.bluff_classifier = LogisticRegression(max_iter=1000, random_state=42)
        self.bluff_fitted = False
        self.feature_names = [
            "pot_size", "current_bet", "pot_odds", "board_cards_count", "hand_strength",
            "vpip", "pfr", "folds", "calls", "raises"
        ]

        # Verificar si existen pesos preentrenados en models/ y cargarlos automáticamente
        models_dir = Path(__file__).resolve().parent / "models"
        pretrained_file = models_dir / f"opponent_model_{self.model_type}.joblib"
        if pretrained_file.is_file():
            try:
                self.load_model(pretrained_file)
            except Exception:
                pass

    def _engineer_features(self, game_state, historical_actions, opponent_profile):
        """
        Engineered features incorporating raw game state, opponent profile,
        historical actions, and advanced Aria poker equity / hand strength metrics.
        """
        pot_size = float(game_state.get("pot_size", 0.0))
        current_bet = float(game_state.get("current_bet", 0.0))
        board_cards_count = int(game_state.get("board_cards_count", 0))

        # Advanced feature: Pot Odds
        pot_odds = current_bet / (pot_size + current_bet + 1e-6)

        # Advanced feature: Aria Equity / Hand Strength Percentile
        hand_strength = 0.5  # Default fallback
        if ARIA_AVAILABLE:
            try:
                mis_cartas_str = game_state.get("mis_cartas", "")
                comunitarias_str = game_state.get("cartas_comunitarias", "")
                if mis_cartas_str and len(mis_cartas_str.split()) == 2:
                    hole = parse_cards(mis_cartas_str)
                    if comunitarias_str:
                        board = parse_cards(comunitarias_str)
                        import random
                        rng = random.Random(42)
                        hand_strength = made_pct(hole, board, rng)
                    else:
                        hand_strength = preflop_pct(hole)
            except Exception:
                pass

        vpip_val = float(opponent_profile.get("vpip_pct", opponent_profile.get("VPIP", 25.0)))
        pfr_val = float(opponent_profile.get("pfr_pct", opponent_profile.get("PFR", 15.0)))
        if vpip_val > 1.0: vpip_val /= 100.0
        if pfr_val > 1.0: pfr_val /= 100.0

        hist_dict = historical_actions if isinstance(historical_actions, dict) else {}

        features = [
            pot_size,
            current_bet,
            pot_odds,
            board_cards_count,
            hand_strength,
            vpip_val,
            pfr_val,
            float(hist_dict.get("folds", 0)),
            float(hist_dict.get("calls", 0)),
            float(hist_dict.get("raises", 0))
        ]

        feat_arr = np.array(features).reshape(1, -1)
        if self.is_scaled:
            try:
                feat_arr = self.scaler.transform(feat_arr)
            except Exception:
                pass
        return feat_arr

    def train(self, X_train, y_train, feature_names=None):
        X_arr = np.array(X_train, dtype=np.float32)
        y_arr = np.array(y_train)

        print(f"Training opponent model ({self.model_type}) with {len(X_arr)} samples...")
        self.scaler.fit(X_arr)
        self.is_scaled = True
        X_scaled = self.scaler.transform(X_arr)

        if self.model_type in ("svm", "svc"):
            from sklearn.calibration import CalibratedClassifierCV
            counts = np.bincount(y_arr) if len(y_arr) > 0 else []
            min_class_samples = int(np.min(counts)) if len(counts) > 0 else 0
            if len(X_arr) >= 10 and min_class_samples >= 2:
                cv_val = min(3, min_class_samples)
                self.model = CalibratedClassifierCV(estimator=SVC(kernel="rbf", random_state=42), cv=cv_val)
            else:
                self.model = SVC(kernel="rbf", random_state=42)

        self.model.fit(X_scaled, y_arr)

        # Entrenar clasificador de farol binario (1 = Farol, 0 = Mano de Valor)
        y_bluff = (y_arr == 2).astype(int)  # raises/bets agresivas como candidatos a farol
        if len(np.unique(y_bluff)) > 1:
            self.bluff_classifier.fit(X_scaled, y_bluff)
            self.bluff_fitted = True

        if feature_names:
            self.feature_names = feature_names

    def predict_opponent_action(self, game_state, historical_actions, opponent_profile):
        features = self._engineer_features(game_state, historical_actions, opponent_profile)

        action_labels = ["fold", "call", "raise"]

        if hasattr(self.model, 'predict_proba'):
            try:
                probabilities = self.model.predict_proba(features)[0]
                classes = getattr(self.model, 'classes_', np.array([0, 1, 2]))
                action_probs = {}
                for idx, cls_val in enumerate(classes):
                    if idx < len(probabilities) and int(cls_val) < len(action_labels):
                        action_probs[action_labels[int(cls_val)]] = float(probabilities[idx])
                for act in action_labels:
                    action_probs.setdefault(act, 0.0)

                best_idx = np.argmax(probabilities)
                predicted_label = action_labels[int(classes[best_idx])] if int(classes[best_idx]) < len(action_labels) else "call"
                return {"predicted_action": predicted_label, "probabilities": action_probs}
            except Exception:
                pass

        predicted_action = self.model.predict(features)[0]
        label = action_labels[int(predicted_action)] if int(predicted_action) < len(action_labels) else "call"
        return {"predicted_action": label, "probabilities": {label: 1.0}}

    def predict_bluff_probability(self, game_state, historical_actions, opponent_profile) -> float:
        """Predice la probabilidad de que la agresión del oponente sea un farol (Regresión Logística / SVM)."""
        features = self._engineer_features(game_state, historical_actions, opponent_profile)
        if self.bluff_fitted and hasattr(self.bluff_classifier, 'predict_proba'):
            try:
                probs = self.bluff_classifier.predict_proba(features)[0]
                return round(float(probs[1]), 4)
            except Exception:
                pass

        # Regla por defecto basada en factor de agresión o pot odds si el modelo aún no está ajustado
        current_bet = float(game_state.get("current_bet", 0.0))
        pot_size = float(game_state.get("pot_size", 0.0))
        pot_odds = current_bet / (pot_size + current_bet + 1e-6)
        return round(min(0.85, max(0.10, pot_odds * 0.9)), 4)

    def train_from_database(self, db_path: str = None) -> dict:
        """Extrae características e interacciones registradas en SQLite (poker_stats.db) y entrena el modelo."""
        target_db = Path(db_path or (Path(__file__).resolve().parent / "logs" / "poker_stats.db"))
        if not target_db.is_file():
            # Fallback prior si la BD aún no existe
            X_dummy = np.array([
                [50, 10, 0.166, 0, 0.5, 0.25, 0.15, 1, 2, 0],
                [200, 50, 0.2, 3, 0.7, 0.25, 0.15, 0, 1, 3],
                [100, 20, 0.16, 4, 0.4, 0.40, 0.25, 2, 1, 4],
                [30, 5, 0.14, 0, 0.3, 0.10, 0.05, 4, 0, 0]
            ], dtype=np.float32)
            y_dummy = np.array([0, 1, 2, 0])  # fold=0, call=1, raise=2
            self.train(X_dummy, y_dummy)
            return {"samples_trained": len(X_dummy), "model_type": self.model_type, "source": "prior_fallback"}

        import sqlite3
        try:
            with sqlite3.connect(str(target_db)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT player_id, manos_jugadas, preflop_calls, preflop_raises, "
                    "folds_ante_raise, postflop_bets, postflop_calls, postflop_checks, "
                    "three_bets, fold_to_3bet, cbet_count, fold_to_cbet, check_raises "
                    "FROM oponentes WHERE manos_jugadas > 0"
                )
                rows = cur.fetchall()

                # También consultar decisiones históricas registradas si existen
                decisions_rows = []
                try:
                    cur.execute(
                        "SELECT pot_size, to_call, effective_stack, street, position, "
                        "action_taken, estimated_equity, is_strategic_error "
                        "FROM analytics_decisions LIMIT 1000"
                    )
                    decisions_rows = cur.fetchall()
                except Exception:
                    pass

            if not rows and not decisions_rows:
                return self.train_from_database("non_existent.db")

            X_list = []
            y_list = []

            for r in rows:
                manos = max(1, r[1])
                calls_pre, raises_pre, folds_pre = r[2] or 0, r[3] or 0, r[4] or 0
                bets_post, calls_post, checks_post = r[5] or 0, r[6] or 0, r[7] or 0
                vpip = (calls_pre + raises_pre) / manos
                pfr = raises_pre / manos

                # Generar muestras representativas por jugador registradas en BD
                X_list.append([60.0, 20.0, 0.25, 3, 0.5, vpip, pfr, float(folds_pre), float(calls_pre + calls_post), float(raises_pre + bets_post)])
                y_list.append(1 if calls_post >= bets_post else 2)

                X_list.append([40.0, 0.0, 0.0, 0, 0.3, vpip, pfr, float(folds_pre), float(calls_pre), float(raises_pre)])
                y_list.append(0 if folds_pre > calls_pre else 1)

            for d in decisions_rows:
                pot, to_call, eff_stack, street, pos, act, eq, is_err = d
                act_norm = str(act or "CHECK").upper()
                label = 0 if "FOLD" in act_norm else (1 if "CALL" in act_norm or "CHECK" in act_norm else 2)
                pot_val = float(pot or 50.0)
                call_val = float(to_call or 0.0)
                pot_odds = call_val / (pot_val + call_val + 1e-6)
                eq_val = float(eq or 0.5)

                X_list.append([pot_val, call_val, pot_odds, 3 if street != "PREFLOP" else 0, eq_val, 0.25, 0.15, 1.0, 1.0, 1.0])
                y_list.append(label)

            MIN_SAMPLES_THRESHOLD = 50
            if len(X_list) < MIN_SAMPLES_THRESHOLD:
                print(f"⚠️ Muestras en BD ({len(X_list)}) por debajo del umbral recomendado ({MIN_SAMPLES_THRESHOLD}). Agregando muestras de refuerzo GTO para garantizar estabilidad estadística.")
                baseline_samples = [
                    ([50, 10, 0.166, 0, 0.5, 0.25, 0.15, 1, 2, 0], 0),
                    ([200, 50, 0.2, 3, 0.7, 0.25, 0.15, 0, 1, 3], 1),
                    ([100, 20, 0.16, 4, 0.4, 0.40, 0.25, 2, 1, 4], 2),
                    ([30, 5, 0.14, 0, 0.3, 0.10, 0.05, 4, 0, 0], 0),
                    ([150, 30, 0.16, 3, 0.6, 0.30, 0.20, 1, 1, 2], 1),
                    ([80, 20, 0.20, 1, 0.45, 0.22, 0.12, 2, 0, 1], 2),
                ]
                while len(X_list) < MIN_SAMPLES_THRESHOLD:
                    for bx, by in baseline_samples:
                        X_list.append(bx)
                        y_list.append(by)
                        if len(X_list) >= MIN_SAMPLES_THRESHOLD:
                            break

            self.train(X_list, y_list)
            try:
                models_dir = Path(__file__).resolve().parent / "models"
                models_dir.mkdir(parents=True, exist_ok=True)
                save_path = models_dir / f"opponent_model_{self.model_type}.joblib"
                self.save_model(save_path)
            except Exception as se:
                print(f"⚠️ Warning guardando modelo entrenado: {se}")

            return {"samples_trained": len(X_list), "model_type": self.model_type, "source": str(target_db)}
        except Exception as e:
            print(f"⚠️ Error cargando muestras desde {target_db}: {e}")
            return self.train_from_database("non_existent.db")

    def save_model(self, path):
        import joblib
        joblib.dump({"model": self.model, "scaler": self.scaler, "bluff": self.bluff_classifier}, path)
        print(f"Opponent model ({self.model_type}) saved to {path}")

    def load_model(self, path):
        import joblib
        data = joblib.load(path)
        if isinstance(data, dict):
            self.model = data.get("model", self.model)
            self.scaler = data.get("scaler", self.scaler)
            self.bluff_classifier = data.get("bluff", self.bluff_classifier)
            self.is_scaled = True
            self.bluff_fitted = True
        else:
            self.model = data
        print(f"Opponent model loaded from {path}")


import time

class RuntimeModelManager:
    """
    Gestor dinámico de modelos en runtime:
    - Reentrena periódicamente los 5 modelos de oponentes (RandomForest, GradientBoosting, LogisticRegression, SVM, XGBoost) desde SQLite.
    - Evalúa el rendimiento histórico (EV acumulado) de cada variante de modelo.
    - Selecciona y activa automáticamente en runtime el modelo con mejor conversión de ganancias.
    """
    def __init__(self, db_path=None):
        self.db_path = db_path
        self.available_types = ["random_forest", "gradient_boosting", "logistic_regression", "svm", "xgboost"]
        self.performance_stats = {m: {"decisions": 0, "ev_sum": 0.0, "wins": 0} for m in self.available_types}
        self.active_model_type = "random_forest"
        self.active_model = OpponentModel(model_type=self.active_model_type)
        self.last_retrain_time = time.time()

    def record_decision_result(self, model_type: str, ev_bb: float, won: bool):
        m = str(model_type or "").lower()
        if m in self.performance_stats:
            self.performance_stats[m]["decisions"] += 1
            self.performance_stats[m]["ev_sum"] += float(ev_bb)
            if won:
                self.performance_stats[m]["wins"] += 1

    def evaluate_and_switch_best_model(self) -> str:
        """Selecciona el modelo con mejor EV promedio por decisión."""
        best_type = self.active_model_type
        best_score = -float('inf')
        for m, stats in self.performance_stats.items():
            if stats["decisions"] >= 3:
                avg_ev = stats["ev_sum"] / stats["decisions"]
                if avg_ev > best_score:
                    best_score = avg_ev
                    best_type = m

        if best_type != self.active_model_type:
            print(f"🔄 [RUNTIME MODEL SWITCH] Cambiando modelo activo de '{self.active_model_type}' a '{best_type}' (Mejor EV promedio: {best_score:+.2f} BB)")
            self.active_model_type = best_type
            self.active_model = OpponentModel(model_type=best_type)
        return self.active_model_type

    def periodic_runtime_update(self):
        """Reentrena todos los modelos en background con los datos más recientes de SQLite."""
        now = time.time()
        if now - self.last_retrain_time > 180:
            self.last_retrain_time = now
            try:
                print("🔄 [BACKGROUND RUNTIME TRAINING] Actualizando modelos con datos en vivo de SQLite...")
                for m_type in self.available_types:
                    tmp_model = OpponentModel(model_type=m_type)
                    tmp_model.train_from_database(self.db_path)
                self.evaluate_and_switch_best_model()
            except Exception as e:
                print(f"⚠️ Error en reentrenamiento runtime: {e}")

