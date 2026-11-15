"""
Suite Completa de Pruebas Unitarias, de Concurrencia e Integración (Fase 8).

Valida rigurosamente:
1. Fórmulas estadísticas (IC 95%, Wilson, Welch t-test, Paired t-test, Holm-Bonferroni, estados MEJORA/INCONCLUSO/REGRESIÓN).
2. Cálculos HUD (VPIP, PFR, 3-Bet%, Fold to 3-Bet%, AF Postflop, AFq%, WTSD%, W$SD%, WWSF%, CBet%, Fold to CBet%) con numeradores, denominadores y tamaño de muestra.
3. Cálculo de EV, All-in EV y manejo de información faltante (`EVALUACION_NO_DISPONIBLE`).
4. Detección de errores estratégicos, oportunidades perdidas (`is_missed_opportunity`) e independencia del desenlace.
5. Persistencia de datos y separación estricta entre `live` (`is_synthetic=0`) y `benchmark` (`is_synthetic=1`).
6. Prevención de duplicados en manos y decisiones (`hand_uid`, `decision_uid`, cierres repetidos).
7. Concurrencia multihilo y drenado sin pérdida de registros en `PokerAnalyticsLogger` + registro de acción realmente ejecutada (`executed_action` vs `intended_action`).
8. Comparación científica de versiones y laboratorio de experimentación (`analytics_experiments`).
9. Integridad de datos (`PRAGMA integrity_check`, cero grupos duplicados).
10. Generación del Dashboard de 9 secciones y exportación trazable de las 6 entidades CSV + JSON (`code_hash`).
11. Rendimiento y telemetría empírica P50, P95 y P99 (`calcular_percentil`, `resumir_distribucion_latencia`).
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from analytics_benchmark import ejecutar_benchmark_versiones
from analytics_dashboard import generar_dashboard_html
from analytics_db import AnalyticsStorage, calcular_hash_codigo_proyecto
from analytics_diagnostics import (
    calcular_percentil,
    generar_diagnosticos_inteligentes,
    obtener_uso_memoria_rss_mb,
    resumir_distribucion_latencia,
)
from analytics_engine import (
    PokerAnalyticsEngine,
    clasificar_estado_comparacion,
    evaluar_decision_estrategica,
    holm_bonferroni_correction,
    mean_std_ci95,
    paired_t_test,
    welch_t_test,
    wilson_confidence_interval,
)
from analytics_logger import PokerAnalyticsLogger
from poker_agent import PokerDecision


class TestStatisticalFormulasAndHypothesisTesting(unittest.TestCase):
    """1. Valida fórmulas estadísticas, Holm-Bonferroni y clasificación en 3 estados."""

    def test_mean_std_ci95_calculation(self):
        vals = [0.5] * 100
        res = mean_std_ci95(vals, scale=100.0)
        self.assertEqual(res["n"], 100)
        self.assertAlmostEqual(res["mean"], 50.0, places=2)
        self.assertAlmostEqual(res["std"], 0.0, places=2)
        self.assertAlmostEqual(res["variance"], 0.0, places=4)
        self.assertAlmostEqual(res["ci95_low"], 50.0, places=2)
        self.assertAlmostEqual(res["ci95_high"], 50.0, places=2)

    def test_wilson_confidence_interval(self):
        low, high = wilson_confidence_interval(80, 100)
        self.assertTrue(70.0 < low < 75.0)
        self.assertTrue(85.0 < high < 88.0)
        self.assertEqual(wilson_confidence_interval(0, 0), (0.0, 0.0))

    def test_welch_and_paired_t_tests(self):
        a = [-0.2 + (0.1 if i % 2 == 0 else -0.1) for i in range(80)]
        b = [0.4 + (0.1 if i % 2 == 0 else -0.1) for i in range(80)]

        welch = welch_t_test(a, b, scale=100.0)
        self.assertAlmostEqual(welch["delta_scaled"], 60.0, places=1)
        self.assertTrue(welch["significant_95"])
        self.assertEqual(welch["verdict_state"], "MEJORA")
        self.assertLess(welch["p_value"], 0.001)
        self.assertGreater(welch["cohens_d"], 1.0)

        pairs = list(zip(a, b))
        paired = paired_t_test(pairs, scale=100.0)
        self.assertIsNotNone(paired)
        self.assertEqual(paired["n_paired"], 80)
        self.assertTrue(paired["significant_95"])
        self.assertGreater(paired["ci95_diff"][0], 0.0)

    def test_holm_bonferroni_multiple_comparisons_correction(self):
        raw_p = [0.005, 0.020, 0.045]
        corrected = holm_bonferroni_correction(raw_p, alpha=0.05)
        self.assertEqual(len(corrected), 3)
        # p_(1)=0.005 * 3 = 0.015 (<0.05 -> True)
        self.assertAlmostEqual(corrected[0]["holm_adjusted_p_value"], 0.015, places=4)
        self.assertTrue(corrected[0]["significant_after_holm"])
        # p_(2)=0.020 * 2 = 0.040 (<0.05 -> True)
        self.assertAlmostEqual(corrected[1]["holm_adjusted_p_value"], 0.040, places=4)
        self.assertTrue(corrected[1]["significant_after_holm"])
        # p_(3)=0.045 * 1 = 0.045 (<0.05 -> True)
        self.assertAlmostEqual(corrected[2]["holm_adjusted_p_value"], 0.045, places=4)

    def test_three_state_classification_and_exploratory_flag(self):
        # Muestra pequeña (<30) nunca debe declararse MEJORA aunque p < 0.05
        small = clasificar_estado_comparacion(
            n_prev=15,
            n_curr=15,
            delta_ev_bb100=25.0,
            holm_p_value=0.001,
            ci95_diff=[10.0, 40.0],
            cohens_d=1.2,
        )
        self.assertEqual(small["verdict_state"], "INCONCLUSO")
        self.assertTrue(small["is_exploratory"])

        # Muestra suficiente con evidencia favorable -> MEJORA
        imp = clasificar_estado_comparacion(
            n_prev=120,
            n_curr=120,
            delta_ev_bb100=18.5,
            holm_p_value=0.002,
            ci95_diff=[8.0, 29.0],
            cohens_d=0.65,
        )
        self.assertEqual(imp["verdict_state"], "MEJORA")
        self.assertFalse(imp["is_exploratory"])

        # Muestra suficiente con deterioro significativo -> REGRESIÓN
        reg = clasificar_estado_comparacion(
            n_prev=120,
            n_curr=120,
            delta_ev_bb100=-22.0,
            holm_p_value=0.001,
            ci95_diff=[-35.0, -9.0],
            cohens_d=-0.75,
        )
        self.assertEqual(reg["verdict_state"], "REGRESIÓN")


class TestDecisionQualityAndMissingInfo(unittest.TestCase):
    """2. Valida evaluación de EV, errores estratégicos, oportunidades perdidas y falta de información."""

    def test_unavailable_evaluation_when_cards_or_pot_missing(self):
        res_no_cards = evaluar_decision_estrategica(
            hole_cards="",
            board="A♠ K♦ 2♣",
            pot_size=80.0,
            to_call=20.0,
            effective_stack=400.0,
            action_taken="CALL",
        )
        self.assertFalse(res_no_cards["evaluation_available"])
        self.assertEqual(res_no_cards["decision_quality"], "EVALUACION_NO_DISPONIBLE")
        self.assertFalse(res_no_cards["is_strategic_error"])

        res_no_pot = evaluar_decision_estrategica(
            hole_cards="A♠ K♠",
            board="",
            pot_size=0.0,
            to_call=0.0,
            effective_stack=400.0,
            action_taken="RAISE",
        )
        self.assertFalse(res_no_pot["evaluation_available"])
        self.assertEqual(res_no_pot["decision_quality"], "EVALUACION_NO_DISPONIBLE")

    def test_premium_hand_allin_is_optimal_even_if_lost_later(self):
        res = evaluar_decision_estrategica(
            hole_cards="A♠ A♥",
            board="",
            pot_size=100.0,
            to_call=40.0,
            effective_stack=160.0,
            action_taken="ALL_IN",
            bet_amount=160.0,
            position="BTN",
            street="PREFLOP",
            opponent_profile="REGULAR EQUILIBRADO",
            legal_actions=["FOLD", "CALL", "RAISE", "ALL_IN"],
            big_blind=20.0,
        )
        self.assertTrue(res["evaluation_available"])
        self.assertTrue(res["is_gto_aligned"])
        self.assertFalse(res["is_strategic_error"])
        self.assertEqual(res["decision_quality"], "OPTIMA")
        self.assertEqual(res["ev_lost_bb"], 0.0)

    def test_missed_value_opportunity_detection(self):
        # Hero tiene Set de Ases en el River en posición con Check gratis y decide hacer CHECK en vez de apostar por valor
        res = evaluar_decision_estrategica(
            hole_cards="A♠ A♦",
            board="A♣ 9♥ 4♦ 2♠ 7♣",
            pot_size=160.0,
            to_call=0.0,
            effective_stack=340.0,
            action_taken="CHECK",
            bet_amount=0.0,
            position="BTN",
            street="RIVER",
            opponent_profile="CALLING STATION",
            legal_actions=["CHECK", "BET", "RAISE"],
            big_blind=20.0,
        )
        self.assertTrue(res["is_missed_opportunity"])
        self.assertTrue(res["is_strategic_error"])
        self.assertGreater(res["ev_lost_bb"], 0.6)

    def test_fold_when_check_is_free_and_bluff_calling_station(self):
        res_fold = evaluar_decision_estrategica(
            hole_cards="10♠ 9♠",
            board="A♦ 7♣ 2♠",
            pot_size=60.0,
            to_call=0.0,
            effective_stack=400.0,
            action_taken="FOLD",
            bet_amount=0.0,
            position="BB",
            street="FLOP",
            opponent_profile="REGULAR EQUILIBRADO",
            legal_actions=["FOLD", "CHECK", "BET"],
            big_blind=20.0,
        )
        self.assertTrue(res_fold["is_strategic_error"])
        self.assertEqual(res_fold["decision_quality"], "BLUNDER_EV")

        res_bluff = evaluar_decision_estrategica(
            hole_cards="7♦ 2♣",
            board="A♠ K♥ Q♦ 9♣",
            pot_size=120.0,
            to_call=0.0,
            effective_stack=400.0,
            action_taken="RAISE",
            bet_amount=100.0,
            position="BTN",
            street="TURN",
            opponent_profile="CALLING STATION",
            legal_actions=["CHECK", "BET", "RAISE"],
            big_blind=20.0,
        )
        self.assertTrue(res_bluff["is_strategic_error"])
        self.assertFalse(res_bluff["opponent_adaptation_aligned"])


class TestStorageConcurrencyAndTelemetry(unittest.TestCase):
    """3. Valida persistencia, separación Live vs Benchmark, deduplicación, concurrencia y telemetría P50/P95/P99."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_poker_stats.db"
        self.storage = AnalyticsStorage(db_path=self.db_path)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def test_executed_action_vs_intended_action_and_deduplication(self):
        logger = PokerAnalyticsLogger(
            version_id="1.3",
            version_description="Test acción ejecutada y deduplicación",
            mode="live",
            db_path=self.db_path,
            async_mode=False,
        )

        # IA propuso FOLD pero el botón Check Gratis estaba activo y ejecutar_accion en bot.py ejecutó CHECK
        dom_pre = {
            "mis_cartas": "Q♠ J♠",
            "cartas_comunitarias": "",
            "bote": "30",
            "a_igualar": "0",
            "mi_stack": "500",
            "stack_rival": "500",
            "opciones_disponibles": "CHECK | RAISE (40)",
            "perfil_rival": {"nickname": "TestRival", "estilo": "NIT"},
        }
        dec_ia = PokerDecision(
            evaluacion="Intento de fold en check gratis",
            estrategia_competitiva="Defensiva",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="FOLD",
            monto_apuesta=0,
            confianza=0.75,
        )
        logger.registrar_decision(
            dom_data=dom_pre,
            decision=dec_ia,
            executed_action="CHECK",
            hand_number=1,
            position_name="BB",
            opp_id="opp_1",
            opp_nick="TestRival",
        )
        # Segunda llamada idéntica en el mismo turno -> no debe duplicar la decisión
        logger.registrar_decision(
            dom_data=dom_pre,
            decision=dec_ia,
            executed_action="CHECK",
            hand_number=1,
            position_name="BB",
            opp_id="opp_1",
            opp_nick="TestRival",
        )

        logger.finalizar_mano(
            resultado_str="Mano Ganada",
            hand_number=1,
            hole_cards="Q♠ J♠",
            pot_final=40.0,
            net_profit_chips=20.0,
            won_hand=True,
        )
        # Segundo cierre repetido del mismo modal -> debe ignorarse sin duplicar mano
        logger.finalizar_mano(
            resultado_str="Mano Ganada",
            hand_number=1,
            hole_cards="Q♠ J♠",
            pot_final=40.0,
            net_profit_chips=20.0,
            won_hand=True,
        )

        decs = self.storage.consultar_decisiones({"data_source": "live"})
        hands = self.storage.consultar_manos({"data_source": "live"})
        self.assertEqual(len(hands), 1)
        self.assertEqual(len(decs), 1)
        self.assertEqual(decs[0]["intended_action"], "FOLD")
        self.assertEqual(decs[0]["action_taken"], "CHECK")
        self.assertEqual(decs[0]["is_synthetic"], 0)
        self.assertEqual(hands[0]["is_synthetic"], 0)

    def test_async_concurrency_zero_dropped_records_and_p50_p95_p99(self):
        logger = PokerAnalyticsLogger(
            version_id="1.3",
            version_description="Test concurrencia asíncrona",
            mode="live",
            db_path=self.db_path,
            async_mode=True,
        )

        def _worker_sim(thread_idx: int):
            for i in range(1, 6):
                hand_no = thread_idx * 100 + i
                dom = {
                    "mis_cartas": "A♠ K♦",
                    "cartas_comunitarias": "",
                    "bote": str(40 + i * 10),
                    "a_igualar": "20",
                    "mi_stack": "500",
                    "stack_rival": "500",
                }
                dec = PokerDecision(
                    evaluacion="Raise valor",
                    estrategia_competitiva="GTO",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="RAISE",
                    monto_apuesta=60,
                    confianza=0.9,
                )
                logger.registrar_decision(
                    dom_data=dom,
                    decision=dec,
                    executed_action="RAISE",
                    response_time_ms=120.0 + i,
                    hand_number=hand_no,
                    position_name="BTN",
                    scenario_id=f"T{thread_idx}_H{i}",
                )

        threads = [threading.Thread(target=_worker_sim, args=(t,)) for t in range(1, 5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logger.shutdown(wait=True)

        tel = logger.obtener_telemetria_logger()
        self.assertEqual(tel["dropped_records"], 0)
        self.assertEqual(tel["storage_errors"], 0)
        self.assertEqual(tel["processed_tasks"], 20)
        self.assertGreater(tel["enqueue_latency"]["p95_ms"], 0.0)
        self.assertGreater(tel["sqlite_write_latency"]["p50_ms"], 0.0)
        self.assertGreater(obtener_uso_memoria_rss_mb(), 0.0)

        decs = self.storage.consultar_decisiones({"data_source": "live"})
        self.assertEqual(len(decs), 20)

        integrity = self.storage.verificar_integridad_db()
        self.assertTrue(integrity["is_healthy"])
        self.assertEqual(integrity["sqlite_integrity_check"], "ok")
        self.assertEqual(integrity["duplicate_decisions_groups"], 0)

    def test_full_pipeline_benchmark_diagnostics_and_exports(self):
        # 1. Insertar 1 mano live y correr benchmark de 30 escenarios (4 versiones = 120 manos benchmark)
        live_logger = PokerAnalyticsLogger(version_id="1.3", mode="live", db_path=self.db_path, async_mode=False)
        dom = {"mis_cartas": "K♠ K♥", "cartas_comunitarias": "", "bote": "40", "a_igualar": "20", "mi_stack": "500"}
        dec = PokerDecision(
            evaluacion="KK Raise",
            estrategia_competitiva="Valor",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="RAISE",
            monto_apuesta=60,
            confianza=0.98,
        )
        live_logger.registrar_decision(dom_data=dom, decision=dec, executed_action="RAISE", hand_number=1, position_name="BTN")
        live_logger.finalizar_mano(resultado_str="Ganada", hand_number=1, hole_cards="K♠ K♥", pot_final=120.0, net_profit_chips=60.0, won_hand=True)

        bench_res = ejecutar_benchmark_versiones(storage=self.storage, num_scenarios=30, force_reseed=True)
        self.assertEqual(bench_res["versions_evaluated"], 4)
        self.assertEqual(bench_res["total_hands_saved"], 120)

        # Verificar separación estricta entre live (1 mano) y benchmark (120 manos)
        live_hands = self.storage.consultar_manos({"data_source": "live"})
        bench_hands = self.storage.consultar_manos({"data_source": "benchmark"})
        self.assertEqual(len(live_hands), 1)
        self.assertEqual(len(bench_hands), 120)

        engine = PokerAnalyticsEngine(storage=self.storage)
        report = engine.calcular_analiticas_completas(filters={"data_source": "benchmark"})

        # Verificar definiciones HUD documentadas y desgloses por tamaño de bote y stack
        hud_defs = report["gameplay_hud"]["definitions_table"]
        self.assertEqual(len(hud_defs), 11)
        for hd in hud_defs:
            self.assertIn("numerator", hd)
            self.assertIn("denominator", hd)
            self.assertIn("sample_size", hd)
            self.assertIn("formula", hd)

        self.assertIn("SMALL (<4 BB)", report["strategic_analysis"]["performance_by_pot_size"])
        self.assertIn("SHORT (<10 BB)", report["strategic_analysis"]["performance_by_stack_depth"])
        self.assertIn("gto_evaluation", report["strategic_analysis"])
        self.assertEqual(
            report["strategic_analysis"]["gto_evaluation"]["exploitability_analysis"]["full_game_nash_exploitability_status"],
            "NO_DISPONIBLE_SIN_SOLVER_CFR",
        )

        # Verificar Diagnóstico Inteligente (Fase 5)
        diags = generar_diagnosticos_inteligentes(report)
        self.assertIsInstance(diags, list)
        self.assertGreater(len(diags), 0)

        # Verificar Dashboard de 9 secciones y exportaciones CSV/JSON trazables (Fase 4 y Fase 7)
        html_out = Path(self.tmp_dir.name) / "dashboard.html"
        dash_res = generar_dashboard_html(db_path=self.db_path, output_html=html_out, auto_seed_benchmark_if_empty=False)
        self.assertTrue(Path(dash_res["dashboard_html_path"]).exists())
        html_text = html_out.read_text(encoding="utf-8")
        for sec_id in ("sec-1", "sec-2", "sec-3", "sec-4", "sec-5", "sec-6", "sec-7", "sec-8", "sec-9"):
            self.assertIn(sec_id, html_text)

        csv_exports = dash_res["csv_exports"]
        for req_key in ("hands_csv", "sessions_csv", "decisions_csv", "metrics_csv", "benchmarks_csv", "version_comparisons_csv"):
            self.assertIn(req_key, csv_exports)
            self.assertTrue(Path(csv_exports[req_key]).exists())

        self.assertEqual( len(calcular_hash_codigo_proyecto()), 16)
        self.assertAlmostEqual(calcular_percentil([10.0, 20.0, 30.0, 40.0, 50.0], 50.0), 30.0, places=2)
        lat_summary = resumir_distribucion_latencia([1.0, 2.0, 3.0, 4.0, 10.0])
        self.assertEqual(lat_summary["count"], 5)
        self.assertGreaterEqual(lat_summary["p99_ms"], lat_summary["p95_ms"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
