import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from analytics_benchmark import ejecutar_benchmark_versiones
from analytics_dashboard import generar_dashboard_html
from analytics_db import AnalyticsStorage
from analytics_engine import (
    PokerAnalyticsEngine,
    calcular_tamano_muestra_necesario,
    clasificar_estado_comparacion,
)


class TestFase5HonestAnalytics(unittest.TestCase):
    def test_sample_size_power_calculation_and_thresholds(self):
        # Fórmula de tamaño muestral: paired requiere menos manos que two-sample
        res_unpaired = calcular_tamano_muestra_necesario(
            delta_bb100=10.0,
            std_bb_per_hand=15.0,
            alpha=0.05,
            power=0.80,
            paired=False,
        )
        res_paired = calcular_tamano_muestra_necesario(
            delta_bb100=10.0,
            std_bb_per_hand=15.0,
            alpha=0.05,
            power=0.80,
            paired=True,
        )
        self.assertGreater(res_unpaired["required_hands"], res_paired["required_hands"])
        self.assertGreater(res_paired["required_hands"], 0)

        # En vivo exige >= MIN_LIVE_HANDS_FOR_IMPROVEMENT (5000) para declarar MEJORA
        live_insufficient = clasificar_estado_comparacion(
            n_prev=4999,
            n_curr=5000,
            delta_ev_bb100=20.0,
            holm_p_value=0.001,
            ci95_diff=[10.0, 30.0],
            cohens_d=0.60,
            is_live=True,
        )
        self.assertEqual(live_insufficient["verdict_state"], "INCONCLUSO")
        self.assertEqual(live_insufficient["min_hands_threshold"], config.MIN_LIVE_HANDS_FOR_IMPROVEMENT)
        self.assertEqual(live_insufficient["n_prev"], 4999)
        self.assertEqual(live_insufficient["n_curr"], 5000)

        live_ok = clasificar_estado_comparacion(
            n_prev=5000,
            n_curr=5000,
            delta_ev_bb100=20.0,
            holm_p_value=0.001,
            ci95_diff=[10.0, 30.0],
            cohens_d=0.60,
            is_live=True,
        )
        self.assertEqual(live_ok["verdict_state"], "MEJORA")

        # Benchmark pareado exige el mínimo justificado por potencia
        bench_insufficient = clasificar_estado_comparacion(
            n_prev=80,
            n_curr=80,
            delta_ev_bb100=20.0,
            holm_p_value=0.001,
            ci95_diff=[10.0, 30.0],
            cohens_d=0.60,
            is_live=False,
            paired_power_min_n=120,
        )
        self.assertEqual(bench_insufficient["verdict_state"], "INCONCLUSO")
        self.assertEqual(bench_insufficient["min_hands_threshold"], 120)

    def test_operational_kpis_and_ci95_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_f5.db"
            store = AnalyticsStorage(db_path=db_path)
            store.guardar_mano({
                "hand_uid": "h_live_1",
                "session_id": "sess_1",
                "version_id": "1.3",
                "is_synthetic": 0,
                "data_source": "live",
                "big_blind": 20.0,
                "net_profit_chips": 40.0,
                "net_profit_bb": 2.0,
                "ev_net_bb": 1.5,
                "allin_ev_bb": 1.8,
                "vpip_flag": 1,
                "pfr_flag": 1,
                "won_hand": 1,
            })
            store.guardar_decision({
                "decision_uid": "d_live_1",
                "hand_uid": "h_live_1",
                "session_id": "sess_1",
                "version_id": "1.3",
                "is_synthetic": 0,
                "data_source": "live",
                "street": "PREFLOP",
                "hero_cards": "As Kd",
                "pot_size": 30.0,
                "big_blind": 20.0,
                "intended_action": "RAISE",
                "action_taken": "CALL",
                "gto_recommended_action": "CALL",
                "response_time_ms": 42.5,
                "error_description": "ILEGAL corregida: RAISE->CALL",
            })
            engine = PokerAnalyticsEngine(storage=store)
            res = engine.calcular_analiticas_completas()
            kpis = res["operational_kpis"]
            self.assertEqual(kpis["n_hands"], 1)
            self.assertEqual(len(kpis["winrate_ci95"]), 2)
            self.assertGreater(kpis["required_hands_for_10bb100"], 0)
            self.assertEqual(kpis["illegal_actions_corrected_count"], 1)
            self.assertEqual(kpis["illegal_actions_corrected_rate_pct"], 100.0)
            self.assertAlmostEqual(kpis["avg_decision_latency_ms"], 42.5, places=1)
            self.assertIn("dom_errors_count", kpis)
            self.assertIn("experience_memory_hits", kpis)

    def test_dashboard_distinguishes_benchmark_and_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_dash_f5.db"
            out_html = Path(tmp) / "dash.html"
            generar_dashboard_html(
                db_path=db_path,
                output_html=out_html,
                auto_seed_benchmark_if_empty=False,
            )
            html_text = out_html.read_text(encoding="utf-8")
            self.assertIn("Benchmark Sintético", html_text)
            self.assertIn("Resultado en Vivo", html_text)
            self.assertIn("Salud Operativa", html_text)

    def test_benchmark_reproducibility_same_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_a = Path(tmp) / "bench_a.db"
            db_b = Path(tmp) / "bench_b.db"
            ejecutar_benchmark_versiones(storage=AnalyticsStorage(db_path=db_a), num_scenarios=30, seed=20261114, force_reseed=True)
            ejecutar_benchmark_versiones(storage=AnalyticsStorage(db_path=db_b), num_scenarios=30, seed=20261114, force_reseed=True)

            comp_a = PokerAnalyticsEngine(storage=AnalyticsStorage(db_path=db_a)).comparar_versiones(data_source="benchmark")
            comp_b = PokerAnalyticsEngine(storage=AnalyticsStorage(db_path=db_b)).comparar_versiones(data_source="benchmark")

            vers_a = [(v["version_id"], v["bb_per_100"], v["ev_bb_per_100"], v["gto_alignment_pct"]) for v in comp_a["versions"]]
            vers_b = [(v["version_id"], v["bb_per_100"], v["ev_bb_per_100"], v["gto_alignment_pct"]) for v in comp_b["versions"]]
            self.assertEqual(vers_a, vers_b)


if __name__ == "__main__":
    unittest.main()
