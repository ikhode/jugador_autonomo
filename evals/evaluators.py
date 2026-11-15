"""Evaluadores offline para la capa de IA."""
from __future__ import annotations

from typing import Any, Dict

import config
from ai_layer import AIDecisionResult
from evals.dataset import EvalCase


def eval_legality(case: EvalCase, result: AIDecisionResult) -> Dict[str, Any]:
    """(1) Legalidad estricta: acción en opciones legales y sin FOLD ante CHECK gratis."""
    act = result.decision.accion
    legal = set(case.legal_actions)
    to_call = float(case.dom_data.get("a_igualar") or 0.0)

    if act == "FOLD" and "CHECK" in legal and to_call <= 0:
        return {"pass": False, "score": 0.0, "reason": "fold_with_free_check"}
    if act in ("WAIT", "START_GAME", "REPLAY"):
        return {"pass": False, "score": 0.0, "reason": f"passive_action:{act}"}
    if legal and act not in legal:
        if act == "RAISE" and "BET" in legal:
            return {"pass": True, "score": 1.0, "reason": "raise_as_bet"}
        return {"pass": False, "score": 0.0, "reason": f"illegal:{act}"}
    return {"pass": True, "score": 1.0, "reason": "ok"}


def eval_schema(case: EvalCase, result: AIDecisionResult) -> Dict[str, Any]:
    """(2) Cumplimiento del esquema PokerDecision."""
    ok = bool(result.trace.schema_valid) or result.decision_source == "gto_fallback_0ms"
    return {
        "pass": ok,
        "score": 1.0 if result.trace.schema_valid else (0.5 if result.decision_source == "gto_fallback_0ms" else 0.0),
        "reason": "schema_valid" if result.trace.schema_valid else f"fallback:{result.trace.fallback_reason}",
    }


def eval_gto_or_ev_alignment(case: EvalCase, result: AIDecisionResult) -> Dict[str, Any]:
    """(3) Coincidencia con acción recomendada GTO o acción de mayor EV disponible."""
    act = result.decision.accion
    gto = case.expected_gto_action
    best_ev_act = None
    if case.ev_by_action:
        legal_ev = {k: v for k, v in case.ev_by_action.items() if not case.legal_actions or k in case.legal_actions}
        pool = legal_ev or case.ev_by_action
        best_ev_act = max(pool, key=lambda k: pool[k])

    matched = (act == gto) or (best_ev_act is not None and act == best_ev_act)
    if not matched and act in ("RAISE", "BET") and gto in ("RAISE", "BET"):
        matched = True
    return {
        "pass": matched,
        "score": 1.0 if matched else 0.0,
        "reason": "aligned" if matched else f"got={act},gto={gto},best_ev={best_ev_act}",
    }


def eval_eval_action_coherence(case: EvalCase, result: AIDecisionResult) -> Dict[str, Any]:
    """(4) Coherencia entre texto de evaluación y acción elegida."""
    text = (result.decision.evaluacion or "").lower()
    act = result.decision.accion
    contradictions = {
        "FOLD": ("all-in", "all_in", "subiendo por valor", "apostar fuerte"),
        "CHECK": ("all-in", "fold disciplinado"),
        "RAISE": ("fold para proteger", "retirarse"),
        "ALL_IN": ("fold para proteger", "pasar gratis"),
    }
    for bad in contradictions.get(act, ()):
        if bad in text:
            return {"pass": False, "score": 0.0, "reason": f"contradiction:{bad}"}
    return {"pass": True, "score": 1.0, "reason": "coherent"}


def eval_latency(case: EvalCase, result: AIDecisionResult, budget_ms: float = 0.0) -> Dict[str, Any]:
    """(5) Latencia total dentro del presupuesto."""
    limit = budget_ms or float(getattr(config, "AI_DECISION_TIMEOUT_SEC", 4.5)) * 1000.0
    lat = float(result.trace.total_latency_ms)
    ok = lat <= limit
    return {"pass": ok, "score": 1.0 if ok else 0.0, "latency_ms": lat, "budget_ms": limit}


def evaluate_case(case: EvalCase, result: AIDecisionResult) -> Dict[str, Any]:
    """Ejecuta los 5 evaluadores sobre un caso."""
    return {
        "case_id": case.case_id,
        "category": case.category,
        "decision_source": result.decision_source,
        "action": result.decision.accion,
        "legality": eval_legality(case, result),
        "schema": eval_schema(case, result),
        "gto_ev_alignment": eval_gto_or_ev_alignment(case, result),
        "coherence": eval_eval_action_coherence(case, result),
        "latency": eval_latency(case, result),
    }
