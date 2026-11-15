"""Pruebas de la capa de IA, resiliencia, degradación, latencia, trazabilidad BD y evals."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai_layer import AILayer, ModelPolicy, PokerDecision
from analytics_db import AnalyticsStorage
from analytics_logger import PokerAnalyticsLogger
import config
from evals.run import compare_with_baseline, run_evaluation
from poker_agent import PokerAgent


def _sample_dom(opciones: str = "FOLD | CHECK | RAISE", a_igualar: float = 0.0) -> dict:
    return {
        "es_mi_turno": True,
        "mis_cartas": ["A♠", "K♦"],
        "cartas_comunitarias": ["Q♣", "7♦", "2♠"],
        "bote": 80,
        "a_igualar": a_igualar,
        "mi_stack": 420,
        "stack_rival": 420,
        "opciones_disponibles": opciones,
        "gto_math": {
            "bote_bb": 4.0,
            "a_igualar_bb": a_igualar / 20.0,
            "stack_efectivo_bb": 21.0,
            "pot_odds_pct": 0.0,
        },
        "gto_preflop": {"posicion": "BTN", "tier": "S", "score": 92, "mano_code": "AKo"},
    }


def _valid_json(accion: str = "CHECK", monto: int = 0) -> str:
    return PokerDecision(
        evaluacion=f"Acción {accion} sólida",
        estrategia_competitiva="GTO",
        estado_pantalla="EN_MESA_JUEGO",
        es_mi_turno=True,
        accion=accion,  # type: ignore[arg-type]
        monto_apuesta=monto,
        confianza=0.9,
    ).model_dump_json()


def test_invalid_json_retries_then_falls_back_deterministically():
    calls = {"n": 0}

    def bad_json(_m, _c, _t):
        calls["n"] += 1
        return "{not-valid-json", 10, 5

    policy = ModelPolicy(models=["m1"])
    layer = AILayer(policy=policy, model_callable=bad_json, use_genkit=False)
    res = layer.decidir(dom_data=_sample_dom("CHECK | BET", 0.0))
    assert calls["n"] == 1 + config.AI_MAX_SCHEMA_RETRIES
    assert res.decision_source == "gto_fallback_0ms"
    assert res.decision.accion == "CHECK"
    assert "schema_error" in res.trace.fallback_reason
    assert res.trace.retry_count == config.AI_MAX_SCHEMA_RETRIES


def test_timeout_triggers_deterministic_fallback():
    def timeout_fn(_m, _c, _t):
        raise TimeoutError("timed out")

    policy = ModelPolicy(models=["m1"])
    layer = AILayer(policy=policy, model_callable=timeout_fn, use_genkit=False)
    res = layer.decidir(dom_data=_sample_dom("FOLD | CALL", 40.0))
    assert res.decision_source == "gto_fallback_0ms"
    assert res.decision.accion in ("FOLD", "CALL")
    assert "TIMEOUT" in res.trace.fallback_reason


def test_429_503_retry_with_backoff_then_success():
    attempts = {"n": 0}
    sleeps = []

    def flaky(_m, _c, _t):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return _valid_json("CHECK"), 50, 20

    policy = ModelPolicy(models=["m1"])
    layer = AILayer(policy=policy, model_callable=flaky, use_genkit=False, sleep_fn=sleeps.append)
    res = layer.decidir(dom_data=_sample_dom("CHECK | BET", 0.0))
    assert res.decision.accion == "CHECK"
    assert res.trace.schema_valid is True
    assert len(sleeps) == 1
    assert res.trace.retry_count == 1


def test_404_and_daily_quota_rotate_models_and_open_cooldown():
    seen = []

    def multi_err(model, _c, _t):
        seen.append(model)
        if model == "m_404":
            raise RuntimeError("404 NOT_FOUND")
        if model == "m_daily":
            raise RuntimeError("429 quota exceeded PerDay")
        return _valid_json("CALL"), 40, 15

    policy = ModelPolicy(models=["m_404", "m_daily", "m_ok"])
    layer = AILayer(policy=policy, model_callable=multi_err, use_genkit=False, sleep_fn=lambda _: None)
    res = layer.decidir(dom_data=_sample_dom("FOLD | CALL", 20.0))
    assert seen == ["m_404", "m_daily", "m_ok"]
    assert res.decision.accion == "CALL"
    assert res.trace.model_used == "m_ok"
    assert policy.available_models() == ["m_ok"]


def test_empty_response_and_illegal_actions_never_hang_or_fold_free_check():
    # 1. Empty response -> fallback
    layer_empty = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: ("", 0, 0),
        use_genkit=False,
    )
    res_empty = layer_empty.decidir(dom_data=_sample_dom("CHECK | BET", 0.0))
    assert res_empty.decision.accion == "CHECK"
    assert res_empty.trace.fallback_reason == "empty_response"

    # 2. FOLD when free CHECK is available -> auto-converted to CHECK
    layer_fold = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: (_valid_json("FOLD"), 20, 10),
        use_genkit=False,
    )
    res_fold = layer_fold.decidir(dom_data=_sample_dom("FOLD | CHECK | BET", 0.0))
    assert res_fold.decision.accion == "CHECK"
    assert "[Auto-Check Gratis]" in res_fold.decision.evaluacion

    # 3. Passive WAIT/START_GAME during active turn -> rejected to deterministic fallback
    layer_wait = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: (_valid_json("START_GAME"), 20, 10),
        use_genkit=False,
    )
    res_wait = layer_wait.decidir(dom_data=_sample_dom("FOLD | CALL", 20.0))
    assert res_wait.decision_source == "gto_fallback_0ms"
    assert res_wait.decision.accion in ("FOLD", "CALL")


def test_circuit_breaker_opens_after_threshold():
    policy = ModelPolicy(models=["m1"], failure_threshold=2, open_sec=30.0)
    layer = AILayer(
        policy=policy,
        model_callable=lambda _m, _c, _t: (_ for _ in ()).throw(RuntimeError("boom")),
        use_genkit=False,
    )
    layer.decidir(dom_data=_sample_dom())
    assert policy.circuit_snapshot()["m1"]["state"] == "HALF_OPEN"
    layer.decidir(dom_data=_sample_dom())
    assert policy.circuit_snapshot()["m1"]["state"] == "OPEN"


def test_degradation_with_and_without_genkit_matches():
    dom = _sample_dom("CHECK | BET", 0.0)
    l_off = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: (_valid_json("CHECK"), 10, 5),
        use_genkit=False,
    )
    l_on = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: (_valid_json("CHECK"), 10, 5),
        use_genkit=True,
    )
    r_off = l_off.decidir(dom_data=dom, decision_uid="d_cmp")
    r_on = l_on.decidir(dom_data=dom, decision_uid="d_cmp")
    assert r_off.decision.accion == r_on.decision.accion
    assert r_off.trace.prompt_hash == r_on.trace.prompt_hash


def test_latency_overhead_within_budget():
    layer = AILayer(
        policy=ModelPolicy(models=["m1"]),
        model_callable=lambda _m, _c, _t: (_valid_json("CHECK"), 10, 5),
        use_genkit=True,
    )
    t0 = time.perf_counter()
    res = layer.decidir(dom_data=_sample_dom("CHECK | BET", 0.0))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < float(config.AI_LATENCY_OVERHEAD_BUDGET_MS)
    assert res.trace.total_latency_ms < float(config.AI_LATENCY_OVERHEAD_BUDGET_MS)


def test_db_traceability_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "trace_test.db"
        AnalyticsStorage(db_path=db_path)
        logger = PokerAnalyticsLogger(db_path=db_path, async_mode=False)

        layer = AILayer(
            policy=ModelPolicy(models=["gemini-2.5-flash"]),
            model_callable=lambda _m, _c, _t: (_valid_json("CHECK"), 80, 25),
            use_genkit=False,
        )
        agent = PokerAgent(api_key="dummy-test-key", ai_layer=layer)
        dom = _sample_dom("CHECK | BET", 0.0)
        dec = agent.analizar_pantalla(dom_data=dom, decision_uid="dec_trace_001", hand_uid="hand_trace_001")
        assert agent.last_trace is not None

        logger.registrar_decision(
            dom_data=dom,
            decision=dec,
            decision_source="google_genai",
            response_time_ms=agent.last_trace.total_latency_ms,
            hand_number=1,
            position_name="BTN",
            ai_trace=agent.last_trace,
            decision_uid="dec_trace_001",
        )
        logger.shutdown()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM analytics_decisions WHERE decision_uid='dec_trace_001'").fetchone())
        mig = conn.execute("SELECT version FROM schema_migrations WHERE version='v2_ai_traceability'").fetchone()
        conn.close()

        assert mig is not None
        assert row["prompt_version"] == "v1"
        assert len(row["prompt_hash"]) == 12
        assert row["model_used"] == "gemini-2.5-flash"
        assert json.loads(row["trace_json"])["decision_uid"] == "dec_trace_001"


def test_offline_eval_reproducible_and_passes_gate():
    r1 = run_evaluation(prompt_version="v1", model_mode="mock", seed=20261114, max_per_category=3)
    r2 = run_evaluation(prompt_version="v1", model_mode="mock", seed=20261114, max_per_category=3)
    assert r1["counts_by_category"] == r2["counts_by_category"]
    assert r1["metrics"]["legality_rate"] == 1.0
    assert r1["metrics"]["coherence_rate"] == 1.0
    ok, _ = compare_with_baseline(r1, r2)
    assert ok is True
