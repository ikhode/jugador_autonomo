"""CLI de evaluación offline reproducible: python -m evals.run --prompt v1"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from ai_layer import AILayer, PokerDecision
from evals.dataset import EvalCase, load_eval_dataset
from evals.evaluators import evaluate_case


def _mock_model_for_case(case: EvalCase) -> Tuple[str, Optional[int], Optional[int]]:
    """Modelo simulado determinista para pruebas offline sin red."""
    payload = PokerDecision(
        evaluacion=f"Evaluación offline para {case.category} -> {case.expected_gto_action}",
        estrategia_competitiva="Alineación GTO/EV offline",
        estado_pantalla="EN_MESA_JUEGO",
        es_mi_turno=True,
        accion=case.expected_gto_action if case.expected_gto_action in ("FOLD", "CHECK", "CALL", "RAISE", "ALL_IN") else "CHECK",  # type: ignore[arg-type]
        monto_apuesta=40 if case.expected_gto_action == "RAISE" else 0,
        confianza=0.9,
    )
    return payload.model_dump_json(), 120, 45


def run_evaluation(
    prompt_version: str = "v1",
    model_mode: str = "mock",
    seed: Optional[int] = None,
    max_per_category: int = 8,
    db_path: Optional[Path] = None,
    use_genkit: bool = False,
) -> Dict[str, Any]:
    """Ejecuta el conjunto completo de evaluación y retorna métricas agregadas y por categoría."""
    cases = load_eval_dataset(db_path=db_path, seed=seed, max_per_category=max_per_category)
    cat_counts = dict(Counter(c.category for c in cases))

    results: List[Dict[str, Any]] = []
    for case in cases:
        if model_mode == "mock":
            layer = AILayer(
                model_callable=lambda _m, _c, _t, c=case: _mock_model_for_case(c),
                use_genkit=use_genkit,
                prompt_version=prompt_version,
            )
        else:
            layer = AILayer(use_genkit=use_genkit, prompt_version=prompt_version)

        res = layer.decidir(dom_data=case.dom_data, decision_uid=case.case_id, prompt_version=prompt_version)
        results.append(evaluate_case(case, res))

    n = len(results)
    summary = {
        "prompt_version": prompt_version,
        "model_mode": model_mode,
        "seed": seed if seed is not None else getattr(config, "AI_EVAL_DEFAULT_SEED", 20261114),
        "total_cases": n,
        "counts_by_category": cat_counts,
        "metrics": {
            "legality_rate": round(sum(1 for r in results if r["legality"]["pass"]) / n, 4) if n else 0.0,
            "schema_rate": round(sum(r["schema"]["score"] for r in results) / n, 4) if n else 0.0,
            "gto_ev_alignment_rate": round(sum(1 for r in results if r["gto_ev_alignment"]["pass"]) / n, 4) if n else 0.0,
            "coherence_rate": round(sum(1 for r in results if r["coherence"]["pass"]) / n, 4) if n else 0.0,
            "latency_pass_rate": round(sum(1 for r in results if r["latency"]["pass"]) / n, 4) if n else 0.0,
            "avg_latency_ms": round(sum(r["latency"]["latency_ms"] for r in results) / n, 2) if n else 0.0,
        },
        "cases": results,
    }
    return summary


def compare_with_baseline(report: Dict[str, Any], baseline: Dict[str, Any]) -> Tuple[bool, Dict[str, float]]:
    """Verifica que la nueva versión iguale o supere las métricas clave del baseline."""
    cur = report["metrics"]
    base = baseline["metrics"]
    diffs = {
        k: round(float(cur.get(k, 0.0)) - float(base.get(k, 0.0)), 4)
        for k in ("legality_rate", "schema_rate", "gto_ev_alignment_rate", "coherence_rate", "latency_pass_rate")
    }
    passed = all(v >= 0.0 for v in diffs.values()) and cur.get("legality_rate", 0.0) == 1.0
    return passed, diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluación offline reproducible de prompts/modelos")
    parser.add_argument("--prompt", default="v1", help="Versión de prompt en prompts/<version>.txt")
    parser.add_argument("--model", default="mock", choices=["mock", "gemini"], help="Backend de evaluación")
    parser.add_argument("--seed", type=int, default=None, help="Semilla fija")
    parser.add_argument("--max-per-cat", type=int, default=8, help="Máximo de casos por categoría")
    parser.add_argument("--baseline", default="", help="Ruta JSON de baseline para regresión")
    parser.add_argument("--out", default="", help="Ruta JSON para guardar el reporte")
    args = parser.parse_args()

    report = run_evaluation(
        prompt_version=args.prompt,
        model_mode=args.model,
        seed=args.seed,
        max_per_category=args.max_per_cat,
    )

    print(f"=== EVAL REPORT (prompt={report['prompt_version']}, model={report['model_mode']}, seed={report['seed']}) ===")
    print(f"Total cases: {report['total_cases']} | Per category: {report['counts_by_category']}")
    print("Metrics:", json.dumps(report["metrics"], ensure_ascii=False))

    exit_code = 0
    if args.baseline:
        base_data = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        ok, diffs = compare_with_baseline(report, base_data)
        print("Baseline diff:", json.dumps(diffs, ensure_ascii=False), "| GATE:", "PASS" if ok else "FAIL")
        if not ok:
            exit_code = 1

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
