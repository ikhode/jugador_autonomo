"""Constructor reproducible del dataset de evaluación desde poker_stats.db + casos críticos."""
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


CRITICAL_CATEGORIES = (
    "check_gratis",
    "push_fold_short",
    "board_pair_vs_pocket",
    "draws_proyectos",
    "facing_allin",
    "board_plays",
)


@dataclass
class EvalCase:
    """Caso individual de evaluación offline."""

    case_id: str
    category: str
    dom_data: Dict[str, Any]
    expected_gto_action: str
    ev_by_action: Dict[str, float] = field(default_factory=dict)
    legal_actions: List[str] = field(default_factory=list)


def _canonical_cases() -> List[EvalCase]:
    """Casos canónicos reproducibles para garantizar cobertura de todas las categorías críticas."""
    return [
        EvalCase(
            case_id="canon_check_gratis_1",
            category="check_gratis",
            dom_data={
                "es_mi_turno": True,
                "mis_cartas": ["9♣", "8♠"],
                "cartas_comunitarias": ["Q♣", "7♦", "Q♠"],
                "bote": 90,
                "a_igualar": 0,
                "mi_stack": 450,
                "stack_rival": 450,
                "opciones_disponibles": "CHECK | BET | FOLD",
                "gto_math": {"bote_bb": 4.5, "a_igualar_bb": 0.0, "stack_efectivo_bb": 22.5, "pot_odds_pct": 0.0},
                "gto_preflop": {"posicion": "BTN", "tier": "C", "score": 45, "mano_code": "98o"},
            },
            expected_gto_action="CHECK",
            ev_by_action={"CHECK": 25.0, "BET": 10.0, "FOLD": 0.0},
            legal_actions=["CHECK", "BET", "FOLD"],
        ),
        EvalCase(
            case_id="canon_push_fold_1",
            category="push_fold_short",
            dom_data={
                "es_mi_turno": True,
                "mis_cartas": ["A♠", "9♦"],
                "cartas_comunitarias": [],
                "bote": 30,
                "a_igualar": 20,
                "mi_stack": 160,
                "stack_rival": 300,
                "opciones_disponibles": "FOLD | CALL | RAISE | ALL_IN",
                "gto_math": {"bote_bb": 1.5, "a_igualar_bb": 1.0, "stack_efectivo_bb": 8.0, "pot_odds_pct": 25.0},
                "gto_preflop": {"posicion": "BTN", "tier": "A", "score": 78, "mano_code": "A9o"},
            },
            expected_gto_action="ALL_IN",
            ev_by_action={"ALL_IN": 18.5, "CALL": 8.0, "FOLD": 0.0},
            legal_actions=["FOLD", "CALL", "RAISE", "ALL_IN"],
        ),
        EvalCase(
            case_id="canon_board_pair_1",
            category="board_pair_vs_pocket",
            dom_data={
                "es_mi_turno": True,
                "mis_cartas": ["7♦", "7♣"],
                "cartas_comunitarias": ["K♠", "K♥", "4♣"],
                "bote": 120,
                "a_igualar": 0,
                "mi_stack": 400,
                "stack_rival": 400,
                "opciones_disponibles": "CHECK | BET",
                "gto_math": {"bote_bb": 6.0, "a_igualar_bb": 0.0, "stack_efectivo_bb": 20.0, "pot_odds_pct": 0.0},
                "gto_combinacion": {"categoria": "DOBLE PAREJA (Pocket Pair)", "es_pareja_en_mano": True, "pareja_en_mesa": True, "mano_en_mesa": False},
            },
            expected_gto_action="BET",
            ev_by_action={"BET": 42.0, "CHECK": 34.0},
            legal_actions=["CHECK", "BET"],
        ),
        EvalCase(
            case_id="canon_draws_1",
            category="draws_proyectos",
            dom_data={
                "es_mi_turno": True,
                "mis_cartas": ["A♥", "J♥"],
                "cartas_comunitarias": ["Q♥", "9♥", "2♣"],
                "bote": 140,
                "a_igualar": 40,
                "mi_stack": 420,
                "stack_rival": 420,
                "opciones_disponibles": "FOLD | CALL | RAISE",
                "gto_math": {"bote_bb": 7.0, "a_igualar_bb": 2.0, "stack_efectivo_bb": 21.0, "pot_odds_pct": 22.2},
                "gto_combinacion": {"categoria": "PROYECTO COLOR", "proyecto_color": True, "outs_totales": 9},
            },
            expected_gto_action="CALL",
            ev_by_action={"CALL": 31.0, "RAISE": 28.0, "FOLD": 0.0},
            legal_actions=["FOLD", "CALL", "RAISE"],
        ),
        EvalCase(
            case_id="canon_facing_allin_1",
            category="facing_allin",
            dom_data={
                "es_mi_turno": True,
                "mis_cartas": ["8♠", "7♦"],
                "cartas_comunitarias": ["A♣", "K♠", "8♦"],
                "bote": 500,
                "a_igualar": 380,
                "mi_stack": 380,
                "stack_rival": 0,
                "opciones_disponibles": "FOLD | ALL_IN",
                "gto_math": {"bote_bb": 25.0, "a_igualar_bb": 19.0, "stack_efectivo_bb": 19.0, "pot_odds_pct": 43.2},
            },
            expected_gto_action="FOLD",
            ev_by_action={"FOLD": 0.0, "ALL_IN": -85.0},
            legal_actions=["FOLD", "ALL_IN"],
        ),
        EvalCase(
            case_id="canon_board_plays_1",
            category="board_plays",
            dom_data={
                "es_mi_turno": True,
                "mis_cartas": ["3♣", "2♦"],
                "cartas_comunitarias": ["A♠", "A♦", "K♣", "K♥", "Q♠"],
                "bote": 180,
                "a_igualar": 0,
                "mi_stack": 350,
                "stack_rival": 350,
                "opciones_disponibles": "CHECK | BET",
                "gto_math": {"bote_bb": 9.0, "a_igualar_bb": 0.0, "stack_efectivo_bb": 17.5, "pot_odds_pct": 0.0},
                "gto_combinacion": {"categoria": "DOBLE PAREJA EN MESA", "mano_en_mesa": True, "pareja_en_mesa": True},
            },
            expected_gto_action="CHECK",
            ev_by_action={"CHECK": 90.0, "BET": 40.0},
            legal_actions=["CHECK", "BET"],
        ),
    ]


def _classify_db_row(row: Dict[str, Any]) -> str:
    legal = row.get("legal_actions") or []
    to_call = float(row.get("to_call") or 0.0)
    bb = max(1.0, float(row.get("big_blind") or 20.0))
    stack_bb = float(row.get("effective_stack") or 500.0) / bb
    board = str(row.get("board") or "").strip()

    if set(legal) == {"FOLD", "ALL_IN"} or ("ALL_IN" in legal and "CALL" not in legal and "CHECK" not in legal):
        return "facing_allin"
    if "CHECK" in legal and to_call <= 0:
        return "check_gratis"
    if not board and stack_bb <= float(getattr(config, "PUSH_FOLD_MAX_BB", 10.0)):
        return "push_fold_short"
    if board:
        ranks = [tok[:-1] for tok in board.split() if len(tok) >= 2]
        if len(ranks) != len(set(ranks)):
            return "board_pair_vs_pocket"
    return "draws_proyectos"


def load_eval_dataset(
    db_path: Optional[Path] = None,
    seed: Optional[int] = None,
    max_per_category: int = 10,
) -> List[EvalCase]:
    """Carga un dataset reproducible de casos reales de BD + casos canónicos."""
    rng = random.Random(int(seed if seed is not None else getattr(config, "AI_EVAL_DEFAULT_SEED", 20261114)))
    cases_by_cat: Dict[str, List[EvalCase]] = {c: [] for c in CRITICAL_CATEGORIES}

    for c in _canonical_cases():
        cases_by_cat[c.category].append(c)

    target_db = Path(db_path) if db_path else Path(__file__).resolve().parent.parent / "logs" / "poker_stats.db"
    if target_db.exists():
        try:
            with sqlite3.connect(f"file:{target_db}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT decision_uid, street, hole_cards, board, pot_size, to_call,
                           effective_stack, big_blind, legal_actions, gto_recommended_action,
                           ev_by_action_json, position
                    FROM analytics_decisions
                    WHERE evaluation_available = 1
                    ORDER BY is_synthetic ASC, timestamp DESC
                    LIMIT 300
                    """
                ).fetchall()

            for r in rows:
                d = dict(r)
                try:
                    legal = json.loads(d.get("legal_actions") or "[]")
                except Exception:
                    legal = []
                try:
                    ev_map = json.loads(d.get("ev_by_action_json") or "{}")
                except Exception:
                    ev_map = {}
                d["legal_actions"] = legal
                cat = _classify_db_row(d)
                if len(cases_by_cat[cat]) >= max_per_category:
                    continue
                bb = max(1.0, float(d.get("big_blind") or 20.0))
                pot = float(d.get("pot_size") or 0.0)
                to_call = float(d.get("to_call") or 0.0)
                eff = float(d.get("effective_stack") or 500.0)
                dom = {
                    "es_mi_turno": True,
                    "mis_cartas": str(d.get("hole_cards") or "").split(),
                    "cartas_comunitarias": str(d.get("board") or "").split(),
                    "bote": pot,
                    "a_igualar": to_call,
                    "mi_stack": eff,
                    "stack_rival": eff,
                    "posicion": d.get("position") or "BTN",
                    "opciones_disponibles": " | ".join(legal),
                    "gto_math": {
                        "bote_bb": round(pot / bb, 2),
                        "a_igualar_bb": round(to_call / bb, 2),
                        "stack_efectivo_bb": round(eff / bb, 2),
                        "pot_odds_pct": round((to_call / (pot + to_call) * 100.0), 1) if (pot + to_call) > 0 else 0.0,
                    },
                }
                cases_by_cat[cat].append(
                    EvalCase(
                        case_id=f"db_{d['decision_uid'][:12]}",
                        category=cat,
                        dom_data=dom,
                        expected_gto_action=str(d.get("gto_recommended_action") or "CHECK").upper(),
                        ev_by_action={str(k).upper(): float(v) for k, v in ev_map.items()},
                        legal_actions=[str(x).upper() for x in legal],
                    )
                )
        except Exception:
            pass

    all_cases: List[EvalCase] = []
    for cat in CRITICAL_CATEGORIES:
        bucket = cases_by_cat[cat]
        rng.shuffle(bucket)
        all_cases.extend(bucket[:max_per_category])
    return all_cases
